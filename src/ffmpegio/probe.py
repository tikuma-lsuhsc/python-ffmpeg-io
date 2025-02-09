from __future__ import annotations

from typing import BinaryIO, Any, Literal, Union, Tuple, Dict
from numbers import Number
from collections.abc import Sequence
from typing_extensions import Buffer, IO
from io import IOBase

import json, re
from fractions import Fraction
from functools import lru_cache

from .path import ffprobe, PIPE

# fmt:off
__all__ = ['full_details', 'format_basic', 'streams_basic',
'video_streams_basic', 'audio_streams_basic', 'query', 'frames']
# fmt:on

_re_ratio = re.compile(r"^(\d+)\:(\d+)$")


def _items_to_numeric(d):
    def try_conv(v):
        if v == "N/A":
            return None
        if isinstance(v, dict):
            return _items_to_numeric(v)
        if isinstance(v, list):
            return [_items_to_numeric(e) for e in v]

        try:
            return int(v)
        except ValueError:
            try:
                return float(v)
            except ValueError:

                # convert ratio to fraction ':' -> '/' if
                v = _re_ratio.sub(r"\1/\2", v)

                try:
                    return Fraction(v)
                except:
                    return v

    return {k: try_conv(v) for k, v in d.items()}


def _add_select_streams(args, stream_specifier):
    if stream_specifier:
        args.extend(["-select_streams", str(stream_specifier)])
    return args


def _compose_entries(entries: dict[str, bool | Sequence[str]]) -> str:
    arg = []
    for key, val in entries.items():
        if isinstance(val, Sequence):
            arg.append(f"{key}={','.join(val)}")
        elif val is not False:
            arg.append(key)
    return ":".join(arg)


IntervalSpec = Union[
    str,
    int,
    float,
    Tuple[Union[str, float], Union[str, int, float]],
    Dict[Literal["start", "start_offset", "end"], Union[str, float]],
]
""" Union type to specify the FFprobe read_intervals option

    FFprobe will seek to the interval starting point and will continue reading from that.
    An IntervalSpec argument can be specified in multiple ways to form the FFprobe read_intervals option:

    #. ``str`` - pass through the argument as-is to ffprobe
    #. ``int`` - read this numbers of packets to read from the beginning of the file
    #. ``float`` - read packets over this duration in seconds from the beginning of the file
    #. ``tuple[str|float, str|int|float]`` - sets (start, end) points
        * start: ``str`` = as-is, ``float`` = starting time in seconds
        * end: ``str`` = as-is, ``int`` = offset in # of packets, ``float`` = offset in seconds
    #. ``dict`` - specifies start and end points with the following keys:
        * ``'start'``        - (``str|float``) start time
        * ``'start_offset'`` - (``str|float``) start time offset from the previous read. Ignored if ``'start'`` is present.
        * ``'end'``          - (``str|float``) end time
        * ``'end_offset'``   - (``str|float|int``) end time offset from the start time. Ignored if ``'end'`` is present.
"""


def _add_read_intervals(args, intervals: IntervalSpec | Sequence[IntervalSpec]):
    """add -read_intervals option to ffprobe argumnets

    :param args: argument list under construction
    :type args: list[str]
    :param intervals: interval specification
    :type intervals: str, int, float, seq[str|float,str|int|float], dict, seq[dict]
    :return: same as args input
    :rtype: list[str]

    """

    # INTERVAL  ::= [START|+START_OFFSET][%[END|+END_OFFSET]]
    # INTERVALS ::= INTERVAL[,INTERVALS]
    if intervals is None:
        return args

    def compose_time(t, is_end_offset):
        if isinstance(t, str):
            return str
        elif isinstance(t, float):
            return str(t)
        elif is_end_offset and isinstance(t, int):
            return f"#{t}"
        else:
            raise ValueError("unknown interval endpoint specification")

    def compose_dict(intv):
        s = ""
        try:
            s = compose_time(intv["start"], False)
        except:
            try:
                s = "+" + compose_time(intv["start_offset"], False)
            except:
                pass
        try:
            s += "%" + compose_time(intv["end"], False)
        except:
            try:
                s += "%+" + compose_time(intv["end_offset"], True)
            except:
                pass
        return s

    if not isinstance(intervals, str):
        try:
            if isinstance(intervals, dict):
                intervals = compose_dict(intervals)
            else:
                try:
                    # try to set duration
                    intervals = f"%+{compose_time(intervals, True)}"
                except:
                    n = len(intervals)
                    if n and isinstance(intervals[0], dict):
                        # multiple intervals
                        intervals = ",".join([compose_dict(intv) for intv in intervals])
                    else:
                        # (start, +end)
                        assert len(intervals) == 2
                        start = compose_time(intervals[0], False)
                        end = compose_time(intervals[1], True)
                        intervals = f"{start}%+{end}"
        except:
            raise ValueError("unknown interval specification")

    args.extend(("-read_intervals", intervals))
    return args


