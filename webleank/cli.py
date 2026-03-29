'''
Link Lean sideick web apps to LSP-enabled editors
'''

import argparse, asyncio, logging, socket, subprocess, sys
from pathlib import Path
from typing import Any

from .server import run_service
from .util import log, version

from lspleanklib import get_user_socket_path, lspleank_connect_main


async def socket_available(sock_path: Path) -> bool:
    for i in range(8):
        try:
            await asyncio.open_unix_connection(sock_path)
            return True
        except (FileNotFoundError, ConnectionRefusedError):
            await asyncio.sleep(0.125)
    return False


def no_stdio_Popen(cmd: list[str], **os_specific_kwargs: Any) -> None:
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **os_specific_kwargs,
    )


def start_main() -> int:
    cmd = [sys.executable, '-m', PROG_NAME, 'service']
    if sys.platform != "win32":
        no_stdio_Popen(cmd, start_new_session=True)
    else:
        no_stdio_Popen(
            cmd,
            creationflags=(
                subprocess.DETACHED_PROCESS
                | subprocess.CREATE_NEW_PROCESS_GROUP
                | subprocess.CREATE_NO_WINDOW
            ),
        )
    sock_path = get_user_socket_path()
    ok = asyncio.run(socket_available(sock_path))
    return 0 if ok else 1


async def service_amain(web_port: int) -> int:
    try:
        sock_path = get_user_socket_path()
        started = await run_service(web_port, sock_path)
        if not started:
            other_process_got_it = await socket_available(sock_path)
            if not other_process_got_it:
                log.error(f"Unable to start server for {sock_path}")
            return 0 if other_process_got_it else 1
    except Exception as ex:
        log.exception(ex)
        return 1
    return 0


PROG_NAME = "webleank"


def main(cmd_line_args: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    logging.captureWarnings(True)
    log.setLevel(logging.DEBUG)

    if not hasattr(socket, 'AF_UNIX'):
        bad_os = "Missing operating system support for UNIX domain sockets"
        log.error(bad_os)
        return 1

    cli = argparse.ArgumentParser(prog=PROG_NAME, description=__doc__)
    cli.add_argument('--version', action='version', version=version())
    cli.add_argument('command', choices=['connect', 'start', 'service'])
    cli.add_argument(
        '--web-port',
        type=int,
        default=1342,
        help='port for websockets and control panel web app',
    )
    args = cli.parse_args()
    match args.command:
        case 'connect':
            start_cmd = [sys.executable, '-m', PROG_NAME, 'start']
            return lspleank_connect_main(start_cmd)
        case 'start':
            return start_main()
        case 'service':
            return asyncio.run(service_amain(args.web_port))
    raise NotImplementedError
