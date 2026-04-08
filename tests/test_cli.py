import pytest

import asyncio, os, sys
from pathlib import Path


@pytest.mark.slow
async def test_cli_start():
    sock_path = Path(os.environ['XDG_RUNTIME_DIR']) / "lean" / "lspleank.sock"
    assert not sock_path.exists()
    cmd = [sys.executable, "-m", "webleank", "start"]
    env = os.environ
    env.update({
        'WEBLEANK_LINGER_SECONDS': "0.5",
    })
    proc = await asyncio.create_subprocess_exec(*cmd, env=env)
    await asyncio.sleep(0.25)
    assert sock_path.exists()
    assert proc.returncode == 0

    proc2 = await asyncio.create_subprocess_exec(*cmd, env=env)
    await proc2.communicate()
    assert sock_path.exists()
    assert proc2.returncode == 0
