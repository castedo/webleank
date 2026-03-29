import logging
from collections.abc import Mapping
from typing import TypeVar

from lspleanklib import LspAny

T = TypeVar('T')

log = logging.getLogger(__spec__.parent)


async def awaitable(value: T) -> T:
    return value


def get_str(lobj: LspAny, key: str) -> str:
    got = lobj.get(key) if isinstance(lobj, Mapping) else None
    return got if isinstance(got, str) else ''


def get_obj(lobj: LspAny, key: str) -> Mapping[str, LspAny]:
    got = lobj.get(key) if isinstance(lobj, Mapping) else None
    return got if isinstance(got, Mapping) else {}


def version() -> str:
    try:
        from ._version import version  # type: ignore[import-not-found, import-untyped, unused-ignore]

        return str(version)
    except ImportError:
        return '0.0.0'
