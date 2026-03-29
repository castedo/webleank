"""
JSON-RPC for WebSockets
"""

import asyncio, json, logging

import websockets.exceptions
from websockets.asyncio.connection import Connection

from lspleanklib import (
    JsonRpcMsg,
    RpcChannel,
    RpcMsgChannel,
    RpcMsgConnection,
)


log = logging.getLogger(__spec__.parent)


class WebSocketRpcMsgConnection(RpcMsgConnection):
    def __init__(self, conn: Connection):
        self._conn = conn

    async def close_and_wait(self) -> None:
        await self._conn.close()

    async def write(self, msg: JsonRpcMsg) -> None:
        text = json.dumps(msg.to_lsp_obj(), separators=(',', ':'))
        try:
            await self._conn.send(text)
        except websockets.exceptions.ConnectionClosed as ex:
            raise RuntimeError("WebSocket connection closed") from ex

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
                log.exception("Bad JSON-RPC messsage on WebSocket")


def websocket_rpc_channel(websocket: Connection, name: str) -> RpcChannel:
    rpc_conn = WebSocketRpcMsgConnection(websocket)
    return RpcMsgChannel(rpc_conn, name='websocket', loop=asyncio.get_running_loop())
