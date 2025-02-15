from __future__ import annotations

#!/usr/bin/python
# -*- coding: utf-8 -*-
"""FFmpeg I/O interface

Transcode media file to another format/codecs
---------------------------------------------
:py:func:`ffmpegio.transcode()`

Stream Read/Write
-----------------

ffmpegio.open()

Block Read/Write/Filter Functions
---------------------------------

`ffmpegio.video.read()`
`ffmpegio.video.write()`
`ffmpegio.video.filter()`
`ffmpegio.image.read()`
`ffmpegio.image.write()`
`ffmpegio.image.filter()`
`ffmpegio.audio.read()`
`ffmpegio.audio.write()`
`ffmpegio.audio.filter()`
`ffmpegio.media.read()`
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger("ffmpegio")
logger.addHandler(logging.NullHandler())

from . import path, plugins

# register builtin plugins and external plugins found in site-packages
plugins.initialize()

# initialize the paths
try:
    path.find()
except Exception as e:
    logger.warning(str(e))

use = plugins.use

def __getattr__(name):
    if name == "ffmpeg_ver":
        return path.FFMPEG_VER
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")


from . import ffmpegprocess

from .errors import FFmpegError, FFmpegioError
from .utils.concat import FFConcat
from .filtergraph import Graph as FilterGraph
from . import devices, ffmpegprocess, caps, probe, audio, image, video, media
from .transcode import transcode
from . import streams as _streams
from .utils.parser import FLAG

# check if ffmpegio-core is installed, if it is warn its deprecation
from ._utils import deprecate_core
deprecate_core()

# fmt:off
__all__ = ["ffmpeg_info", "get_path", "set_path", "is_ready", "ffmpeg", "ffprobe",
    "transcode", "caps", "probe", "audio", "image", "video", "media", "devices",
    "open", "ffmpegprocess", "FFmpegError", "FFmpegioError", "FilterGraph", "FFConcat", "use"]
# fmt:on

__version__ = "0.11.0"

ffmpeg_info = path.versions
set_path = path.find
get_path = path.where
is_ready = path.found
ffmpeg = path.ffmpeg
ffprobe = path.ffprobe


def open(
    url_fg: str,
    mode: str,
    rate_in: Optional[float] = None,
    shape_in: Optional[Tuple[int, ...]] = None,
    dtype_in: Optional[str] = None,
    rate: Optional[float] = None,
    shape: Optional[Tuple[int, ...]] = None,
    **kwds,
):
    """Open a multimedia file/stream for read/write

    :param url_fg: URL of the media source/destination for file read/write or filtergraph definition
                   for filter operation.
    :type url_fg: str or seq(str)
    :param mode: specifies the mode in which the FFmpeg is used, see below
    :type mode: str
    :param rate_in: (write and filter only, required) input frame rate (video) or sampling rate 
                    (audio), defaults to None
    :type rate_in: Fraction, float, int, optional
    :param shape_in: (write and filter only) input video frame size (height x width [x ncomponents]),
                  or audio sample size (channels,), defaults to None
    :type shape_in: seq of int, optional
    :param dtype_in: (write and filter only) input data type, defaults to None
    :type dtype_in: str, optional
    :param rate: (filter only, required) output frame rate (video write) or sample rate (audio
                 write), defaults to None
    :type rate: Fraction, float, int, optional
    :param dtype: (read and filter specific) output data type, defaults to None
    :type dtype: str, optional
    :param shape: (read and filter specific) output video frame size (height x width [x ncomponents]),
                  or audio sample size (channels,), defaults to None
    :type shape: seq of int, optional
    :param show_log: True to echo the ffmpeg log to stdout, default to False
    :type show_log: bool, optional
    :param progress: progress callback function (see :ref:`quick-callback`)
    :type progress: Callable, optional
    :param blocksize: (read and filter only) Number of frames to read by `read()` method, default to None (auto)
    :type blocksize: int, optional
    :param extra_inputs: (write only) List of additional (non-pipe) inputs to pass onto FFmpeg. Each
                         input is defined by a tuple of its url or a dict of input options, default to None
    :type extra_inputs: List[Tuple[str,dict]], optional
    :param default_timeout: (filter only) default filter timeout in seconds, defaults to None (10 ms)
    :type default_timeout: float, optional
    :param sp_kwargs: Keyword arguments for FFmpeg process (see :py:class:`ffmpegio.ffmpegprocess.Popen`), default to None
    :type sp_kwargs: dict, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional
    :returns: ffmpegio stream object

    Start FFmpeg and open I/O link to it to perform read/write/filter operation and return
    a corresponding stream object. If the file cannot be opened, an error is raised.
    See :ref:`quick-streamio` for more examples of how to use this function.

    Just like built-in `open()`, it is good practice to use the with keyword when dealing with 
    ffmpegio stream objects. The advantage is that the ffmpeg process and associated threads are 
    properly closed after ffmpeg terminates, even if an exception is raised at some point. 
    Using with is also much shorter than writing equivalent try-finally blocks.
    
    :Examples:

    Open an MP4 file and process all the frames::

        with ffmpegio.open('video_source.mp4', 'rv') as f:
            frame = f.read()
            while frame:
                # process the captured frame data
                frame = f.read()

    Read an audio stream of MP4 file and write it to a FLAC file as samples
    are decoded::

        with ffmpegio.open('video_source.mp4','ra') as rd:
            fs = rd.sample_rate
            with ffmpegio.open('video_dst.flac','wa',rate_in=fs) as wr:
                frame = rd.read()
                while frame:
                    wr.write(frame)
                    frame = rd.read()

    :Additional Notes:

    `url_fg` can be a string specifying either the pathname (absolute or relative to the current
    working directory) of the media target (file or streaming media) to be opened or a string describing
    the filtergraph to be implemented. Its interpretation depends on the `mode` argument.

    `mode` is an optional string that specifies the mode in which the FFmpeg is opened.

    ====  ===================================================
    Mode  Description
    ====  ===================================================
    'r'   read from url
    'w'   write to url
    'f'   filter data defined by fg
    'v'   operate on video stream, 'vv' if multi-video reader
    'a'   operate on audio stream, 'aa' if multi-audio reader
    ====  ===================================================

    `rate` and `rate_in`: Video frame rates shall be given in frames/second and
    may be given as a number, string, or `fractions.Fraction`. Audio sample rate in
    samples/second (per channel) and shall be given as an integer or string.

    Optional `shape` or `shape_in` for video defines the video frame size and
    number of components with a 2 or 3 element sequence: `(width, height[, ncomp])`.
    The number of components and other optional `dtype` (or `dtype_in`) implicitly
    define the pixel format (FFmpeg pix_fmt option):

    =====  =====  =========  ===================================
    ncomp  dtype  pix_fmt    Description
    =====  =====  =========  ===================================
      1     \|u8   gray       grayscale
      1     <u2   gray10le   10-bit grayscale
      1     <u2   gray12le   12-bit grayscale
      1     <u2   gray14le   14-bit grayscale
      1     <u2   gray16le   16-bit grayscale (default <u2 choice)
      1     <f4   grayf32le  floating-point grayscale
      2     \|u1   ya8        grayscale with alpha channel
      2     <u2   ya16le     16-bit grayscale with alpha channel
      3     \|u1   rgb24      RGB
      3     <u2   rgb48le    16-bit RGB
      4     \|u1   rgba       RGB with alpha transparency channel
      4     <u2   rgba64le   16-bit RGB with alpha channel
    =====  =====  =========  ===================================

    For audio stream, single-element seq argument, `shape` or `shape_in`,
    specifies the number of channels while `dtype` and `dtype_in` determines
    the sample format (FFmpeg sample_fmt option):

    ======  ==========
    dtype   sample_fmt
    ======  ==========
     \|u1     u8
     <i2     s16
     <i4     s32
     <f4     flt
     <f8     dbl
    ======  ==========

    If dtypes and shapes are not specified at the time of opening, they will
    be set during the first write/filter operation using the input data.

    In addition, `open()` accepts the standard FFmpeg option keyword arguments.

    """

    is_fg = isinstance(url_fg, FilterGraph)
    if isinstance(url_fg, str):
        is_fg = kwds.get("f_in", None) == "lavfi"
        url_fg = (url_fg,)

    unk = set(mode) - set("avrwf")
    if unk:
        raise ValueError(
            f"Invalid FFmpeg streaming mode: {mode}. Unknown mode {unk} specified."
        )

    read = "r" in mode
    write = "w" in mode
    filter = "f" in mode

    if read + write + filter != 1:
        raise ValueError(
            f"Invalid FFmpeg streaming mode argument: {mode}. It must contain one and only one of 'rwf'."
        )

    audio = sum(1 for m in mode if m == "a")
    video = sum(1 for m in mode if m == "v")

    if audio + video == 0:
        raise ValueError(
            f"Invalid FFmpeg streaming mode argument: {mode}. Stream type not specified. Mode must contain 'v' or 'a' at least once."
        )

    if read:
        vars = []
        if rate_in is not None:
            vars.append("rate_in")
        if rate is not None:
            vars.append("rate")
        if len(vars):
            vars = ", ".join(vars)
            raise ValueError(
                f"Invalid argument for a read stream: {vars}. To change rate, use FFmpeg 'r' argument for video stream or 'ar' argument for audio stream."
            )
        vars = []
        if shape_in is not None:
            vars.append("shape_in")
        if shape is not None:
            vars.append("shape")
        if len(vars):
            vars = ", ".join(vars)
            raise ValueError(
                f"Invalid argument for a read stream: {vars}. To change shape, use FFmpeg 's' argument for video frame or 'ac' for the number of audio channels."
            )

        if dtype_in is not None:
            raise ValueError("Invalid argument for a read stream: dtype_in.")
    else:
        if audio + video > 1:
            raise ValueError(
                f"Too many streams specified: {mode}. A {'write' if write else 'filter'} stream can only process one stream at a time."
            )

        if write:
            if is_fg:
                ValueError("Cannot write to a filtergraph.")
            if rate_in is None:
                raise ValueError(
                    "Missing required argument: rate_in. A write stream must specify the rate of the input media stream."
                )
            if rate is not None:
                raise ValueError(
                    "Invalid argument for a write stream: rate. To change rate, use FFmpeg 'r' argument for video stream or 'ar' argument for audio stream."
                )
            if shape is not None:
                raise ValueError(
                    "Invalid argument for a read stream: shape. To change shape, use FFmpeg 's' argument for video frame or 'ac' for the number of audio channels."
                )
        else:  # if filter
            vars = []
            if rate_in is None:
                vars.append("rate_in")
            if rate is None:
                vars.append("rate")
            if len(vars):
                vars = ", ".join(vars)
                raise ValueError(
                    f"Missing required arguments: {vars}. A filter stream must specify the rates of both the input and output media streams."
                )

    try:
        StreamClass = (
            {
                0: {
                    0: _streams.SimpleAudioReader,
                    1: _streams.SimpleAudioWriter,
                    2: _streams.SimpleAudioFilter,
                },
                1: {
                    0: _streams.SimpleVideoReader,
                    1: _streams.SimpleVideoWriter,
                    2: _streams.SimpleVideoFilter,
                },
            }[video][write + 2 * filter]
            if audio + video == 1
            else _streams.AviMediaReader
        )
    except:
        raise ValueError(f"Invalid/unsupported FFmpeg streaming mode: {mode}.")

    if len(url_fg) > 1 and not StreamClass.multi_read:
        raise ValueError(f'Multi-input streaming is not supported in "{mode}" mode')

    # add other info to the arguments
    args = (*url_fg,) if read else (*url_fg, rate_in)
    if not read:
        for k, v in (
            ("dtype_in", dtype_in),
            ("shape_in", shape_in),
            ("rate", rate),
            ("shape", shape),
        ):
            if v is not None:
                kwds[k] = v

    # instantiate the streaming object
    return StreamClass(*args, **kwds)
