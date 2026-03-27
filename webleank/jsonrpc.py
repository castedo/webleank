"""
JSON-RPC for WebSockets
"""

import json, logging

import websockets.exceptions
from websockets.asyncio.connection import Connection

from lspleanklib import JsonRpcMsg, JsonRpcMsgConnection


log = logging.getLogger(__spec__.parent)


class WebSocketJsonRpcMsgConnection(JsonRpcMsgConnection):
    def __init__(self, conn: Connection, name: str):
        self._conn = conn
        self._name = name

    async def close(self) -> None:
        await self._conn.close()

    async def write(self, msg: JsonRpcMsg) -> None:
        text = json.dumps(msg.to_lsp_obj(), separators=(',', ':'))
        try:
            await self._conn.send(text)
        except websockets.exceptions.ConnectionClosed as ex:
            raise RuntimeError(f"WebSocket {self.name} connection closed") from ex

    async def read(self) -> JsonRpcMsg | None:
        while True:
            try:
                data = await self._conn.recv()
            except websockets.exceptions.ConnectionClosedOK:
                return None
            except websockets.exceptions.ConnectionClosedError as ex:
                raise RuntimeError("WebSocket closed") from ex
            try:
                return JsonRpcMsg.from_jsonrpc(json.loads(data))
            except ValueError:
                err = "Bad JSON-RPC messsage on WebSocket '{}'"
                log.exception(err.format(self.name))

    @property
    def name(self) -> str:
        return self._name
