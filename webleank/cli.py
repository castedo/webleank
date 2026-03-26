'''
Link Lean sideick web apps to LSP-enabled editors
'''

import argparse, asyncio, logging, socket
from pathlib import Path

from .server import run_server, log

from lspleanklib import get_user_socket_path


async def socket_available(sock_path: Path) -> bool:
    try:
        await asyncio.open_unix_connection(sock_path)
    except FileNotFoundError:
        return False
    return True


async def amain_start(web_port: int) -> int:
    try:
        sock_path = get_user_socket_path()
        started = await run_server(web_port, sock_path)
        if not started:
            other_process_got_it = await socket_available(sock_path)
            if not other_process_got_it:
                log.error(f"Unable to start server for {sock_path}")
            return 0 if other_process_got_it else 1
        # TODO: daemonize
    except Exception as ex:
        log.exception(ex)
        return 1
    return 0


def version() -> str:
    try:
        from ._version import version  # type: ignore[import-not-found]

        return str(version)
    except ImportError:
        return '0.0.0'


def main(cmd_line_args: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.DEBUG)
    logging.captureWarnings(True)

    if not hasattr(socket, 'AF_UNIX'):
        bad_os = "Missing operating system support for UNIX domain sockets"
        log.error(bad_os)
        return 1

    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--version', action='version', version=version())
    cli.add_argument('command', choices=['start'])
    cli.add_argument(
        '--web-port',
        type=int,
        default=1342,
        help='port for websockets and control panel web app',
    )
    args = cli.parse_args()
    return asyncio.run(amain_start(args.web_port))
