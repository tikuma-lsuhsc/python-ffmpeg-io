"""ffmpegio.filtergraph.presets Module - a collection of preset filtergraph generators
"""

from __future__ import annotations

from .._typing import TYPE_CHECKING, Any, Sequence, Literal
from ..stream_spec import StreamSpecDict
from .abc import FilterGraphObject

from .. import filtergraph as fgb

if TYPE_CHECKING:
    from .Graph import Graph


def filter_video_basic(
    fill_color: str | None = None,
    remove_alpha: bool = False,
    scale: str | Sequence | None = None,
    crop: str | Sequence | None = None,
    flip: Literal["horizontal", "vertical", "both"] | None = None,
    transpose: str | Sequence | None = None,
    square_pixels: (
        Literal["upscale", "downscale", "upscale_even", "downscale_even"] | None
    ) = None,
) -> FilterGraphObject:
    bg_color = fill_color or "white"

    vfilters = (
        fgb.Graph(
            f"color=c={bg_color}[l1];[l1][in]scale2ref[l2],[l2]overlay=shortest=1"
        )
        if remove_alpha
        else fgb.Chain()
    )

    if square_pixels == "upscale":
        vfilters += "scale='max(iw,ih*dar)':'max(iw/dar,ih)':eval=init,setsar=1/1"
    elif square_pixels == "downscale":
        vfilters += "scale='min(iw,ih*dar)':'min(iw/dar,ih)':eval=init,setsar=1/1"
    elif square_pixels == "upscale_even":
        vfilters += "scale='trunc(max(iw,ih*dar)/2)*2':'trunc(max(iw/dar,ih)/2)*2':eval=init,setsar=1/1"
    elif square_pixels == "downscale_even":
        vfilters += "scale='trunc(min(iw,ih*dar)/2)*2':'trunc(min(iw/dar,ih)/2)*2':eval=init,setsar=1/1"
    elif square_pixels is not None:
        raise ValueError(f"unknown `square_pixels` option value given: {square_pixels}")

    if crop:
        try:
            assert not isinstance(crop, str)
            vfilters += fgb.Filter("crop", *crop)
        except:
            vfilters += fgb.Filter("crop", crop)

    if flip:
        try:
            ftype = ("", "horizontal", "vertical", "both").index(flip)
        except:
            raise Exception("Invalid flip filter specified.")
        if ftype % 2:
            vfilters += "hflip"
        if ftype >= 2:
            vfilters += "vflip"

    if transpose is not None:
        try:
            assert not isinstance(transpose, str)
            vfilters += fgb.Filter("transpose", *transpose)
        except:
            vfilters += fgb.Filter("transpose", transpose)

    if scale:
        try:
            scale = [int(s) for s in scale.split("x")]
        except:
            pass
        try:
            assert not isinstance(scale, str)
            vfilters += fgb.Filter("scale", *scale)
        except:
            vfilters += fgb.Filter("scale", scale)

    return vfilters


def merge_audio(
    streams: dict[StreamSpecDict, dict[str, Any]],
    output_ar: int | None = None,
    output_sample_fmt: str | None = None,
    output_pad_label: str | None = "aout",
) -> Graph:
    """Create a filtergraph to merge input audio streams.

    This preset filtergraph formats the input streams so that their sampling rates and sample formats are first converted
    to the same satisfying the requirements of the `amerge` filter.

    :param streams: List of input audio streams to merge. Each stream is keyed by its FFmpeg stream specifier and must provide its input options.
                    The option must include the sampling rate (`ar`) and sample format (`sample_fmt`).
    :param output_ar: Sampling rate of the merged audio stream in samples/second, defaults to None to use the sampling rate of the first input stream
    :param output_sample_fmt: Sample format of the merged audio stream, defaults to None to use the sample format of the first input stream
    :param output_pad_label: label of the `amerge` filter output, defaults to None to leave the output pad unconnected

    """

    # number of input audio streams to be merged
    n_ain = len(streams)

    # if output sampling rate or sample format not given, use the first stream's setting
    if output_ar is None or output_sample_fmt is None:
        opts = next(iter(streams.values()))
        if output_ar is None:
            output_ar = opts["ar"]
        if output_sample_fmt is None:
            output_sample_fmt = opts["sample_fmt"]

    # build complex_filter to merge

    def match_sample(sspec, opts):
        fopts = {}
        if opts["ar"] != output_ar:
            fopts["r"] = output_ar
        if opts["sample_fmt"] != output_sample_fmt:
            fopts["f"] = output_sample_fmt

        in_label = f"[{sspec}]"
        return (in_label >> fgb.aformat(**fopts)) if len(fopts) else in_label

    afilt = [match_sample(*st) for st in streams.items()] >> fgb.amerge(inputs=n_ain)

    return (afilt >> output_pad_label) if output_pad_label else afilt
