"""streams and map specifier handling module

parse & compose FFmpeg stream spec string from/to StreamSpec object

"""

from __future__ import annotations

from typing import get_args, Literal, TypedDict, Union, Tuple
from ._typing import MediaType, NotRequired

import re

StreamSpecDictMediaType = Literal["v", "a", "s", "d", "t", "V"]
# libavformat/avformat.c:match_stream_specifier()


class StreamSpecDict_Options(TypedDict):
    media_type: NotRequired[MediaType]  # py3.11 NotRequired[MediaType]
    program_id: NotRequired[int]  # py3.11 NotRequired[int]
    group_index: NotRequired[int]  # py3.11 NotRequired[int]
    group_id: NotRequired[int]  # py3.11 NotRequired[int]
    stream_id: NotRequired[int]  # py3.11 NotRequired[int]


class StreamSpecDict_Index(StreamSpecDict_Options):
    index: int


class StreamSpecDict_Tag(StreamSpecDict_Options):
    tag: Union[str, Tuple[str, str]]


class StreamSpecDict_Usable(StreamSpecDict_Options):
    usable: bool


StreamSpecDict = Union[StreamSpecDict_Index, StreamSpecDict_Tag, StreamSpecDict_Usable]


class InputMapOptionDict(TypedDict):
    """Parsed dict of FFmpeg -map option when mapping input stream(s)"""

    negative: NotRequired[
        bool
    ]  # True to disables matching streams from already created mappings
    input_file_id: int  # index of the source index
    stream_specifier: NotRequired[str | StreamSpecDict]  # stream specifier
    view_specifier: NotRequired[str]  # view specifier
    optional: NotRequired[str]  # True if optional mapping


class GraphMapOptionDict(TypedDict):
    """Parsed dict of FFmpeg -map option, when mapping filtergraph output(s)"""

    linklabel: str | None  # link label of output of a filtergraph


MapOptionDict = Union[InputMapOptionDict, GraphMapOptionDict]
"""Parsed dict of FFmpeg -map option string"""

#################################


def parse_stream_spec(spec: str | int) -> StreamSpecDict:
    """Parse stream specifier string

    :param spec: stream specifier string. If int, it specifies the stream index.
    :return: stream spec dict

    The reverse of `stream_spec()`
    """

    if isinstance(spec, str):

        out: StreamSpecDict = {}
        spec_parts = spec.split(":")
        nspecs = len(spec_parts)
        i = 0  # current index

        def get_int(s, name):
            try:
                v = int(
                    s,
                    (
                        10
                        if s[0] != "0" and len(s) > 1
                        else 16 if s.startswith("0x") or s.startswith("0X") else 8
                    ),
                )
                assert v >= 0
            except Exception as e:
                raise ValueError(f"Invalid {name} ({s})") from e
            return v

        def get_id(i, name):

            try:
                s = spec_parts[i + 1]
            except IndexError as e:
                raise ValueError(f"Missing {name}") from e
            else:
                return get_int(s, name)

        # process the optional parts
        while i < nspecs:
            spec = spec_parts[i]
            # optional specifiers first
            if spec in get_args(StreamSpecDictMediaType):
                out["media_type"] = spec
                i += 1
            elif spec == "g":
                i += 1
                spec = spec_parts[i]
                if spec == "i":
                    out["group_id"] = get_id(i, "group_id")
                    i += 2
                elif spec.startswith("#"):
                    out["group_id"] = get_int(spec[1:], "group_id")
                    i += 1
                else:
                    out["group_index"] = get_int(spec, "group index")
                    i += 1
            elif spec == "p":
                out["program_id"] = get_id(i, "program_id")
                i += 2
            else:
                # final primary specifier
                if spec.startswith("#"):
                    out["stream_id"] = get_int(spec[1:], "stream_id")
                elif spec == "i":
                    out["stream_id"] = get_id(i, "stream_id")
                    i += 1
                elif spec == "u":
                    out["usable"] = True
                elif spec == "m":
                    try:
                        key, *value = spec_parts[i + 1 :]
                        assert len(value) <= 1
                    except (IndexError, AssertionError) as e:
                        raise ValueError(
                            f"Invalid metadata tag specifier: {':'.join(spec_parts[i:])}"
                        ) from e
                    else:
                        i = nspecs - 1
                    out["tag"] = (key, value[0]) if len(value) else key
                else:
                    try:
                        out["index"] = get_int(spec, "stream_index")
                    except ValueError as e:
                        raise ValueError(f"Unknown stream specifier: {spec}") from e
                break

        if i + 1 < nspecs:
            raise ValueError(f"Not all specifiers resolved: {':'.join(spec_parts[i:])}")

        return out

    if not (isinstance(spec, int) and spec >= 0):
        raise ValueError("Invalid stream specifier")
    return {"index": int(spec)}


