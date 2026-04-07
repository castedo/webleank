import pytest

import asyncio, contextlib, tempfile
from pathlib import Path

import websockets

from lspleanklib import (
    MethodCall as MC,
    MsgParams,
    Response,
    RpcInterface,
    json_rpc_channel,
)
from webleank.jsonrpc import websocket_rpc_channel
from webleank.server import run_service

from .util import MockClient, initialize_call


TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"


@pytest.mark.slow
async def test_service(tmp_path):
    ta = asyncio.create_task(
        run_service(0, tmp_path / "test.sock", linger_secs=0.2)
    )
    await asyncio.sleep(0.1)
    assert not ta.done()
    await asyncio.sleep(0.2)
    assert ta.done()
    assert ta.result() == True


@contextlib.asynccontextmanager
async def server_session_init(client: RpcInterface, root: Path, web_port: int):
  with tempfile.TemporaryDirectory() as tmp_dir:
    loop = asyncio.get_running_loop()
    sock_path = Path(tmp_dir) / "test.sock"
    service_task = asyncio.create_task(run_service(web_port, sock_path, linger_secs=0.1))
    await asyncio.sleep(0.05)

    reader, writer = await asyncio.open_unix_connection(sock_path)
    chan = json_rpc_channel(reader, writer, name='socket', loop=loop)
    asyncio.create_task(chan.pump(MockClient()))
    rpc = chan.proxy

    aw_resp = await rpc.request(initialize_call(root))
    server_init = await aw_resp
    assert isinstance(server_init.result, dict)
    assert server_init.result['serverInfo']['name'] == "Lean 4 Server"
    await rpc.notify(MC('initialized', {}))

    yield rpc

    aw_resp = await rpc.request(MC('shutdown'))
    assert await aw_resp == Response(None)
    await rpc.notify(MC('exit'))
    await rpc.close_and_wait()

    await service_task
    assert service_task.result() == True


@pytest.mark.slow
async def test_socket_connect():
    async with server_session_init(MockClient(), TESTS_DIR, 41342):
        await asyncio.sleep(0.1)


@pytest.mark.slow
async def test_sidekick_connect():
    web_port = 41342
    leank_client = MockClient()
    root_dir = CASES_DIR / "min_import"
    async with server_session_init(leank_client, root_dir, web_port) as rpc:
        await asyncio.sleep(0.1)

        sidekick = MockClient()
        uri = f"ws://localhost:{web_port}/ws/sidekick"
        origin = "https://foo.castedo.com"
        async with websockets.connect(uri, origin=origin) as ws:
            assert ws.state == websockets.protocol.OPEN
            side_chan = websocket_rpc_channel(ws, 'sidekick')
            side_task = asyncio.create_task(side_chan.pump(sidekick))

            fu_ack = sidekick.future_notif('ack')
            fu_high = sidekick.future_notif('documentHighlight')

            assert await fu_ack == None

            doc_path = CASES_DIR / "min_import" / "Main.lean"
            await rpc.notify(
                MC('textDocument/didOpen', {
                    'textDocument': {
                        'uri': doc_path.as_uri(),
                        'version': 1,
                        'languageId': 'lean',
                        'text': doc_path.read_text(),
                    }
                })
            )


            params = {
                "textDocument": {"uri": doc_path.as_uri()},
                "position": {"character": 7, "line": 1},
            }
            aw_resp = await rpc.request(MC("textDocument/documentHighlight", params))
            response = await aw_resp
            assert response == Response([])

            # textDocument/documentHighlight params sent to Lake LSP should echo to sidekick
            assert await fu_high == params

        assert side_task.exception() is None
