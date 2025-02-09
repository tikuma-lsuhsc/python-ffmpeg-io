"""ffmpegio.filtergraph.presets Module - a collection of preset filtergraph generators
"""

from __future__ import annotations

from ..typing import TYPE_CHECKING, Any, StreamSpecDict

from collections.abc import Sequence

if TYPE_CHECKING:
    from .Graph import Graph


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

    from .. import filtergraph as fg

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
        return (in_label >> fg.aformat(**fopts)) if len(fopts) else in_label

    afilt = [match_sample(*st) for st in streams.items()] >> fg.amerge(inputs=n_ain)

    return (afilt >> output_pad_label) if output_pad_label else afilt
