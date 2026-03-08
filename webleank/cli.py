'''
Link Lean sideick web apps to LSP-enabled editors
'''

import asyncio, argparse, logging


from .server import Server


LOG = logging.getLogger('webleank')


def version() -> str:
    try:
        from ._version import version  # type: ignore[import-not-found]

        return str(version)
    except ImportError:
        return '0.0.0'


def main(cmd_line_args: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--version', action='version', version=version())
    cli.add_argument('command', choices=['start'])
    cli.parse_args()
    srvr = Server()
    asyncio.run(srvr.run())
    return 0