def _exec(
    url: str | IO | Buffer,
    entries: str,
    sp_kwargs: tuple[tuple[str, Any]] | None = None,
    streams: str | int | None = None,
    intervals: IntervalSpec | Sequence[IntervalSpec] | None = None,
    count_frames: bool | None = False,
    count_packets: bool | None = False,
    keep_optional_fields: bool | None = None,
) -> dict[str, str]:
    """execute ffprobe and return stdout as dict"""

    sp_opts = {"stdout": PIPE, "stderr": PIPE}

    if sp_kwargs is not None:
        sp_opts = {**dict(sp_kwargs), **sp_opts}

    args = ["-hide_banner", "-of", "json", "-show_entries", entries]

    if streams is not None:
        _add_select_streams(args, streams)

    if intervals is not None:
        _add_read_intervals(args, intervals)

    if count_frames:
        args.append("-count_frames")
        # returns "nb_read_frames" item in each stream

    if count_packets:
        args.append("-count_packets")
        # returns "nb_read_packets" item in each stream

    if keep_optional_fields is not None:
        args.extend(
            ["-show_optional_fields", "always" if keep_optional_fields else "never"]
        )

    if isinstance(url, Buffer):
        sp_opts["input"] = url
        url = 'pipe:0'
    elif isinstance(url, IOBase):
        sp_opts["stdin"] = url
        url = 'pipe:0'
    else:
        url = str(url)

    args.append(url)

    # run ffprobe
    ret = ffprobe(args, **sp_opts)
    if ret.returncode != 0:
        raise Exception(f"ffprobe execution failed\n\n{ret.stderr.decode('utf8')}\n")

    # decode output JSON string
    return json.loads(ret.stdout)


@lru_cache()
def _exec_cached(*args, **kwargs) -> dict[str, str]:
    """execute ffprobe, return stdout as dict, and cache its output"""
    return _exec(*args, **kwargs)


def _run(
    url: str | BinaryIO | memoryview,
    entries: dict[str, bool | Sequence[str]],
    *args,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
    **kwargs,
) -> dict[str, str]:
    """execute ffprobe, return stdout as dict, and cache its output"""

    entries = _compose_entries(entries)
    if sp_kwargs is not None:
        sp_kwargs = tuple(sp_kwargs.items())
    return (
        _exec_cached(url, entries, sp_kwargs, *args, **kwargs)
        if cache_output
        else _exec(url, entries, sp_kwargs, *args, **kwargs)
    )


