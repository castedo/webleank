from __future__ import annotations
import asyncio, errno, http, os, tomli
from asyncio import Event, TaskGroup
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from importlib import resources
from pathlib import Path
from urllib.parse import urlsplit

from platformdirs import user_config_path
import websockets.asyncio.server
from websockets import http11
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers

from lspleanklib import (
    ErrorCode,
    LspAny,
    MethodCall,
    Response,
    RpcChannel,
    RpcDirChannelFactory,
    RpcInterface,
    RpcSubprocessFactory,
    awaitable_error,
    channel_lsp_server,
    json_rpc_channel,
)

from .jsonrpc import websocket_rpc_channel
from .util import awaitable, get_obj, get_str, log, version


LSP_CLIENT_NAME = "webleank"
LSP_SERVER_NAME = "webleank"


def leank_init_response(lake_init_response: Response) -> Response:
    if lake_init_response.error is not None:
        return lake_init_response
    return Response(
        {
            # TODO check and standardize server caps
            'capabilities': get_obj(lake_init_response.result, 'capabilities'),
            'serverInfo': {'name': LSP_SERVER_NAME, 'version': version()},
        }
    )


class LakeClient(RpcInterface):
    def __init__(self, leank_client: RpcInterface):
        self.client = leank_client

    async def close_and_wait(self) -> None:
        await self.client.close_and_wait()

    async def notify(self, mc: MethodCall) -> None:
        await self.client.notify(mc)

    async def request(
        self, mc: MethodCall, fix_id: str | None = None
    ) -> Awaitable[Response]:
        if mc.method == "client/registerCapability":
            return awaitable_error(ErrorCode.MethodNotFound)
        return await self.client.request(mc, fix_id)


def initialize_call(leank_params: LspAny) -> MethodCall:
    return MethodCall(
        'initialize',
        {
            'capabilities': get_obj(leank_params, 'capabilities'),
            'clientInfo': {'name': LSP_CLIENT_NAME, 'version': version()},
            'processId': os.getpid(),
            'rootUri': get_str(leank_params, 'rootUri'),
        },
    )


class LeankServer(RpcInterface):
    def __init__(self, lake_server: RpcInterface):
        self._lake_server = lake_server

    async def close_and_wait(self) -> None:
        await self._lake_server.close_and_wait()

    async def notify(self, mc: MethodCall) -> None:
        await self._lake_server.notify(mc)

    async def request(
        self, mc: MethodCall, fix_id: str | None = None
    ) -> Awaitable[Response]:
        if mc.method != "initialized":
            return await self._lake_server.request(mc)
        else:
            aw_response = await self._lake_server.request(initialize_call(mc.params))
            response = await aw_response
            return awaitable(leank_init_response(response))


class LeankSession:
    async def run(self, chan: RpcChannel, lake_factory: RpcDirChannelFactory) -> None:
        async with TaskGroup() as session_tasks:
            leank_client = chan.proxy
            lake_client = LakeClient(leank_client)
            lake_server = channel_lsp_server(lake_factory, lake_client, session_tasks)
            leank_server = LeankServer(lake_server)
            session_tasks.create_task(chan.pump(leank_server))


class SidekickSession:
    async def run(self, chan: RpcChannel) -> None:
        await chan.proxy.notify(MethodCall('ack'))
        await chan.pump()


LINGER_SECONDS = 5


class LifeSaver:
    def __init__(self, life_needed: Callable[[], bool]) -> None:
        self._life_needed = life_needed
        self._expire_event = Event()
        self._life_event = Event()
        self._life_event.set()

    async def wait_expired(self) -> None:
        await self._expire_event.wait()

    def on_life_event(self) -> None:
        self._life_event.set()

    async def stay_alive(self) -> None:
        # Ah, ah, ah, ah ... stayin' alive ... stayin' alive
        while await self._life_event.wait():
            self._life_event.clear()
            if not self._life_needed():
                log.debug(f"Staying alive for {LINGER_SECONDS} seconds")
                await asyncio.sleep(LINGER_SECONDS)
            if not self._life_needed() and not self._life_event.is_set():
                break
        self._expire_event.set()


