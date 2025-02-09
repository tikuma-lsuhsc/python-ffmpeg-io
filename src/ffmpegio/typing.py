"""type hint definition for external use"""
from __future__ import annotations

from typing import *
from typing_extensions import *
from collections.abc import Buffer

from ._typing import *
from .filtergraph.abc import FilterGraphObject

from .stream_spec import MediaType, StreamSpecDict, StreamSpecDictMediaType

from .configure import FFmpegArgs, FFmpegUrlType

# from typing_extensions import *

