import pytest

import asyncio

from webleank.server import run_service


@pytest.mark.slow
async def test_service(tmp_path):
    ta = asyncio.create_task(
        run_service(1342, tmp_path / "test.sock", linger_secs=0.2)
    )
    await asyncio.sleep(0.1)
    assert not ta.done()
    await asyncio.sleep(0.2)
    assert ta.done()
    assert ta.result() == True
