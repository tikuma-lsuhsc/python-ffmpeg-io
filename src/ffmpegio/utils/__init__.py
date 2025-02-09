""" utility functions for the main modules"""

from __future__ import annotations

from collections.abc import Sequence
from numbers import Number

from math import cos, radians, sin
import re, fractions

from .. import caps, plugins
from .._utils import *
from ..stream_spec import *

from ..typing import Any

# TODO: auto-detect endianness
# import sys
# sys.byteorder




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


def get_pixel_config(
    input_pix_fmt: str, pix_fmt: str | None = None
) -> tuple[str, int, str, bool]:
    """get best pixel configuration to read video data in specified pixel format

    :param input_pix_fmt: input pixel format
    :param pix_fmt: desired output pixel format, defaults to None (auto-select)
    :return: output pix_fmt, number of components, data type string, and whether
             alpha component must be removed

    =====  =====  =========  ===================================
    ncomp  dtype  pix_fmt    Description
    =====  =====  =========  ===================================
      1     |u1   gray       grayscale
      1     <u2   gray10le   10-bit grayscale
      1     <u2   gray12le   12-bit grayscale
      1     <u2   gray14le   14-bit grayscale
      1     <u2   gray16le   16-bit grayscale
      1     <f4   grayf32le  floating-point grayscale
      2     |u1   ya8        grayscale with alpha channel
      2     <u2   ya16le     16-bit grayscale with alpha channel
      3     |u1   rgb24      RGB
      3     <u2   rgb48le    16-bit RGB
      4     |u1   rgba       RGB with alpha transparency channel
      4     <u2   rgba64le   16-bit RGB with alpha channel
    =====  =====  =========  ===================================
    """
    try:
        fmt_info = caps.pix_fmts()[input_pix_fmt]
    except:
        raise Exception(
            f"unknown pixel format '{input_pix_fmt}' specified. Run ffmpegio.caps.pix_fmts() for supported formats."
        )
    n_in = fmt_info["nb_components"]
    bpp = fmt_info["bits_per_pixel"]

    if pix_fmt is None:
        if n_in == 1:
            pix_fmt = "gray" if bpp <= 8 else "gray16le" if bpp <= 16 else "grayf32le"
        elif n_in == 2:
            pix_fmt = "ya8" if bpp <= 16 else "ya16le"
        elif n_in == 3:
            pix_fmt = "rgb24" if bpp <= 24 else "rgb48le"
        elif n_in == 4:
            pix_fmt = "rgba" if bpp <= 32 else "rgba64le"

    if pix_fmt == input_pix_fmt:
        n_out = n_in
    elif n_in == 1 and pix_fmt == "gray16le":
        # sub-16-bit pixel format, use the input format
        pix_fmt = input_pix_fmt
        n_out = n_in
    else:
        fmt_info = caps.pix_fmts()[pix_fmt]
        n_out = fmt_info["nb_components"]
        bpp = fmt_info["bits_per_pixel"]

    return (
        pix_fmt,
        n_out,
        "|u1" if bpp // n_out <= 8 else "<u2" if bpp // n_out <= 16 else "<f4",
        not n_in % 2 and n_out % 2,  # True if transparency need to be dropped
    )


def alpha_change(
    input_pix_fmt: str, output_pix_fmt: str | None, dir: int | None = None
) -> bool | int | None:
    """get best pixel configuration to read video data in specified pixel format

    :param input_pix_fmt: input pixel format
    :param output_pix_fmt: output pixel format
    :param dir: specify the change direction for boolean answer, defaults to None
    :return: dir None: 0 if no change, 1 if alpha added, -1 if alpha removed, None if indeterminable
             dir int: True if changes in the specified direction or False

    """
    if input_pix_fmt is None or output_pix_fmt is None:
        return None if dir is None else False
    n_in = caps.pix_fmts()[input_pix_fmt]["nb_components"]
    n_out = caps.pix_fmts()[output_pix_fmt]["nb_components"]
    d = (n_in % 2) - (n_out % 2)
    return d if dir is None else d > 0 if dir > 0 else d < 0 if dir < 0 else d == 0


