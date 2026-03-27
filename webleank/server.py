from __future__ import annotations
import asyncio, errno, http, logging, os, tomli
from asyncio import TaskGroup
from collections.abc import Sequence
from contextlib import suppress
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_config_path
import websockets.asyncio.server
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from lspleanklib import (
    DuplexStream,
    JsonRpcChannel,
    JsonRpcMsgStream,
    LeankLakeFactory,
    MethodCall,
    RpcChannel,
    RpcDirChannelFactory,
    RpcSubprocessFactory,
    channel_lsp_server,
)

from .jsonrpc import WebSocketJsonRpcMsgConnection


log = logging.getLogger(__spec__.parent)


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
    def __init__(self, web_port:int, domains: AllowedDomains, tg: TaskGroup) -> None:
        self._web_port = web_port
        self._tg = tg
        self._webapp_files = load_webapp_files()
        self._domains = domains
        self._loop = asyncio.get_event_loop()

    async def start(self) -> bool:
        try:
            web_port_server = await websockets.asyncio.server.serve(
                self._on_connect,
                'localhost',
                self._web_port,
                process_request=self._webapp_http_server,
                start_serving=False,
            )
        except OSError as ex:
            if ex.errno != errno.EADDRINUSE:
                raise ex
            return False
        self._tg.create_task(web_port_server.start_serving())
        return True

    async def _on_connect(self, websocket: ServerConnection) -> None:
        if websocket.request is None:
            return
        if websocket.request.path != '/ws/sidekick':
            return
        origin = websocket.request.headers.get("origin")
        if origin is None:
            log.error("Websocket connection missing origin HTTP header")
            return
        hostname = urlsplit(origin).hostname
        conn = WebSocketJsonRpcMsgConnection(websocket, 'sidekick')
        ws_chan = JsonRpcChannel(conn, self._loop)
        if self._domains.is_allowed(hostname):
            await ws_chan.proxy.notify(MethodCall('ack', hostname))
        else:
            log.error("Websocket connection origin domain not allowed")
            await ws_chan.proxy.notify(MethodCall('forbidden', hostname))

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


LINGER_SECONDS = 5


class LeankSocketServer:
    def __init__(self, factory: RpcDirChannelFactory, tg: TaskGroup):
        self._factory = factory
        self._socket_tasks = tg
        self._loop = asyncio.get_running_loop()
        self._staying_alive()

    def on_connect(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        log.debug("socket connected")
        aio = DuplexStream(reader, writer)
        sock_chan = JsonRpcChannel(JsonRpcMsgStream(aio,'socket'), self._loop)
        self._socket_tasks.create_task(self._async_on_connect(sock_chan))

    async def _async_on_connect(self, sock_chan: RpcChannel) -> None:
        try:
            async with TaskGroup() as connection_tasks:
                server = channel_lsp_server(
                    self._factory, sock_chan.proxy, connection_tasks
                )
                connection_tasks.create_task(sock_chan.pump(server))
        except Exception as ex:
            log.exception(ex)
        finally:
            log.debug("socket finished")
            self._staying_alive()

    def _staying_alive(self) -> None:
        # Ah, ah, ah, ah ... stayin' alive ... stayin' alive
        log.debug(f"staying alive {LINGER_SECONDS} seconds")
        self._socket_tasks.create_task(asyncio.sleep(LINGER_SECONDS))


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
    lake_factory = RpcSubprocessFactory(lake_cmd, loop)
    leank_factory = LeankLakeFactory(lake_factory)
    async with TaskGroup() as web_tasks:
        web_server = LeankWebServer(web_port, config.allowed_domains, web_tasks)
        this_process_got_it = await web_server.start()
        if not this_process_got_it:
            # assume another process is running as service
            return False
        try:
            async with TaskGroup() as socket_tasks:
                socker = LeankSocketServer(leank_factory, socket_tasks)
                await asyncio.start_unix_server(socker.on_connect, sock_path)
                log.info(f"listening on socket {sock_path}")
                # will exit with-context when TaskGroup tasks complete
        finally:
            with suppress(FileNotFoundError):
                os.unlink(sock_path)
    return True
