"""type hint definition for external use"""
from __future__ import annotations

from typing import *
from typing_extensions import *
from collections.abc import Buffer

from ._typing import *
from .filtergraph.abc import FilterGraphObject

from .stream_spec import MediaType, StreamSpecDict, StreamSpecDictMediaType

# from typing_extensions import *


class FFmpegArgs(TypedDict):
    """FFmpeg arguments"""

    inputs: list[
        tuple[FFmpegUrlType | FilterGraphObject, dict | None]
    ]  # list of input definitions (pairs of url and options)
    outputs: list[
        tuple[FFmpegUrlType, dict | None]
    ]  # list of output definitions (pairs of url and options)
    global_options: NotRequired[dict | None]  # FFmpeg global options

FFmpegInputUrlComposite = Union[FFmpegUrlType, FilterGraphObject, IO, Buffer]
FFmpegOutputUrlComposite = Union[FFmpegUrlType, IO]