def get_pixel_format(fmt: str) -> tuple[str, int]:
    """get data format and number of components associated with video pixel format

    :param fmt: ffmpeg pix_fmt
    :return: data type string and the number of components associated with the pix_fmt
    """
    try:
        return dict(
            gray=("|u1", 1),
            gray10le=("<u2", 1),
            gray12le=("<u2", 1),
            gray14le=("<u2", 1),
            gray16le=("<u2", 1),
            grayf32le=("<f4", 1),
            ya8=("|u1", 2),
            ya16le=("<u2", 2),
            rgb24=("|u1", 3),
            rgb48le=("<u2", 3),
            rgba=("|u1", 4),
            rgba64le=("<u2", 4),
        )[fmt]
    except:
        raise ValueError(f"{fmt} is not a valid grayscale/rgb pix_fmt")


def get_video_format(
    fmt: str, s: tuple[int, int] | str
) -> tuple[str, tuple[int, int, int]]:
    """get pixel data type and frame array (height,width,ncomp)

    :param fmt: ffmpeg pix_fmt or data type string
    :param s: frame size  (width,height)
    :return: data type string and shape tuple
    """
    dtype, ncomp = get_pixel_format(fmt)
    s = parse_video_size(s)
    return dtype, (*s[::-1], ncomp)


def guess_video_format(
    shape: Sequence[int, int, int], dtype: str
) -> tuple[tuple[int, int], str]:
    """get video format

    :param shape: frame data shape
    :param dtype: frame data type
    :return: frame size and pix_fmt

    ```
        X = np.ones((100,480,640,3),'|u1')
        size, pix_fmt = guess_video_format(X)
        # => size=(640,480), pix_fmt='rgb24'

        # the same result can be obtained by
        size, pix_fmt = guess_video_format((X.shape,X.dtype))
    """

    ndim = len(shape)
    if ndim < 2 or ndim > 4:
        raise ValueError(
            f"invalid video data dimension: data shape must be must be 2d, 3d or 4d"
        )

    has_comp = ndim != 2 and (ndim != 3 or shape[-1] < 5)
    size = shape[-2:-4:-1] if has_comp else shape[:-3:-1]
    ncomp = shape[-1] if has_comp else 1

    try:
        pix_fmt = {
            "|u1": {1: "gray", 2: "ya8", 3: "rgb24", 4: "rgba"},
            "<u2": {1: "gray16le", 2: "ya16le", 3: "rgb48le", 4: "rgba64le"},
            "<f4": {1: "grayf32le"},
        }[dtype][ncomp]
    except Exception as e:
        print(e)
        raise ValueError(
            f"dtype ({dtype}) and guessed number of components ({ncomp}) do not yield a pix_fmt."
        )

    return size, pix_fmt


def get_rotated_shape(w: int, h: int, deg: float) -> tuple[int, int]:
    """compute the shape of rotated rectangle

    :param w: rectangle width
    :param h: rectangle height
    :param deg: rotation angle in degrees, positive in clockwise direction
    :return: the (width, height) after rotation
    """
    theta = radians(deg)
    C = cos(theta)
    S = sin(theta)
    return int(round(abs(C * w - S * h))), int(round(abs(S * w + C * h))), theta
    # X = [[C, -S], [S, C]], [[w, w, 0.0], [0.0, h, h]]
    # return int(round(abs(X[0, 0] - X[0, 2]))), int(round(abs(X[1, 1]))), theta


def get_audio_codec(fmt: str) -> tuple[str, str]:
    """get pcm audio codec & format

    :param fmt: ffmpeg sample_fmt
    :return: tuple of pcm codec name and container format
    """
    try:
        return dict(
            u8=("pcm_u8", "u8"),
            s16=("pcm_s16le", "s16le"),
            s32=("pcm_s32le", "s32le"),
            s64=("pcm_s64le", "s64le"),
            flt=("pcm_f32le", "f32le"),
            dbl=("pcm_f64le", "f64le"),
        )[fmt]
    except:
        raise ValueError(f"{fmt} is not a valid raw audio sample_fmt")