def is_stream_spec(spec: str | int) -> bool:
    """True if valid stream specifier string

    :param spec: stream specifier string to be tested
    :param file_index: True if spec starts with a file index, None to allow with or without file_index defaults to False
    :return: True if valid stream specifier
    """
    try:
        parse_stream_spec(spec)
        return True
    except ValueError:
        return False


def stream_spec(
    index: int | None = None,
    media_type: MediaType | None = None,
    group_index: int | None = None,
    group_id: int | None = None,
    program_id: int | None = None,
    stream_id: int | None = None,
    tag: str | tuple[str, str] | None = None,
    usable: bool | None = None,
    file_index: int | None = None,
    no_join: bool = False,
) -> str:
    """Get stream specifier string

    :param index: Matches the stream with this index. If stream_index is used as
    an additional stream specifier, then it selects stream number stream_index
    from the matching streams. Stream numbering is based on the order of the
    streams as detected by libavformat except when a program ID is also
    specified. In this case it is based on the ordering of the streams in the
    program., defaults to None
    :param media_type: One of following: ’v’ or ’V’ for video, ’a’ for audio, ’s’ for
    subtitle, ’d’ for data, and ’t’ for attachments. ’v’ matches all video
    streams, ’V’ only matches video streams which are not attached pictures,
    video thumbnails or cover arts. If additional stream specifier is used, then
    it matches streams which both have this type and match the additional stream
    specifier. Otherwise, it matches all streams of the specified type, defaults
    to None
    :param group_index: Matches streams which are in the group with this group index.
                        Can be combined with other stream_specifiers, except for `group_index`.
    :param group_index: Matches streams which are in the group with this group id.
                        Can be combined with other stream_specifiers, except for `group_id`.
    :param program_id: Selects streams which are in the program with this id. If
    additional_stream_specifier is used, then it matches streams which both are
    part of the program and match the additional_stream_specifier, defaults to
    None
    :param stream_id: stream id given by the container (e.g. PID in MPEG-TS
    container), defaults to None
    :param tag: metadata tag key having the specified value. If value is not
    given, matches streams that contain the given tag with any value, defaults
    to None
    :param usable: streams with usable configuration, the codec must be defined
    and the essential information such as video dimension or audio sample rate
    must be present, defaults to None
    :param file_index: file index to be prepended if specified, defaults to None
    :param filter_output: True to append "out" to stream type, defaults to False
    :param no_join: True to return list of stream specifier elements, defaults to False
    :return: stream specifier string or empty string if all arguments are None

    Note matching by metadata will only work properly for input files.

    Note index, stream_id, tag, and usable are mutually exclusive. Only one of them
    can be specified.

    """

    if sum(v is not None for v in (index, stream_id, tag, usable)) > 1:
        raise ValueError('Only one of "index", "tag", or "usable" may be specified.')

    if sum(v is not None for v in (group_index, group_id)) > 1:
        raise ValueError('Only one of "group_index" or "group_id" may be specified.')

    spec = [] if file_index is None else [str(file_index)]

    if media_type is not None:
        if media_type not in get_args(StreamSpecDictMediaType):
            raise ValueError(f"Unknown {media_type=}.")
        spec.append(media_type)

    if group_index is not None:
        spec.append(f"g:{group_index}")
    elif group_id is not None:
        spec.append(f"g:#{group_id}")

    if program_id is not None:
        spec.append(f"p:{program_id}")

    if index is not None:
        spec.append(str(index))
    elif stream_id is not None:
        spec.append(f"#{stream_id}")
    elif tag is not None:
        spec.append(f"m:{tag}" if isinstance(tag, str) else f"m:{tag[0]}:{tag[1]}")
    elif usable is not None and usable:
        spec.append("u")

    return spec if no_join else ":".join(spec)


