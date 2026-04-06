import asyncio
from pathlib import Path
from typing import Awaitable

from lspleanklib.jsonrpc import (
    RpcInterface,
    MethodCall,
    Response,
)
from lspleanklib.util import LspAny


def initialize_call(rootPath: Path) -> MethodCall:
    rootUri = rootPath.as_uri()
    return MethodCall("initialize", {
      "workspaceFolders": [{"uri": rootUri, "name": rootPath.name}],
      "clientInfo": {"name": "mock test client"},
    })


class MockClient(RpcInterface):
    def __init__(self) -> None:
        self.notifs: dict[str, asyncio.Future[LspAny]] = {}

    def future_notif(self, method: str) -> asyncio.Future[LspAny]:
        f = self.notifs.get(method)
        if f is None:
            f = asyncio.get_running_loop().create_future()
            self.notifs[method] = f
        return f

    async def notify(self, mc: MethodCall) -> None:
        f = self.notifs.pop(mc.method, None)
        if f is not None:
            f.set_result(mc.params)

    async def request(self, mc: MethodCall, fix_id: str | None = None) -> Awaitable[Response]:
        async def trivial() -> Response:
            return Response(None)
        return trivial()
