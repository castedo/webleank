import pytest

import asyncio
from pathlib import Path

from lspleanklib import (
    MethodCall as MC,
    Response,
    json_rpc_channel,
)

from webleank.server import run_service

from .util import MockClient, initialize_call


TESTS_DIR = Path(__file__).parent


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


@pytest.mark.slow
async def test_socket_connect(tmp_path):
    loop = asyncio.get_running_loop()
    sock_path = tmp_path / "test.sock"
    service_task = asyncio.create_task(run_service(0, sock_path, linger_secs=0.1))
    await asyncio.sleep(0.1)

    reader, writer = await asyncio.open_unix_connection(sock_path)
    chan = json_rpc_channel(reader, writer, name='socket', loop=loop)
    asyncio.create_task(chan.pump(MockClient()))
    rpc = chan.proxy

    rootPath = TESTS_DIR
    aw_resp = await rpc.request(initialize_call(rootPath))
    server_init = await aw_resp
    assert server_init.result['serverInfo']['name'] == "Lean 4 Server"
    await rpc.notify(MC('initialized', {}))

    aw_resp = await rpc.request(MC('shutdown'))
    assert await aw_resp == Response(None)
    await rpc.notify(MC('exit'))
    await rpc.close_and_wait()

    await service_task
    assert service_task.result() == True
