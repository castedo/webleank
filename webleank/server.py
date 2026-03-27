from __future__ import annotations
import asyncio, errno, http, logging, os
from asyncio import TaskGroup
from contextlib import suppress
from importlib import resources
from pathlib import Path

import websockets.asyncio.server 
from websockets.asyncio.server import ServerConnection
from websockets.datastructures import Headers
from websockets.http11 import Request, Response

from lspleanklib import (
    DuplexStream,
    JsonRpcChannel,
    LeankLakeFactory,
    RpcChannel,
    RpcDirChannelFactory,
    RpcSubprocessFactory,
    channel_lsp_server,
)


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


class LeankWebServer:
    def __init__(self) -> None:
        self._web_port: int
        self._web_port_server: websockets.asyncio.server.Server
        self._cnt = 0
        self._webapp_files = load_webapp_files()

    @staticmethod
    async def bind_web_port(web_port: int) -> LeankWebServer | None:
        self = LeankWebServer()
        try:
            self._web_port = web_port
            self._web_port_server = await websockets.asyncio.server.serve(
                self.ack,
                'localhost',
                self._web_port,
                process_request=self.webapp_http_server,
                start_serving=False,
            )
            return self
        except OSError as ex:
            if ex.errno != errno.EADDRINUSE:
                raise ex
        return None

    async def start_serving(self) -> None:
        await self._web_port_server.start_serving()

    async def ack(self, websocket: ServerConnection) -> None:
        self._cnt += 1
        if websocket.request:
            origin = websocket.request.headers.get("origin")
            if origin is not None:
                await websocket.send(origin)
        if origin is None:
            await websocket.send("no origin, no service")
        else:
            await websocket.send(str(self._cnt))

    def webapp_http_server(
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
        sock_chan = JsonRpcChannel(aio, self._loop, 'socket')
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


async def run_server(web_port: int, sock_path: Path) -> bool:
    lake_cmd = ['lake', 'serve']
    loop = asyncio.get_running_loop()
    web_server = await LeankWebServer.bind_web_port(web_port)
    if web_server is None:
        return False
    try:
        async with TaskGroup() as tg:
            tg.create_task(web_server.start_serving())
            lake_factory = RpcSubprocessFactory(lake_cmd, loop)
            leank_factory = LeankLakeFactory(lake_factory)
            socker = LeankSocketServer(leank_factory, tg)
            await asyncio.start_unix_server(socker.on_connect, sock_path)
            log.info(f"listening on socket {sock_path}")
            # will exit when TaskGroup tasks complete
    finally:
        with suppress(FileNotFoundError):
            os.unlink(sock_path)
    return True
