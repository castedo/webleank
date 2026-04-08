from __future__ import annotations
import asyncio, errno, http, os, tomllib
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
    AsyncProgram,
    DuplexStream,
    ErrorCode,
    LspAny,
    MethodCall,
    MsgParams,
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


class LakeWorkspace:
    def __init__(self, lake_server: RpcInterface, center: WebleankCenter) -> None:
        self.lake_server = lake_server
        self._center = center
        self.doc_highlight: MsgParams | None = None

    async def on_notify_from_lake(self, mc: MethodCall) -> None:
        if mc.method == '$/lean/fileProgress':
            # TODO dehack
            for s in self._center._sidekicks:
                await s.notify_sidekick(mc)

    async def on_doc_highlight(self, params: MsgParams) -> None:
        self.doc_highlight = params
        await self._center.on_doc_highlight_changed(self)


SIDEKICK_SERVER_API_REQUEST_METHODS = {
    '$/lean/plainGoal',
    '$/lean/plainTermGoal',
}


class SidekickSession(RpcInterface):
    def __init__(self, center: WebleankCenter, channel: RpcChannel) -> None:
        self._center = center
        self._channel = channel

    async def run(self) -> None:
        await self._channel.proxy.notify(MethodCall('ack'))
        await self._channel.pump(self)

    async def close_and_wait(self) -> None:
        await self._channel.proxy.close_and_wait()

    async def notify(self, mc: MethodCall) -> None:
        pass

    async def request(
        self, mc: MethodCall, fix_id: str | None = None
    ) -> Awaitable[Response]:
        if mc.method in SIDEKICK_SERVER_API_REQUEST_METHODS:
            return await self._center.lake_request(mc, fix_id)
        else:
            return awaitable_error(ErrorCode.MethodNotFound)

    async def notify_sidekick(self, mc: MethodCall) -> None:
        await self._channel.proxy.notify(mc)

    async def notify_doc_highlight(self, doc_highlight: MsgParams) -> None:
        mc = MethodCall('documentHighlight', doc_highlight)
        await self.notify_sidekick(mc)


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
    def __init__(self, leank_client: RpcInterface, space: LakeWorkspace):
        self.client = leank_client
        self._space = space

    async def close_and_wait(self) -> None:
        await self.client.close_and_wait()

    async def notify(self, mc: MethodCall) -> None:
        if not mc.method.startswith('$/lean/'):
            await self.client.notify(mc)
        else:
            await self._space.on_notify_from_lake(mc)

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
        self.space: LakeWorkspace | None = None

    async def close_and_wait(self) -> None:
        await self._lake_server.close_and_wait()

    async def notify(self, mc: MethodCall) -> None:
        await self._lake_server.notify(mc)

    async def request(
        self, mc: MethodCall, fix_id: str | None = None
    ) -> Awaitable[Response]:
        if mc.method == "initialized":
            aw_response = await self._lake_server.request(initialize_call(mc.params))
            response = await aw_response
            return awaitable(leank_init_response(response))
        elif mc.method == "textDocument/documentHighlight":
            aw_response = await self._lake_server.request(mc)
            if mc.params is not None:
                if self.space is not None:
                    await self.space.on_doc_highlight(mc.params)
            return aw_response
        else:
            return await self._lake_server.request(mc)


class LifeSaver:
    def __init__(self) -> None:
        self.expire_event = Event()
        self._life_event = Event()
        self._life_event.set()

    def on_life_event(self) -> None:
        self._life_event.set()

    async def stay_alive(
        self, life_needed: Callable[[], bool], linger_secs: float
    ) -> None:
        # Ah, ah, ah, ah ... stayin' alive ... stayin' alive
        while await self._life_event.wait():
            self._life_event.clear()
            if not life_needed():
                log.debug(f"Staying alive for {linger_secs} seconds")
                await asyncio.sleep(linger_secs)
            if not life_needed() and not self._life_event.is_set():
                break
        self.expire_event.set()


class WebleankCenter:
    def __init__(self, life_saver: LifeSaver):
        self.life_saver = life_saver
        self._spaces: list[LakeWorkspace] = []
        self._sidekicks: list[SidekickSession] = []
        self.active: LakeWorkspace | None = None

    def has_sessions(self) -> bool:
        return bool(self._spaces) or bool(self._sidekicks)

    def open_leank_session(
        self, lake_server: RpcInterface, leank_client: RpcInterface | None
    ) -> LakeWorkspace:
        # TODO pass leank_client too
        spa = LakeWorkspace(lake_server, self)
        self._spaces.append(spa)
        return spa

    async def on_doc_highlight_changed(self, spa: LakeWorkspace) -> None:
        if spa.doc_highlight:
            self.active = spa
            for s in self._sidekicks:
                await s.notify_doc_highlight(spa.doc_highlight)

    def close_leank_session(self, sess: LakeWorkspace) -> None:
        self._spaces.remove(sess)
        self.life_saver.on_life_event()

    async def lake_request(
        self, mc: MethodCall, fix_id: str | None = None
    ) -> Awaitable[Response]:
        if self.active:
            return await self.active.lake_server.request(mc, fix_id)
        else:
            return awaitable_error(ErrorCode.InternalError)

    async def sidekick_websocket_run(self, chan: RpcChannel) -> None:
        sess = SidekickSession(self, chan)
        self._sidekicks.append(sess)
        try:
            if self.active and self.active.doc_highlight:
                await sess.notify_doc_highlight(self.active.doc_highlight)
            await sess.run()
        except Exception:
            log.exception("Sidekick session exception")
        finally:
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
                await self._center.life_saver.expire_event.wait()
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


