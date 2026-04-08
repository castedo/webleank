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
from webleank.service import ServiceProgram

from .util import MockClient, initialize_call


TESTS_DIR = Path(__file__).parent
CASES_DIR = TESTS_DIR / "cases"


async def run_mock_daemon(web_port: int, sock_path: Path, linger_secs: float) -> bool:
    loop = asyncio.get_running_loop()
    prog = ServiceProgram(web_port, sock_path)
    prog.on_stdin_eof()
    return await prog.run_service(linger_secs, loop=loop)


async def test_daemon_service(tmp_path):
    ta = asyncio.create_task(
        run_mock_daemon(0, tmp_path / "test.sock", linger_secs=0.03)
    )
    await asyncio.sleep(0.02)
    assert not ta.done()
    await asyncio.sleep(0.03)
    assert ta.done()
    assert ta.result() == True


async def test_stdin_service_stay(tmp_path):
    loop = asyncio.get_running_loop()
    prog = ServiceProgram(0, tmp_path / "test.sock")
    ta = asyncio.create_task(prog.run_service(0.01, loop=loop))
    await asyncio.sleep(0.04)
    assert not ta.done()
    prog.on_stdin_eof()
    await asyncio.sleep(0.01)
    assert ta.done()
    assert ta.result() == True


async def test_stdin_service_no_linger(tmp_path):
    loop = asyncio.get_running_loop()
    prog = ServiceProgram(0, tmp_path / "test.sock")
    ta = asyncio.create_task(prog.run_service(4, loop=loop))
    await asyncio.sleep(0.01)
    assert not ta.done()
    prog.on_stdin_eof()
    await asyncio.sleep(0.01)
    assert ta.done()
    assert ta.result() == True


@contextlib.asynccontextmanager
async def webleank_service():
    loop = asyncio.get_running_loop()
    web_port = 51342
    with tempfile.TemporaryDirectory() as tmp_dir:
        sock_path = Path(tmp_dir) / "test.sock"
        prog = ServiceProgram(web_port, sock_path)
        service_task = asyncio.create_task(prog.run_service(1000, loop=loop))
        await asyncio.sleep(0.01)

        yield (web_port, sock_path)

        prog.on_stdin_eof()
        await service_task
        assert service_task.result() == True


@contextlib.asynccontextmanager
async def leank_session_init(sock_path: Path, client: RpcInterface, work_root: Path):
    loop = asyncio.get_running_loop()
    reader, writer = await asyncio.open_unix_connection(sock_path)
    chan = json_rpc_channel(reader, writer, name='socket', loop=loop)
    asyncio.create_task(chan.pump(client))
    rpc = chan.proxy

    aw_resp = await rpc.request(initialize_call(work_root))
    server_init = await aw_resp
    assert isinstance(server_init.result, dict)
    assert server_init.result['serverInfo']['name'] == "Lean 4 Server"
    await rpc.notify(MC('initialized', {}))

    yield rpc

    aw_resp = await rpc.request(MC('shutdown'))
    assert await aw_resp == Response(None)
    await rpc.notify(MC('exit'))
    await rpc.close_and_wait()


@contextlib.asynccontextmanager
async def server_session_init(client: RpcInterface, work_root: Path):
    async with webleank_service() as (web_port, sock_path):
        async with leank_session_init(sock_path, client, work_root) as rpc:
            yield (web_port, rpc)


async def test_socket_connect():
    async with server_session_init(MockClient(), TESTS_DIR):
        pass


@contextlib.asynccontextmanager
async def sidekick_session_init(sidekick: MockClient, web_port: int):
    uri = f"ws://localhost:{web_port}/ws/sidekick"
    origin = websockets.Origin("https://foo.castedo.com")
    async with websockets.connect(uri, origin=origin) as ws:
        assert ws.state == websockets.protocol.OPEN
        side_chan = websocket_rpc_channel(ws, 'sidekick')
        side_task = asyncio.create_task(side_chan.pump(sidekick))
        fu_ack = sidekick.future_notif('ack')

        yield

        assert await fu_ack == None
    assert side_task.exception() is None


async def assert_doc_highlight(sidekick: MockClient, rpc: RpcInterface) -> None:
    fu_high = sidekick.future_notif('documentHighlight')

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

    params: MsgParams = {
        "textDocument": {"uri": doc_path.as_uri()},
        "position": {"character": 6, "line": 1},
    }
    aw_resp = await rpc.request(MC("textDocument/documentHighlight", params))
    response = await aw_resp
    assert response == Response([])

    # textDocument/documentHighlight params sent to Lake LSP should echo to sidekick
    assert await fu_high == params


@pytest.mark.slow
@pytest.mark.parametrize("work_root", [
    CASES_DIR / "min_import",
    CASES_DIR
])
async def test_doc_highlight(work_root):
    leank_client = MockClient()
    async with server_session_init(leank_client, work_root) as (web_port, rpc):
        sidekick = MockClient()
        async with sidekick_session_init(sidekick, web_port):
            await assert_doc_highlight(sidekick, rpc)
