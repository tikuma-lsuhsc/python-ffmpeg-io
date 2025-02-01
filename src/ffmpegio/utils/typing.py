from __future__ import annotations

from typing import *
from typing_extensions import *
from fractions import Fraction

# from typing_extensions import *

MediaType = Literal["v", "a", "s", "d", "t", "V"]
# libavformat/avformat.c:match_stream_specifier()


class StreamSpec_Options(TypedDict):
    media_type: MediaType  # py3.11 NotRequired[MediaType]
    file_index: int  # py3.11 NotRequired[int]
    program_id: int  # py3.11 NotRequired[int]
    group_index: int  # py3.11 NotRequired[int]
    group_id: int  # py3.11 NotRequired[int]
    stream_id: int  # py3.11 NotRequired[int]


class StreamSpec_Index(StreamSpec_Options):
    index: int


class StreamSpec_Tag(StreamSpec_Options):
    tag: Union[str, Tuple[str, str]]


class StreamSpec_Usable(StreamSpec_Options):
    usable: bool


StreamSpec = Union[StreamSpec_Index, StreamSpec_Tag, StreamSpec_Usable]

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
