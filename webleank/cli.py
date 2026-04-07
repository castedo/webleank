'''
Link Lean sidekick web apps to LSP-enabled editors
'''

import argparse, asyncio, logging, socket, subprocess, sys
from typing import Any

from .service import ServiceProgram, socket_available
from .util import log, version

from lspleanklib import (
    async_stdio_main,
    get_user_socket_path,
    lspleank_connect_main,
)


PROG_NAME = "webleank"


def no_stdio_Popen(cmd: list[str], **os_specific_kwargs: Any) -> None:
    subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        **os_specific_kwargs,
    )


def main_subcmd_start() -> int:
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
    sub = cli.add_subparsers(dest='subcmd', required=True)

    sub.add_parser(
        'connect',
        help='run as stdio LSP server connecting to lspleank socket',
    )

    sub.add_parser(
        'start',
        help='start webleank service as detached background process',
    )

    service = sub.add_parser(
        'service',
        help='run as webleank service process',
    )
    service.add_argument(
        '--web-port',
        type=int,
        default=1342,
        help='port for websockets and control panel web app',
    )

    args = cli.parse_args()
    match args.subcmd:
        case 'connect':
            start_cmd = [sys.executable, '-m', PROG_NAME, 'start']
            return lspleank_connect_main(start_cmd)
        case 'start':
            return main_subcmd_start()
        case 'service':
            sock_path = get_user_socket_path()
            return async_stdio_main(ServiceProgram(args.web_port, sock_path))
    raise NotImplementedError
