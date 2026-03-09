import http
from importlib import resources
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.http11 import Request, Response


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


class Server:
    def __init__(self) -> None:
        self._cnt = 0
        self._webapp_files = load_webapp_files()

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

    async def run(self, port: int = 1342) -> None:
        async with serve(
            self.ack, 'localhost', port, process_request=self.webapp_http_server
        ) as server:
            await server.serve_forever()