class LeankChannel(RpcChannel):
    def __init__(self, lake_channel: RpcChannel, center: WebleankCenter):
        self._lake_channel = lake_channel
        self._center = center
        self._leank_server = LeankServer(lake_channel.proxy)

    @property
    def proxy(self) -> RpcInterface:
        return self._leank_server

    async def pump(self, leank_client: RpcInterface | None = None) -> None:
        sess = self._center.open_leank_session(self._lake_channel.proxy, leank_client)
        try:
            self._leank_server.space = sess
            impl = None if leank_client is None else LakeClient(leank_client, sess)
            await self._lake_channel.pump(impl)
        finally:
            self._center.close_leank_session(sess)


class LeankChannelFactory(RpcDirChannelFactory):
    def __init__(self, lake_factory: RpcDirChannelFactory, center: WebleankCenter):
        self._lake_factory = lake_factory
        self._center = center

    async def anew(self, work_root: Path) -> RpcChannel:
        lake_channel = await self._lake_factory.anew(work_root)
        return LeankChannel(lake_channel, self._center)

    async def await_expire(self) -> None:
        await self._center.life_saver.expire_event.wait()


class LeankSocketServer:
    def __init__(self, lake_factory: RpcDirChannelFactory, center: WebleankCenter):
        self._leank_factory = LeankChannelFactory(lake_factory, center)
        self._loop = asyncio.get_running_loop()

    async def start_serving(self, sock_path: Path) -> None:
        srvr = await asyncio.start_unix_server(
            self._on_connect, sock_path, start_serving=False
        )
        async with srvr:
            await srvr.start_serving()
            await self._leank_factory.await_expire()

    async def _on_connect(
        self, ain: asyncio.StreamReader, aout: asyncio.StreamWriter
    ) -> None:
        log.debug("socket connected")
        try:
            sock_chan = json_rpc_channel(ain, aout, name='socket', loop=self._loop)
            async with TaskGroup() as tg:
                server = channel_lsp_server(self._leank_factory, sock_chan.proxy, tg)
                tg.create_task(sock_chan.pump(server))
        except Exception:
            log.exception("Leank socket LSP server session exception")
        finally:
            log.debug("socket disconnected")


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
                data = tomllib.load(file)
            domains = data.get('allowed', {}).get('domains', [])
        self.allowed_domains = AllowedDomains(domains)
        self.lake_cmd = data.get('lake', {}).get('cmd', ['lake', 'serve'])


async def socket_available(sock_path: Path) -> bool:
    for i in range(8):
        try:
            await asyncio.open_unix_connection(sock_path)
            return True
        except (FileNotFoundError, ConnectionRefusedError):
            await asyncio.sleep(0.125)
    return False


LINGER_SECONDS = float(os.environ.get("WEBLEANK_LINGER_SECONDS", 5))


class ServiceProgram(AsyncProgram):
    def __init__(self, web_port: int, sock_path: Path):
        self._web_port = web_port
        self._sock_path = sock_path
        self._stdin_eof_event = Event()

    async def amain(
        self, stdio: DuplexStream, *, loop: asyncio.AbstractEventLoop
    ) -> int:
        try:
            started = await self.run_service(linger_secs=LINGER_SECONDS, loop=loop)
            if not started:
                other_proc_got_it = await socket_available(self._sock_path)
                if not other_proc_got_it:
                    log.error(f"Unable to start server for {self._sock_path}")
                return 0 if other_proc_got_it else 1
        except Exception as ex:
            log.exception(ex)
            return 1
        return 0

    def on_stdin_eof(self) -> None:
        self._stdin_eof_event.set()

    async def run_service(
        self, linger_secs: float, *, loop: asyncio.AbstractEventLoop
    ) -> bool:
        config = Config()
        lake_factory = RpcSubprocessFactory(config.lake_cmd, loop=loop)
        life_saver = LifeSaver()
        center = WebleankCenter(life_saver)
        socket_server = LeankSocketServer(lake_factory, center)
        web_port_server = LeankWebServer(center, config.allowed_domains)
        this_process_got_it = await web_port_server.bind_port(self._web_port)
        if not this_process_got_it:
            # assume another process is running as service
            return False
        try:
            async with TaskGroup() as tg:
                tg.create_task(web_port_server.start_serving())
                tg.create_task(socket_server.start_serving(self._sock_path))
                if self._stdin_eof_event.is_set():
                    # stdin is already EOF, so run in daemon-like mode
                    # linger alive for seconds when there are no connection
                    tg.create_task(
                        life_saver.stay_alive(center.has_sessions, linger_secs)
                    )
                else:
                    # use stdin EOF as expiration instead of lingering
                    self._stdin_eof_event = life_saver.expire_event
                    tip = "Press CTRL-D (EOF) to stop waiting for new connections ..."
                    print(tip, flush=True)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(self._sock_path)
                log.debug(f"Deleted socket {self._sock_path}")
        return True
