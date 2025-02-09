"""streams and map specifier handling module

parse & compose FFmpeg stream spec string from/to StreamSpec object

"""

from __future__ import annotations

from typing import get_args, Literal, TypedDict, Union, Tuple
from ._typing import MediaType, NotRequired

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

#################################


def parse_stream_spec(spec: str | int) -> StreamSpec:
    """Parse stream specifier string

    :param spec: stream specifier string. If int, it specifies the stream index.
    :return: stream spec dict

    The reverse of `stream_spec()`
    """

    if isinstance(spec, str):

        out: StreamSpec = {}
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
            if spec in get_args(StreamSpecMediaType):
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
        if media_type not in get_args(StreamSpecMediaType):
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