def get_audio_format(fmt: str, ac: int | None = None) -> str | tuple[str, tuple[int]]:
    """get audio sample data format

    :param fmt: ffmpeg sample_fmt or data type string
    :param ac: number of channels, default to None (to return only dtype)
    :return: data type string and array shape tuple
    """
    try:
        dtype = {
            "u8": "|u1",
            "s16": "<i2",
            "s32": "<i4",
            "s64": "<i8",
            "flt": "<f4",
            "dbl": "<f8",
        }[fmt]
        return dtype, (None if ac is None else (ac,))
    except:
        raise ValueError(f"Unsupported or unknown sample_fmt ({fmt}) specified.")


def guess_audio_format(
    dtype: str, shape: Sequence[int] | None = None
) -> tuple[int, str]:
    """get audio format

    :param dtype: sample data type
    :param shape: sample data shape
    :return: tuple of # of channels and sample_fmt

    ```
        X = np.ones((1000,2),np.int16)
        sample_fmt, ac = guess_audio_format(X.dtype, X.shape)
        # => sample_fmt='s16', ac=2
    """

    if shape is not None:
        ndim = len(shape)
        if ndim < 1 or ndim > 2:
            raise ValueError(
                f"invalid audio data dimension: data shape must be must be 1d or 2d"
            )

    try:
        sample_fmt = {
            "|u1": "u8",
            "<i2": "s16",
            "<i4": "s32",
            "<i8": "s64",
            "<f4": "flt",
            "<f8": "dbl",
        }[dtype]
    except:
        raise ValueError(f"Unsupported or invalid dtype ({dtype}) specified")

    return sample_fmt, (None if shape is None else shape[-1])


def parse_video_size(expr: str | tuple[int, int]) -> tuple[int, int]:

    if isinstance(expr, str):
        m = re.match(r"(\d+)x(\d+)", expr)
        if m:
            return (int(m[1]), int(m[2]))

        return caps.video_size_presets[expr]
    else:
        return expr


def parse_frame_rate(expr) -> fractions.Fraction:
    try:
        return fractions.Fraction(expr)
    except ValueError:
        return caps.frame_rate_presets[expr]


def parse_color(expr) -> tuple[int, int, int, int | None]:
    m = re.match(
        r"([^@]+)?(?:@(0x[\da-f]{2}|[0-1]\.[0-9]+))?$",
        expr,
        re.IGNORECASE,
    )
    expr = m[1]
    alpha = m[2] and (int(m[2], 16) if m[2][1] == "x" else float(m[2]))

    m = re.match(
        r"(?:0x|#)?([\da-f]{6})([\da-f]{2})?$",
        expr,
        re.IGNORECASE,
    )
    if m:
        rgb = m[1]
        if m[2] and alpha is None:
            alpha = int(m[2], 16)
    else:
        colors = caps.colors()
        name = next((k for k in colors.keys() if k.lower() == expr.lower()), None)
        if name is None:
            raise Exception("invalid color expression")
        rgb = colors[name][1:]

    return int(rgb[:2], 16), int(rgb[2:4], 16), int(rgb[4:], 16), alpha


def compose_color(r: str | Sequence[Number], *args: tuple[Number]) -> str:

    if isinstance(r, str):
        colors = caps.colors()
        name = next((k for k in colors.keys() if k.lower() == r.lower()), None)
        if name is None:
            raise Exception("invalid predefined color name")
        return name
    else:

        def conv(x):
            if isinstance(x, float):
                x = int(x * 255)
            return f"{x:02X}"

        if len(args) < 4:
            args = (*args, *([255] * (3 - len(args))))

        return "".join((conv(x) for x in (r, *args)))


def layout_to_channels(layout: str) -> int:
    layouts = caps.layouts()["layouts"]
    names = caps.layouts()["channels"].keys()
    if layout in layouts:
        layout = layouts[layout]

    def each_ch(expr):
        if expr in layouts:
            return layout_to_channels(expr)
        elif expr in names:
            return 1
        else:
            m = re.match(r"(?:(\d+)(?:c|C)|(0x[\da-f]+))", expr)
            if m:
                return (
                    int(m[1])
                    if m[1]
                    else sum([(c == "1") for c in tuple(bin(int(m[2], 16))[2:])])
                )
            else:
                raise Exception(f"invalid channel layout expression: {expr}")

    return sum([each_ch(ch) for ch in re.split(r"\+|\|", layout)])


