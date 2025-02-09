"""common-across-subpackages utility functions that are not dependent on ffmpegio types and other functions"""

from __future__ import annotations

from typing import Any, Sequence

from io import IOBase
from pathlib import Path
from namedpipe import NPopen
import urllib.parse

import re

try:
    from math import prod
except:
    from functools import reduce
    from operator import mul

    prod = lambda seq: reduce(mul, seq, 1)

from builtins import zip as builtin_zip


def zip(*args, strict=False):

    # backwards compatibility for pre-py3.10

    try:
        return builtin_zip(*args, strict=strict)
    except TypeError:
        if strict is False:
            return builtin_zip(*args)

    def strict_zip():
        # strict=True case, excerpted from PEP618: https://peps.python.org/pep-0618/
        iterators = tuple(iter(iterable) for iterable in args)
        try:
            while True:
                items = []
                for iterator in iterators:
                    items.append(next(iterator))
                yield tuple(items)
        except StopIteration:
            pass

        if items:
            i = len(items)
            plural = " " if i == 1 else "s 1-"
            msg = f"zip() argument {i+1} is shorter than argument{plural}{i}"
            raise ValueError(msg)
        sentinel = object()
        for i, iterator in enumerate(iterators[1:], 1):
            if next(iterator, sentinel) is not sentinel:
                plural = " " if i == 1 else "s 1-"
                msg = f"zip() argument {i+1} is longer than argument{plural}{i}"
                raise ValueError(msg)

    return strict_zip()


def is_non_str_sequence(
    value: Any, class_excluded: type | tuple[type, ...] = str
) -> bool:
    """Returns true if value is a sequence but not a str object"""
    return isinstance(value, Sequence) and not isinstance(value, class_excluded)


def as_multi_option(value: Any, exclude_classes: tuple[type] = None) -> Sequence[Any]:
    """Put value in a list if it is not already a sequence

    :param value: value to be put in a list
    :param exclude_classes: sequence classes to be treated as an option value, defaults to None
    :return: option values in a sequence
    """

    if exclude_classes is None:
        exclude_classes = str

    return (
        value
        if isinstance(value, Sequence) and not isinstance(value, exclude_classes)
        else [value]
    )


def dtype_itemsize(dtype):
    return int(dtype[-1])


def get_samplesize(shape, dtype):
    return prod(shape) * dtype_itemsize(dtype)


def deprecate_core():
    from importlib import metadata
    import warnings

    try:
        metadata.version("ffmpegio-core")
    except metadata.PackageNotFoundError:
        return

    warnings.warn(
        message="!!PACKAGE CONFLICT!! ffmpegio-core distribution package has been deprecated. Please read the following link for the instructions: https://github.com/python-ffmpegio/python-ffmpegio/wiki/Instructions-to-upgrade-to-v0.11.0."
    )


def is_url(value: Any, *, pipe_ok: bool = False) -> bool:
    """True if input/output url string path parsed URL
    :param pipe_ok: True to allow FFmpeg pipe protocol string"""
    return (
        pipe_ok or not is_pipe(value)
        if isinstance(value, str)
        else isinstance(value, (Path, urllib.parse.ParseResult))
    )


def is_pipe(value: Any) -> bool:
    """True if FFmpeg pipe protocol string"""
    return value == "-" or bool(re.match(r"pipe\:\d*", value))


def is_namedpipe(
    value: Any, *, readable: bool | None = None, writable: bool | None = None
) -> bool:
    """True if named pipe object

    :param readable: True to test for readable pipe, False to test for non-readable pipe, defaults to None (either)
    :param writable: True to test for writable pipe, False to test for non-writable pipe, defaults to None (either)
    """
    return (
        isinstance(value, NPopen)
        and (readable is None or value.readable() is readable)
        and (writable is None or value.writable() is writable)
    )


def is_fileobj(
    value: Any,
    *,
    seekable: bool | None = None,
    readable: bool | None = None,
    writable: bool | None = None,
) -> bool:
    """True if file object

    :param readable: True to test for readable pipe, False to test for non-readable pipe, defaults to None (either)
    :param writable: True to test for writable pipe, False to test for non-writable pipe, defaults to None (either)
    """

    if not isinstance(value, IOBase):
        return False

    if seekable is True and not value.seekable():
        raise ValueError("Requested seekable file object but it's not seekable.")
    elif seekable is False and value.seekable():
        raise ValueError("Requested non-seekable file object but it is seekable.")

    if readable is True and not value.readable():
        raise ValueError("Requested readable file object but it's not readable.")
    elif readable is False and value.readable():
        raise ValueError("Requested non-readable file object but it is readable.")

    if writable is True and not value.writable():
        raise ValueError("Requested writable file object but it's not writable.")
    elif writable is False and value.writable():
        raise ValueError("Requested non-writable file object but it is writable.")

    return True

def escape(txt: str) -> str:
    """apply FFmpeg single quote escaping

    :param txt: Unescaped string
    :return: Escaped string

    See https://ffmpeg.org/ffmpeg-utils.html#Quoting-and-escaping
    """

    txt = str(txt)

    if re.search(r"\s", txt, re.MULTILINE):
        # quote if txt has any white space
        txt = txt.replace("'", r"'\''")
        return f"'{txt}'"
    else:
        # if not quoted, escape quotes and backslashes
        return re.sub(r"(['\\])", r"\\\1", txt)


def unescape(txt: str) -> str:
    """undo FFmpeg single quote escaping

    :param txt: Escaped string
    :return: Original string

    See https://ffmpeg.org/ffmpeg-utils.html#Quoting-and-escaping
    """

    n = len(txt)
    if not n:
        return txt

    re_start = re.compile(r"[^\\](?:\\\\)*'")
    re_sub = re.compile(r"\\([\\'])")

    blks = []

    # look for a first quoted text block
    m = re.search(r"(?:^|[^\\])(?:\\\\)*'", txt)
    if m:
        i0 = m.end()
        if i0 > 1:
            # unescape the initial unquoted block
            blks.append(re_sub.sub(r"\1", txt[0 : i0 - 1]))
    else:
        # no quoted text block, unescape the whole string
        return re_sub.sub(r"\1", txt)

    # always starts with quoted block
    in_quote = True

    while i0 < n:

        if in_quote:
            # find the end quote
            i1 = txt.find("'", i0)
            if i1 < 0:
                raise ValueError("incorrectly escaped text: missing a closing quote.")
            blks.append(txt[i0:i1])
        else:
            # find the next starting quote
            m = re_start.search(txt, i0 - 1)
            i1 = m.end() - 1 if m else n
            blks.append(re_sub.sub(r"\1", txt[i0:i1]))
        i0 = i1 + 1
        in_quote = not in_quote

    return "".join(blks)