def full_details(
    url: str | BinaryIO | memoryview,
    show_format: bool | None = True,
    show_streams: bool | None = True,
    show_programs: bool | None = False,
    show_chapters: bool | None = False,
    select_streams: str | int | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> dict[str, str | Number | Fraction]:
    """Retrieve full details of a media file or stream

    :param url: URL of the media file/stream
    :type url: str or seekable file-like object or bytes-like object
    :param show_format: True to return format info, defaults to True
    :type show_format: bool, optional
    :param show_streams: True to return stream info, defaults to True
    :type show_streams: bool, optional
    :param show_programs: True to return program info, defaults to False
    :type show_programs: bool, optional
    :param show_chapters: True to return chapter info, defaults to False
    :type show_chapters: bool, optional
    :param select_streams: Stream specifier of the streams to get info of, defaults to None to retrieve all
    :type select_streams: str, int, optional
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :type keep_str_values: bool, optional
    :param cache_output: True to cache FFprobe output, defaults to False
    :type cache_output: bool, optional
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :type sp_kwargs: dict[str, Any], optional
    :return: media file information
    :rtype: dict[str, str|Number|Fraction]

    """

    modes = dict(
        format=show_format,
        stream=show_streams,
        program=show_programs,
        chapter=show_chapters,
    )

    results = _run(
        url, modes, select_streams, cache_output=cache_output, sp_kwargs=sp_kwargs
    )

    if not modes["stream"]:
        modes["streams"] = modes["stream"]
    for key, val in modes.items():
        if not val and key in results:
            del results[key]

    return results if keep_str_values else _items_to_numeric(results)


def _resolve_entries(info_type, entries, default_entries, default_dep_entries={}):

    query = set(default_entries)

    if entries:
        user_query = set(entries)
        bad_query = user_query - query
        if bad_query:
            raise Exception(f"invalid {info_type} entries: {', '.join(bad_query)}")
        query = user_query

    for dent, dee in default_dep_entries.items():
        if dent in query:
            query.discard(dent)
            query |= set(dee)

    return list(query)


def format_basic(
    url: str | BinaryIO | memoryview,
    entries: Sequence[str] | None = None,
    keep_optional_fields: bool | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> dict[str, str | Number | Fraction]:
    """Retrieve basic media format info

    :param url: URL of the media file/stream
    :type url: str or seekable file-like object or bytes-like object
    :param entries: specify to narrow which information entries to retrieve. Default to None, to return all entries
    :type entries: seq of str
    :param keep_optional_fields: True to return a missing optional field in the
                        returned dict with None or "N/A" (if keep_str_values
                        is True) as its value
    :type keep_optional_fields: bool, optional
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :type keep_str_values: bool, optional
    :param cache_output: True to cache FFprobe output, defaults to False
    :type cache_output: bool, optional
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :type sp_kwargs: dict[str, Any], optional
    :return: set of media format information.
    :rtype: dict


    Media Format Information Entries

    ===========  =====
    name         type
    ===========  =====
    filename     int
    nb_streams   str
    format_name  str
    start_time   float
    duration     float
    ===========  =====

    """

    default_entries = (
        "filename",
        "nb_streams",
        "format_name",
        "start_time",
        "duration",
    )

    return query(
        url,
        None,
        _resolve_entries("basic format", entries, default_entries),
        keep_optional_fields,
        keep_str_values,
        cache_output,
        sp_kwargs,
    )


def streams_basic(
    url: str | BinaryIO | memoryview,
    entries: Sequence[str] | None = None,
    keep_optional_fields: bool | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
    stream_spec: str | None = None,
) -> list[dict[str, str | Number | Fraction]]:
    """Retrieve basic info of media streams

    :param url: URL of the media file/stream
    :param entries: specify to narrow which stream entries to retrieve. Default to None, returning all entries
    :param keep_optional_fields: True to return a missing optional field in the
                        returned dict with None or "N/A" (if keep_str_values
                        is True) as its value
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :param cache_output: True to cache FFprobe output, defaults to False
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :param stream_spec: Specify stream specification, defaults to None
    :type stream_spec: str | None, optional
    :return: List of media stream information.

    Media Stream Information dict Entries

    ==========   ====
    name         type
    ==========   ====
    index        int
    codec_name   str
    codec_type   str
    ==========   ====

    """

    default_entries = ("index", "codec_name", "codec_type")

    return query(
        url,
        stream_spec or True,
        _resolve_entries("basic streams", entries, default_entries),
        keep_optional_fields,
        keep_str_values,
        cache_output,
        sp_kwargs,
    )


def video_streams_basic(
    url: str | BinaryIO | memoryview,
    index: int | None = None,
    entries: Sequence[str] | None = None,
    keep_optional_fields: bool | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> list[dict[str, str | Number | Fraction]]:
    """Retrieve basic info of video streams

    :param url: URL of the media file/stream
    :param index: video stream index. 0=first video stream. Defaults to None, which returns info of all video streams
    :param entries: specify to narrow which information entries to retrieve. Default to None, to return all entries
    :param keep_optional_fields: True to return a missing optional field in the
                        returned dict with None or "N/A" (if keep_str_values
                        is True) as its value
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :param cache_output: True to cache FFprobe output, defaults to False
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :return: List of video stream information.


    Video Stream Information Entries

    ====================  =========
    name                  type
    ====================  =========
    index                 int
    codec_name            str
    width                 int
    height                int
    sample_aspect_ratio   Fractions
    display_aspect_ratio  Fractions
    pix_fmt               str
    start_time            float
    duration              float
    frame_rate            Fractions
    nb_frames             int
    ====================  =========

    """

    default_entries = (
        "index",
        "codec_name",
        "width",
        "height",
        "sample_aspect_ratio",
        "display_aspect_ratio",
        "pix_fmt",
        "start_time",
        "duration",
        "frame_rate",
        "nb_frames",
    )

    durpara = ("duration_ts", "time_base")
    fspara = ("avg_frame_rate", "r_frame_rate")
    default_dep_entries = dict(
        start_time=("start_pts", "time_base"),
        duration=durpara,
        frame_rate=fspara,
        nb_frames=("nb_frames", *durpara, *fspara),
    )

    results = query(
        url,
        "v" if index is None else f"v:{index}",
        _resolve_entries("basic video", entries, default_entries, default_dep_entries),
        keep_optional_fields,
        keep_str_values,
        cache_output,
        sp_kwargs,
    )

    def adjust(res):
        tb = res.pop("time_base", 1)
        if "start_pts" in res:
            res["start_time"] = res.pop("start_pts", 0) * tb

        duration = res.pop("duration_ts", 0) * tb
        if not entries or "duration" in entries:
            res["duration"] = duration

        fsa = res.pop("avg_frame_rate", None)
        fsr = res.pop("r_frame_rate", 0)
        frame_rate = Fraction(fsa if fsa and fsa != "0/0" else fsr)
        if not entries or "frame_rate" in entries:
            res["frame_rate"] = frame_rate

        if "nb_frames" not in res and entries and "nb_frames" in entries:
            res["nb_frames"] = int(round(duration * frame_rate))

        return res

    return (
        results
        if keep_str_values
        else (
            adjust(results)
            if isinstance(results, dict)
            else [r if keep_str_values else adjust(r) for r in results]
        )
    )


def audio_streams_basic(
    url: str | BinaryIO | memoryview,
    index: int | None = None,
    entries: Sequence[str] | None = None,
    keep_optional_fields: bool | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> list[dict[str, str | Number | Fraction]]:
    """Retrieve basic info of audio streams

    :param url: URL of the media file/stream
    :param index: audio stream index. 0=first audio stream. Defaults to None, which returns info of all audio streams
    :param entries: specify to narrow which information entries to retrieve. Default to None, to return all entries
    :param keep_optional_fields: True to return a missing optional field in the
                        returned dict with None or "N/A" (if keep_str_values
                        is True) as its value
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :param cache_output: True to cache FFprobe output, defaults to False
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :return: List of audio stream information.

    Audio Stream Information Entries

        ==============  =====
        name            type
        ==============  =====
        index           int
        codec_name      str
        sample_fmt      str
        sample_rate     int
        channels        int
        channel_layout  str
        start_time      float
        duration        float
        nb_samples      int
        ==============  =====

    """

    default_entries = (
        "index",
        "codec_name",
        "sample_fmt",
        "sample_rate",
        "channels",
        "channel_layout",
        "start_time",
        "duration",
        "nb_samples",
    )

    durpara = ("duration_ts", "time_base")
    default_dep_entries = dict(
        start_time=("start_pts", "time_base"),
        duration=durpara,
        nb_samples=("sample_rate", *durpara),
    )

    results = query(
        url,
        "a" if index is None else f"a:{index}",
        _resolve_entries("basic audio", entries, default_entries, default_dep_entries),
        keep_optional_fields,
        keep_str_values,
        cache_output,
        sp_kwargs,
    )

    def adjust(res):
        tb = res.pop("time_base", 1)
        start_pts = res.pop("start_pts", 0)
        duration_ts = res.pop("duration_ts", 0)

        if not entries or "start_time" in entries:
            res["start_time"] = start_pts * tb
        if not entries or "duration" in entries:
            res["duration"] = duration_ts * tb
        if (not entries or "nb_samples" in entries) and "nb_samples" not in res:
            res["nb_samples"] = int(round(duration_ts * tb * res["sample_rate"]))
            if entries and "sample_rate" not in entries:
                res.pop("sample_rate")

        return res

    return (
        results
        if keep_str_values
        else (
            adjust(results)
            if isinstance(results, dict)
            else [adjust(r) for r in results]
        )
    )


def query(
    url: str | BinaryIO | memoryview,
    streams: str | int | bool | None = None,
    fields: Sequence[str] | None = None,
    keep_optional_fields: bool | None = None,
    keep_str_values: bool | None = False,
    cache_output: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> (
    dict[str, Any]
    | Sequence[dict[str, Any]]
    | list[dict[str, Any] | Sequence[dict[str, Any]]]
):
    """Query specific fields of media format or stream

    :param url: URL of the media file/stream
    :type url: str or seekable file-like object or bytes-like object
    :param streams: stream specifier, defaults to None to get format
    :type streams: str, int, bool, optional
    :param fields: list of format/stream fields to retrieve, defaults to None (all fields)
    :type fields: sequence of str, optional
    :param keep_optional_fields: True to return a missing optional field in the
                        returned dict with None or "N/A" (if keep_str_values
                        is True) as its value
    :type keep_optional_fields: bool, optional
    :param keep_str_values: True to keep all field values as str,
                            defaults to False to convert numeric values
    :type keep_str_values: bool, optional
    :param cache_output: True to cache FFprobe output, defaults to False
    :type cache_output: bool, optional
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :type sp_kwargs: dict[str, Any], optional
    :return: field name-value dict. If streams argument is given but does not specify
             index, a list of dict is returned instead
    :rtype: dict or list or dict

    Note: Unlike :py:func:`video_stream_basic()` and :py:func:`audio_stream_basic()`,
          :py:func:`query()` does not process ffprobe output except for the conversion
          from str to float/int.

    """

    get_stream = streams is not None and streams is not False
    if isinstance(streams, bool):
        streams = None

    info = _run(
        url,
        {"stream" if get_stream else "format": fields},
        streams,
        sp_kwargs=sp_kwargs,
        cache_output=cache_output,
        keep_optional_fields=keep_optional_fields,
    )

    if not keep_str_values:
        info = _items_to_numeric(info)

    info = info["streams" if get_stream else "format"]

    if get_stream:
        if len(info) == 0:
            raise ValueError(f"Unknown or invalid stream specifier: {streams}")

        # if isinstance(streams, (str, int)) and "index" in parse_stream_spec(streams):
        #     # return dict only if a specific stream requested
        #     info = info[0]

    return info


def _audio_info(
    url: str | BinaryIO | memoryview,
    stream: str | None,
    sp_kwargs: dict[str, Any] | None,
) -> tuple[int | None, str | None, int | None]:
    "returns (sample_rate, sample_fmt, channels) of the specified url/stream"
    fields = ["sample_rate", "sample_fmt", "channels"]
    q = query(
        url,
        "a:0" if stream is None else stream,
        fields,
        True,
        False,
        True,
        sp_kwargs,
    )[0]
    return tuple(q[f] for f in fields)


def _video_info(
    url: str | BinaryIO | memoryview,
    stream: str | None,
    sp_kwargs: dict[str, Any] | None,
) -> tuple[
    str | None,
    int | None,
    int | None,
    Fraction | Literal["0/0"] | None,
    Fraction | None,
]:
    "returns (pix_fmt, width, height, avg_frame_rate, r_frame_rate) of the specified url/stream"

    fields = ["pix_fmt", "width", "height", "avg_frame_rate", "r_frame_rate"]
    q = query(
        url,
        "v:0" if stream is None else stream,
        fields,
        True,
        False,
        True,
        sp_kwargs,
    )[0]
    return tuple(q[f] for f in fields)


def frames(
    url: str | BinaryIO | memoryview,
    entries: Sequence[str] | None = None,
    streams: str | int | None = None,
    intervals: IntervalSpec | Sequence[IntervalSpec] | None = None,
    accurate_time: bool | None = False,
    sp_kwargs: dict[str, Any] | None = None,
) -> list[dict] | list[str | int | float]:
    """get frame information

    :param url: URL of the media file/stream
    :type url: str or seekable file-like object or bytes-like object
    :param entries: names of frame attributes, defaults to None (get all attributes)
    :type entries: str or seq[str], optional
    :param stream: stream specifier of the stream to retrieve the data of, defaults to None to get all streams
    :type stream: str or int, optional
    :param intervals: time intervals to retrieve the data, see below for the details, defaults to None (get all)
    :type intervals: :py:class:`IntervalSpec` or Sequence[:py:class:`IntervalSpec`], optional
    :param accurate_time: True to return all '\*_time' attributes to be computed from associated timestamps and
                          stream timebase, defaults to False (= us accuracy)
    :param accurate_time: bool, optional
    :param sp_kwargs: Additional keyword arguments for :py:func:`subprocess.run`,
                      default to None
    :type sp_kwargs: dict[str, Any], optional
    :return: frame information. list of dictionary if entries is None or a sequence; list of the selected entry
             if entries is str (i.e., a single entry)
    :rtype: list[dict] or list[str|int|float]

    """

    is_single = isinstance(entries, str)
    if is_single:
        entry = entries
        entries = [entries]

    pick_entries = entries is not None

    if accurate_time:
        has_time = not pick_entries
        if pick_entries:
            time_entries = []
            other_entries = set(("stream_index",))
            for e in entries:
                if e.endswith("_time"):
                    has_time = True
                    time_entries.append(e)
                    other_entries.add(e[:-5])
                else:
                    other_entries.add(e)
            if has_time:
                orig_entries = entries
                entries = other_entries

    entries = {"frame": (entries is None) or entries}
    if accurate_time and has_time:
        entries["stream"] = ["index", "time_base"]

    res = _exec(
        url,
        _compose_entries(entries),
        sp_kwargs and tuple(sp_kwargs.items()),
        streams=streams,
        intervals=intervals,
    )

    out = [_items_to_numeric(d) for d in res["frames"]]

    if len(out) == 0:
        return out

    if pick_entries and "side_data_list" not in entries["frame"]:
        # make sure side_data_list is not included
        for d in out:
            try:
                del d["side_data_list"]
            except KeyError:
                pass

    if accurate_time and has_time:

        time_bases = {d["index"]: Fraction(d["time_base"]) for d in res["streams"]}

        if not pick_entries:
            time_entries = [e for e in out[0].keys() if e.endswith("_time")]

        ts_entries = [e[:-5] for e in time_entries]

        for d in out:
            tb = time_bases[d["stream_index"]]
            for e, e_ts in zip(time_entries, ts_entries):
                d[e] = d[e_ts] * tb
                if pick_entries and e_ts not in orig_entries:
                    del d[e_ts]

    try:
        return [d[entry] for d in out] if is_single else out
    except:
        raise ValueError(f"invalid frame attribute: {entry}")
