from __future__ import annotations
import asyncio, errno, http, logging, os, tomli
from asyncio import TaskGroup
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager, suppress
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_config_path
import websockets.asyncio.server
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from lspleanklib import (
    LeankLakeFactory,
    MethodCall,
    RpcChannel,
    RpcDirChannelFactory,
    RpcSubprocessFactory,
    channel_lsp_server,
    json_rpc_channel,
)

from .jsonrpc import websocket_rpc_channel


log = logging.getLogger(__spec__.parent)


LINGER_SECONDS = 5


class WebleankCenter:
    def __init__(self, factory: RpcDirChannelFactory):
        self._factory = factory

    async def linger(self) -> None:
        log.debug(f"lingering {LINGER_SECONDS} seconds")
        await asyncio.sleep(LINGER_SECONDS)

    async def leank_socket_run(self, chan: RpcChannel) -> None:
        try:
            async with TaskGroup() as connection_tasks:
                server = channel_lsp_server(
                    self._factory, chan.proxy, connection_tasks
                )
                connection_tasks.create_task(chan.pump(server))
        except Exception as ex:
            log.exception(ex)
        finally:
            log.debug("socket finished")
            await self.linger()

    async def sidekick_websocket_run(self, chan: RpcChannel) -> None:
        await chan.proxy.notify(MethodCall('ack'))
        await chan.pump()
        await self.linger()

    async def control_websocket_run(self, chan: RpcChannel) -> None:
        await chan.pump()
        await self.linger()


MIME_TYPES = {'.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript'}


def make_file_response(path: str | Path, content: bytes) -> Response:
    mime_type = MIME_TYPES.get(Path(path).suffix)
    headers = Headers()
    headers['Content-Length'] = str(len(content))
    if mime_type is not None:
        headers['Content-Type'] = mime_type
    return Response(http.HTTPStatus.OK, 'OK', headers, content)


def load_webapp_files() -> dict[str, bytes]:
    ret: dict[str, bytes] = {}
    rp = resources.files(__package__).joinpath('webapp')
    ret['/index.html'] = rp.joinpath('index.html').read_bytes()
    for a in rp.joinpath("assets").iterdir():
        ret[f"/assets/{a.name}"] = a.read_bytes()
    return ret


class AllowedDomains:
    def __init__(self, domains: Sequence[str]):
        self._domains = [d.split('.') for d in domains]

    def is_allowed(self, origin: str | None) -> bool:
        if origin:
            parts = origin.split('.')
            for allowed in self._domains:
                if parts[-len(allowed):] == allowed:
                    return True
        return False


class LeankWebServer:
    def __init__(self, center: WebleankCenter, domains: AllowedDomains) -> None:
        self._center = center
        self._webapp_files = load_webapp_files()
        self._domains = domains
        self._loop = asyncio.get_event_loop()
        self._server: websockets.asyncio.server.Server | None = None

    async def bind_port(self, web_port: int) -> bool:
        try:
            self._server = await websockets.asyncio.server.serve(
                self._on_connect,
                'localhost',
                web_port,
                process_request=self._webapp_http_server,
                start_serving=False,
            )
        except OSError as ex:
            if ex.errno != errno.EADDRINUSE:
                raise ex
            return False
        return True

    async def start_serving(self) -> None:
        assert self._server
        await self._server.start_serving()

    async def _on_connect(self, websocket: ServerConnection) -> None:
        if websocket.request is None:
            return
        origin = websocket.request.headers.get("origin")
        if origin is None:
            log.error("Websocket connection missing origin HTTP header")
            return
        hostname = urlsplit(origin).hostname
        ws_chan = websocket_rpc_channel(websocket, 'sidekick')
        if not self._domains.is_allowed(hostname):
            log.error("Websocket connection origin domain not allowed")
            await ws_chan.proxy.notify(MethodCall('forbidden', hostname))
        else:
            match websocket.request.path:
                case '/ws/sidekick':
                    await self._center.sidekick_websocket_run(ws_chan)
                case '/ws/control':
                    await self._center.control_websocket_run(ws_chan)


    def _webapp_http_server(
        self, connection: ServerConnection, request: Request
    ) -> Response | None:
        path = request.path
        if path == '/':
            path = '/index.html'
        if path == '/index.html' or path.startswith('/assets/'):
            if payload := self._webapp_files.get(path):
                return make_file_response(path, payload)
        if not path.startswith('/ws/'):
            return connection.respond(http.HTTPStatus.NOT_FOUND, "NOT FOUND")
        return None


class LeankSocketServer:
    def __init__(self, center: WebleankCenter):
        self._center = center
        self._socket_tasks: TaskGroup | None = None
        self._loop = asyncio.get_running_loop()

    @asynccontextmanager
    async def start_serving(self, sock_path: Path) -> AsyncIterator[None]:
        async with TaskGroup() as self._socket_tasks:
            await asyncio.start_unix_server(self._on_connect, sock_path)
            yield

    def _on_connect(
        self, ain: asyncio.StreamReader, aout: asyncio.StreamWriter
    ) -> None:
        log.debug("socket connected")
        assert self._socket_tasks
        sock_chan = json_rpc_channel(ain, aout, name='socket', loop=self._loop)
        self._socket_tasks.create_task(self._center.leank_socket_run(sock_chan))


CONFIG_FILENAME = 'webleank.toml'


def get_config_path() -> Path | None:
    lean_config_dir = user_config_path('lean')
    config_path = lean_config_dir / CONFIG_FILENAME
    if not lean_config_dir.exists():
        try:
            default = resources.files(__package__).joinpath('config', CONFIG_FILENAME)
            os.makedirs(lean_config_dir)
            with open(config_path , 'wb') as file:
                file.write(default.read_bytes())
        except FileExistsError:
            pass
    return config_path if config_path.exists() else None


class Config:
    def __init__(self) -> None:
        data = {}
        path = get_config_path()
        if path is None:
            domains = []
        else:
            with open(path, 'rb') as file:
                data = tomli.load(file)
            domains = data.get('allowed', {}).get('domains', [])
        self.allowed_domains = AllowedDomains(domains)


async def run_service(web_port: int, sock_path: Path) -> bool:
    config = Config()
    lake_cmd = ['lake', 'serve']
    loop = asyncio.get_running_loop()
    lake_factory = RpcSubprocessFactory(lake_cmd, loop=loop)
    leank_factory = LeankLakeFactory(lake_factory)
    center = WebleankCenter(leank_factory)
    socket_server = LeankSocketServer(center)
    web_port_server = LeankWebServer(center, config.allowed_domains)
    this_process_got_it = await web_port_server.bind_port(web_port)
    if not this_process_got_it:
        # assume another process is running as service
        return False
    async with TaskGroup() as web_port_tasks:
        web_port_tasks.create_task(web_port_server.start_serving())
        try:
            async with socket_server.start_serving(sock_path):
                log.info(f"listening on socket {sock_path}")
                await center.linger()
        finally:
            with suppress(FileNotFoundError):
                os.unlink(sock_path)
                log.debug(f"Deleted socket {sock_path}")
    return True