class WebleankCenter:
    def __init__(self, lake_factory: RpcDirChannelFactory):
        self._lake_factory = lake_factory
        self._leanks: list[LeankSession] = []
        self._sidekicks: list[SidekickSession] = []
        self.life_saver = LifeSaver(self.has_sessions)

    def has_sessions(self) -> bool:
        return bool(self._leanks) or bool(self._sidekicks)

    async def leank_socket_run(self, chan: RpcChannel) -> None:
        sess = LeankSession()
        self._leanks.append(sess)
        try:
            await sess.run(chan, self._lake_factory)
        except Exception:
            log.exception("Leank socket LSP server session exception")
        finally:
            log.debug("leank socket finished")
            self._leanks.remove(sess)
            self.life_saver.on_life_event()

    async def sidekick_websocket_run(self, chan: RpcChannel) -> None:
        sess = SidekickSession()
        self._sidekicks.append(sess)
        try:
            await sess.run(chan)
        except Exception:
            log.exception("Sidekick session exception")
        finally:
            log.debug("sidekick websocket finished")
            self._sidekicks.remove(sess)
            self.life_saver.on_life_event()

    async def control_websocket_run(self, chan: RpcChannel) -> None:
        # TODO something diff than sidekick
        await self.sidekick_websocket_run(chan)


MIME_TYPES = {'.css': 'text/css', '.html': 'text/html', '.js': 'text/javascript'}


def make_file_response(path: str | Path, content: bytes) -> http11.Response:
    mime_type = MIME_TYPES.get(Path(path).suffix)
    headers = Headers()
    headers['Content-Length'] = str(len(content))
    if mime_type is not None:
        headers['Content-Type'] = mime_type
    return http11.Response(http.HTTPStatus.OK, 'OK', headers, content)


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
                if parts[-len(allowed) :] == allowed:
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
        if self._server is not None:
            async with self._server:
                await self._server.start_serving()
                await self._center.life_saver.wait_expired()
            self._server = None

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
        self, connection: ServerConnection, request: http11.Request
    ) -> http11.Response | None:
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
        self._loop = asyncio.get_running_loop()

    async def start_serving(self, sock_path: Path) -> None:
        srvr = await asyncio.start_unix_server(
            self._on_connect, sock_path, start_serving=False
        )
        async with srvr:
            await srvr.start_serving()
            await self._center.life_saver.wait_expired()

    async def _on_connect(
        self, ain: asyncio.StreamReader, aout: asyncio.StreamWriter
    ) -> None:
        log.debug("socket connected")
        sock_chan = json_rpc_channel(ain, aout, name='socket', loop=self._loop)
        await self._center.leank_socket_run(sock_chan)


CONFIG_FILENAME = 'webleank.toml'


def get_config_path() -> Path | None:
    lean_config_dir = user_config_path('lean')
    config_path = lean_config_dir / CONFIG_FILENAME
    if not lean_config_dir.exists():
        try:
            default = resources.files(__package__).joinpath('config', CONFIG_FILENAME)
            os.makedirs(lean_config_dir)
            with open(config_path, 'wb') as file:
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
    center = WebleankCenter(lake_factory)
    socket_server = LeankSocketServer(center)
    web_port_server = LeankWebServer(center, config.allowed_domains)
    this_process_got_it = await web_port_server.bind_port(web_port)
    if not this_process_got_it:
        # assume another process is running as service
        return False
    try:
        async with TaskGroup() as tg:
            tg.create_task(web_port_server.start_serving())
            tg.create_task(socket_server.start_serving(sock_path))
            tg.create_task(center.life_saver.stay_alive())
    finally:
        with suppress(FileNotFoundError):
            os.unlink(sock_path)
            log.debug(f"Deleted socket {sock_path}")
    return True
