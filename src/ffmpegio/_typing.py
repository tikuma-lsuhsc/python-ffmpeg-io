"""ffmpegio object independent common type hints"""
from __future__ import annotations

from typing import *
from typing_extensions import *

from fractions import Fraction
from pathlib import Path
from urllib.parse import ParseResult

from namedpipe import NPopen


# from typing_extensions import *


RawDataBlob = Any  # depends on raw data reader plugin

RawStreamDef = (
    tuple[int | float | Fraction, RawDataBlob] | tuple[RawDataBlob, dict[str, Any]]
)


ProgressCallable = Callable[[dict[str, Any], bool], bool]
"""FFmpeg progress callback function

    callback(status, done)

      status - dict of encoding status
      done - True if the last callback

    The callback may return True to cancel the FFmpeg execution.
"""

MediaType = Literal["audio", "video"]

FFmpegMediaType = Literal["video", "audio", "subtitle", "data", "attachments"]

FFmpegUrlType = Union[str, Path, ParseResult]