def parse_time_duration(expr: str | float) -> float:
    """convert time/duration expression to seconds

    if expr is not str, the input is returned without any processing

    :param expr: time/duration expression (or in seconds to pass through)
    :return: time/duration in seconds
    """
    if isinstance(expr, str):
        m = re.match(r"(-)?((\d{2})\:)?(\d{2}):(\d{2}(?:\.\d+)?)", expr)
        if m:
            s = int(m[3]) * 60 + float(m[4])
            if m[2]:
                s += 3600 * int(m[2])
            return -s if m[1] else s
        m = re.match(r"(-)?(\d+(?:\.\d+)?)(s|ms|us)?", expr)
        if m:
            s = float(m[2])
            if m[3] == "ms":
                s *= 1e-3
            elif m[3] == "us":
                s *= 1e-6
            return -s if m[1] else s
        raise Exception("invalid time duration")
    return expr


def find_stream_options(options: dict[str, Any], name: str) -> dict[str, Any]:
    """find option keys, which may be stream-specific

    :param options: source option dict (content will be modified)
    :param suffix: matching suffix
    :return: popped options
    """

    re_opt = re.compile(rf"{name}(?=\:|$)")
    return [k for k in options if re_opt.match(k)]


def pop_extra_options(options: dict[str, Any], suffix: str) -> dict[str, Any]:
    """pop matching keys from options dict

    :param options: source option dict (content will be modified)
    :param suffix: matching suffix
    :return: popped options
    """
    n = len(suffix)
    return {
        k[:-n]: options.pop(k)
        for k in [k for k in options.keys() if k.endswith(suffix)]
    }


def pop_extra_options_multi(
    options: dict[str, Any], suffix: str
) -> tuple[str, dict[int, dict[str, Any]]]:
    """pop regex matching keys from options dict and

    :param options: source option dict (content will be modified)
    :param suffix: matching suffix regex expression with one group, capturing the (int) id
    :return: dict of popped options with int id key

    example:

        pop_extra_options_multi({...},r'_in(\d+)$')

    """

    popped = {}

    def match(name, v):
        m = re.search(suffix, name)
        if m:
            k = name[: m.end()]
            id = int(m[1])
            if id in popped:
                popped[id][k] = v
            else:
                popped[id] = {k: v}
        return bool(m)

    for o in (k for k, v in options.items() if match(k, v)):
        options.pop(o)

    return popped


def pop_global_options(options: dict[str, Any]) -> dict[str, Any]:
    """pop global options from options dict

    :param options: source option dict (content will be modified)
    :return: popped options
    """

    all_gopts = caps.options("global")
    return {k: options.pop(k) for k in [k for k in options.keys() if k in all_gopts]}


def array_to_audio_options(data: Any) -> dict:
    """create an input option dict for the given raw audio data blob
    :param data: input audio data, accessed by `audio_info` plugin hook, defaults to None (manual config)
    :returns: dict of audio options
    """

    shape = dtype = None
    shape, dtype = plugins.get_hook().audio_info(obj=data)
    sample_fmt, ac = guess_audio_format(dtype, shape)
    codec, f = get_audio_codec(sample_fmt)
    return {"f": f, f"c:a": codec, f"ac": ac, f"sample_fmt": sample_fmt}


def array_to_video_options(data: Any | None = None) -> dict:
    """create an input option dict for the given raw video data blob

    :param data: input video frame data, accessed with `video_info` plugin hook, defaults to None (manual config)
    :return: option dict
    """

    s, pix_fmt = guess_video_format(*plugins.get_hook().video_info(obj=data))
    return {"f": "rawvideo", f"c:v": "rawvideo", f"s": s, f"pix_fmt": pix_fmt}
