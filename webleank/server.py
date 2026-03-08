from websockets.asyncio.server import ServerConnection, serve


class Server:
    def __init__(self) -> None:
        self._cnt = 0

    async def ack(self, websocket: ServerConnection) -> None:
        self._cnt += 1
        if websocket.request:
            origin = websocket.request.headers.get("origin")
            if origin is not None:
                await websocket.send(origin)
        if origin is None:
            await websocket.send("nope")
        else:
            await websocket.send(str(self._cnt))

    async def run(self) -> None:
        async with serve(self.ack, 'localhost', 1342) as server:
            await server.serve_forever()