#################################


def parse_map_option(
    map: str, *, input_file_id: int | None = None, parse_stream: bool = False
) -> MapOptionDict:
    """parse the FFmpeg -map option str

    :param map: option string value
    :param input_file_id: if specified, auto-insert this id if a file id is missing in the given value,
                          defaults to None to error out if missing.
    :param parse_stream: True to also parse stream spec (if given)
    :return: dict containing the parsed parts of the option value, possibly containing the items:
        - negative: bool
        - input_file_id: int
        - stream_specifier: str
        - view_specifier: str
        - optional: bool
        - linklabel: str

    See the FFmpeg manual for the specification: https://ffmpeg.org/ffmpeg.html#Advanced-options
    """

    map = str(map)

    # -map [-]input_file_id[:stream_specifier][:view_specifier][:?] | [linklabel]
    if map[0] == "[" and map[-1] == "]":
        return {"linklabel": map}

    if input_file_id is not None:
        s1 = map.split(":", 1)
        if len(s1) == 1 or not s1[0].isdigit():
            map = f"{input_file_id}:{map}"

    m = re.match(r"(-)?(\d+)(\:[^?]+?)?(\?)?$", map)

    if not m:
        raise ValueError(f"Given str ({map}) is not a valid FFmpeg map option.")

    out = {"input_file_id": int(m[2])}
    if m[1]:
        out["negative"] = True
    if m[3]:
        s = re.search(r"\:(?:view|vidx|vpos)\:(?:[^:]+)$", m[3])
        if not s:
            out["stream_specifier"] = m[3][1:]
        elif s.start(0):
            out["stream_specifier"] = m[3][1 : s.start(0)]
            out["view_specifier"] = m[3][s.start(0) + 1 :]
        else:
            out["view_specifier"] = m[3][1:]
    if m[4]:
        out["optional"] = True

    if parse_stream and "stream_specifier" in out:
        out["stream_specifier"] = parse_stream_spec(out["stream_specifier"])

    return out


def is_map_option(spec: str, allow_missing_file_id: bool = False) -> bool:
    """True if valid map option string

    :param spec: map option string to be tested
    :param allow_missing_file_id: True to allow missing input file id
    :return: True if valid map option. The validity of stream_specifier is also tested.
    """

    try:
        parse_map_option(
            spec, input_file_id=0 if allow_missing_file_id else None, parse_stream=True
        )
    except Exception:
        return False
    return True


def map_option(
    input_file_id: int | None = None,
    linklabel: str | None = None,
    stream_specifier: str | StreamSpecDict | None = None,
    negative: bool | None = None,
    view_specifier: str | None = None,
    optional: bool | None = None,  # True if optional mapping
) -> str:
    """compose map option str

    :param input_file_id: index of the source index, defaults to None
    :param stream_specifier: stream specifier, defaults to None
    :param negative: True to disables matching streams from already created mappings, defaults to None
    :param view_specifier: view specifier, defaults to None
    :param optional: True if optional mapping, defaults to None
    :param linklabel: output label of a filtergraph
    :return: map option string

    Either input_file_id or linklabel must be non-`None`.
    """

    is_linklabel = input_file_id is None

    if (linklabel is None)==is_linklabel:
        raise ValueError('Either linklabel or input_file_id must be non-None')
    
    if is_linklabel:
        return linklabel
    
    map = str(input_file_id)
    if stream_specifier:
        if isinstance(stream_specifier, dict):
            stream_specifier = stream_spec(**stream_specifier)
        map = f"{map}:{stream_specifier}"
    if negative:
        map = f'-{map}'
    if view_specifier:
        map = f'{map}:{view_specifier}'
    if optional:
        map = f'{map}?'

    return map
