from __future__ import annotations

from typing import *
from typing_extensions import *
from fractions import Fraction

# from typing_extensions import *

MediaType = Literal["v", "a", "s", "d", "t", "V"]
# libavformat/avformat.c:match_stream_specifier()


from ..stream_spec import StreamSpec

RawDataBlob = Any  # depends on raw data reader plugin

RawStreamDef = (
    tuple[int | float | Fraction, RawDataBlob] | tuple[RawDataBlob, dict[str, Any]]
)


class FFmpegArgs(TypedDict):
    """FFmpeg arguments
    """

    inputs: list[tuple[str, dict | None]] # list of input definitions (pairs of url and options)
    outputs: list[tuple[str, dict | None]] # list of output definitions (pairs of url and options)
    global_options: NotRequired[dict | None] # FFmpeg global options


ProgressCallable = Callable[[dict[str, Any], bool], bool]
"""FFmpeg progress callback function

    callback(status, done)

      status - dict of encoding status
      done - True if the last callback

    The callback may return True to cancel the FFmpeg execution.
"""
