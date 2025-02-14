from __future__ import annotations

from collections.abc import Generator, Sequence
import re
from functools import partial
from itertools import chain

from ..caps import filters as list_filters, filter_info, layouts, FilterInfo
from . import utils as filter_utils

from .. import filtergraph as fgb
from ..stream_spec import parse_stream_spec

from .typing import PAD_INDEX, Literal
from .exceptions import *

__all__ = ["Filter"]


class Filter(fgb.abc.FilterGraphObject, tuple):
    """FFmpeg filter definition immutable class

    :param filter_spec: _description_
    :type filter_spec: _type_
    :param filter_id: _description_, defaults to None
    :type filter_id: _type_, optional
    :param \\*opts: filter option values assigned in the order options are
                    declared
    :type \\*opts: dict, optional
    :param \\**kwopts: filter options in key=value pairs
    :type \\**kwopts: dict, optional

    """

    class Error(FFmpegioError):
        pass

    class InvalidName(Error):
        def __init__(self, name):
            from .. import path

            super().__init__(
                f"Filter {name} is not defined in FFmpeg (v{path.FFMPEG_VER}).\n"
            )

    class InvalidOption(Error):
        pass

    class Unsupported(Error):
        def __init__(self, name, feature) -> None:
            super().__init__(f"{feature} not yet supported feature for {name} filter.")

    _info: dict[str, FilterInfo] = {}

    @staticmethod
    def _get_info(name: str) -> FilterInfo:
        try:
            info = Filter._info[name]
        except KeyError:
            try:
                info = Filter._info[name] = list_filters()[name]
            except:
                raise Filter.InvalidName(name)
        return info

    def __new__(self, filter_spec, *args, filter_id=None, **kwargs):
        """_summary_"""
        proto = []
        if isinstance(filter_spec, Filter):
            if filter_spec.id and filter_id is not None:  # new id
                proto.append((filter_spec.name, filter_id))
                proto.extend(filter_spec[1:])
            else:
                proto.extend(filter_spec)
        else:
            # parse if str given
            if isinstance(filter_spec, str):
                filter_spec = filter_utils.parse_filter(filter_spec)

            if not (isinstance(filter_spec, Sequence) and len(filter_spec)):
                raise ValueError("filter_spec must be a non-empty sequence.")
            name, *opts = filter_spec
            if isinstance(name, str):
                self._get_info(name)
                proto.append((name, id) if isinstance(id, str) else name)
            elif not (
                isinstance(name, Sequence)
                and len(name) != 2
                and all((isinstance(i, str) for i in name))
            ):
                raise ValueError(
                    "filter_spec[0] must be a str or 2-element str sequence."
                )
            else:
                # name + id: re-id if id arg given
                self._get_info(name[0])
                proto.append(tuple(name) if filter_id is None else (name[0], filter_id))

            proto.extend(opts)

        # create named options dict
        proto_dict = proto.pop() if isinstance(proto[-1], dict) else {}

        # change ordered options if non-None value is given
        nord = len(proto) - 1  # # of ordered options
        for i, o in enumerate(args[:nord]):
            if o is not None:
                proto[i] = o

        # add additional ordered options if present
        proto.extend(args[nord:])

        # update named options
        if len(kwargs):
            proto_dict.update(kwargs)

        # validate named option keys to be str
        for k in proto_dict:
            if not isinstance(k, str):
                raise ValueError(
                    "All keys of the named option dict must be of type str."
                )

        # add the named option dict to the prototype list
        if len(proto_dict):
            proto.append(proto_dict)

        # create the final tuple
        return tuple.__new__(Filter, proto)

    def __getitem__(self, key):
        value = tuple.__getitem__(self, key)

        if isinstance(value, dict):
            value = {**value}
        if isinstance(value, tuple):
            if isinstance(value[-1], dict):
                value = tuple((*value[:-1], {**value[-1]}))
            elif isinstance(value[0], dict):
                value = tuple(({**value[-1]}, *value[1:]))
        return value

    def compose(
        self,
        show_unconnected_inputs: bool = False,
        show_unconnected_outputs: bool = False,
    ):
        """compose filtergraph

        :param show_unconnected_inputs: display [UNC#] on all unconnected input pads, defaults to True
        :param show_unconnected_outputs: display [UNC#] on all unconnected output pads, defaults to True
        """

        return (
            fgb.Graph(self).compose(
                show_unconnected_inputs, show_unconnected_outputs
            )
            if show_unconnected_inputs or show_unconnected_outputs
            else filter_utils.compose_filter(*self)
        )

    def __repr__(self):
        type_ = type(self)
        return f"""<{type_.__module__}.{type_.__qualname__} object at {hex(id(self))}>
    FFmpeg expression: \"{self.compose(True,True)}\"
    Number of inputs: {self.get_num_inputs()}
    Number of outputs: {self.get_num_outputs()}
"""

    @property
    def name(self):
        name = self[0]
        return name if isinstance(name, str) else name[0]

    @property
    def fullname(self):
        name = self[0]
        return name if isinstance(name, str) else f"{name[0]}@{name[1]}"

    @property
    def id(self):
        name = self[0]
        return None if isinstance(name, str) else name[1]

    @property
    def ordered_options(self):
        opts = self[1:]
        return opts[:-1] if isinstance(opts[-1], dict) else opts

    @property
    def named_options(self):
        opts = self[-1]
        return opts if isinstance(opts, dict) else {}

    @property
    def info(self):
        try:
            return filter_info(self.name)
        except:
            raise Filter.InvalidName(self.name)

    def get_pad_media_type(
        self, port: Literal["input", "output"], pad_id: int
    ) -> Literal["audio", "video"]:
        try:
            port = (
                "inputs"
                if "inputs".startswith(port)
                else "outputs" if "outputs".startswith(port) else None
            )
            assert port is not None
        except:
            raise ValueError(
                f"{port} is an invalid filter port type. Must be either 'input' or 'output'."
            )

        port_info = getattr(self.info, port)

        if port_info is None:
            # filters with homogeneous multiple in/out
            # fmt:off
            pure_video = {
                "inputs": [
                    "bm3d", "decimate", "fieldmatch", "hstack", "interleave", "mergeplanes",
                    "mix", "premultiply", "signature", "streamselect", "unpremultiply",
                    "vstack", "xmedian", "xstack",
                ],
                "outputs": [
                    "alphaextract", "extractplanes", "select", "split", "streamselect",
                ],
            }
            pure_audio = {
                "inputs": [
                    "afir", "ainterleave", "amerge", "amix", "astreamselect", "headphone", "join", "ladspa",
                ],
                "outputs": [
                    "acrossover", "aselect", "asplit", "astreamselect", "channelsplit",
                ],
            }
            # fmt:on

            if self.name in pure_video[port]:
                return "video"
            if self.name in pure_audio[port]:
                return "audio"

            if self.name == "concat":
                n = self.get_option_value("n")
                v = self.get_option_value("v")
                a = self.get_option_value("a")
                return (
                    ("video" if pad_id % n < v else "audio")
                    if port != "outputs"
                    else ("video" if pad_id < v else "audio")
                )

            # multiple pads possible if streams option set
            if self.name in ("movie", "amovie"):
                val = self.get_option_value("streams")
                if val is None:
                    return "video" if self.name == "movie" else "audio"

                spec = val.split("+")[pad_id]
                return (
                    "video"
                    if spec == "dv"
                    else (
                        "audio"
                        if spec == "da"
                        else {"v": "video", "a": "audio", None: None}[
                            parse_stream_spec(spec).get("media_type", None)
                        ]
                    )
                )

            # 2nd pad for audio visualization stream
            vis_mode = ["afir", "aiir", "anequalizer", "ebur128", "aphasemeter"]
            if port == "outputs" and self.name in vis_mode:
                return "video" if pad_id else "audio"

            raise Filter.Unsupported(self.name, "dynamic media type resolution")

        try:
            pad_info = port_info[pad_id]
            return pad_info["type"]
        except:
            raise ValueError(
                f"{pad_id} is an invalid pad_id as an {port[:-1]} pad of {self.name} filter."
            )

    def get_option_value(self, option_name):

        # first check the named options as-is
        named_opts = self.named_options
        try:
            return named_opts[option_name]
        except:
            pass

        # get the option info
        i, opt_info = next(
            (
                (i, o)
                for i, o in enumerate(self.info.options)
                if o.name == option_name or option_name in o.aliases
            ),
            (None, None),
        )
        if i is None:
            raise Filter.InvalidOption(
                f"Invalid option name ({option_name}) for {self.name} filter"
            )

        try:
            # try full name first
            return named_opts[opt_info.name]
        except:
            # try alias name next
            for a in opt_info.aliases:
                try:
                    return named_opts[a]
                except:
                    pass

            # try from ordered options next
            try:
                return self.ordered_options[i]
            except:
                # if nothing fits, use the default value (maybe undefined/None)
                return opt_info.default

    def get_num_inputs(self):
        """get the number of input pads of the filter
        :return: number of input pads
        :rtype: int
        """
        name = self.name
        if not isinstance(name, str):
            # name@id
            name = name[0]

        try:
            nin = self._info[name].num_inputs
        except:
            raise Filter.InvalidName(name)
        if nin is not None:  # fixed number
            return nin

        def _inplace():
            return 1 if self.get_option_value("inplace") else 2

        def _headphone():
            if self.get_option_value("hrir") == "multich":
                return 2
            map = self.get_option_value("map")
            return (
                len(re.split(r"\s*\|\s*", map)) + 1
                if isinstance(map, str)
                else len(map) + 1
            )

        def _mergeplanes():
            map = self.get_option_value("mapping")
            if not isinstance(map, int):
                map = int(map, 16 if map.startswith("0x") else 10)

            return int(max(f"{map:08x}"[::2])) + 1

        def _concat():
            return self.get_option_value("n") * (
                self.get_option_value("v") + self.get_option_value("a")
            )

        option_name, inc = {
            "afir": ("nbirs", 1),
            "concat": (None, _concat),
            "decimate": ("ppsrc", 1),
            "fieldmatch": ("ppsrc", 1),
            "headphone": (None, _headphone),
            "interleave": ("nb_inputs", 0),
            "limitdiff": ("reference", 1),
            "mergeplanes": (None, _mergeplanes),
            "premultiply": (None, _inplace),
            "unpremultiply": (None, _inplace),
            "signature": ("nb_inputs", 0),
            # "astreamselect": ("inputs", 0),
            # "bm3d": ("inputs", 0),
            # "hstack": ("inputs", 0),
            # "mix": ("inputs", 0),
            # "streamselect": ("inputs", 0),
            # "vstack": ("inputs", 0),
            # "xmedian": ("inputs", 0),
            # "xstack": ("inputs", 0),
        }.get(name, ("inputs", 0))

        return (
            int(self.get_option_value(option_name)) + inc
            if isinstance(option_name, str)
            else inc()
        )

    def get_num_outputs(self):
        """get the number of output pads of the filter
        :return: number of output pads
        :rtype: int
        """
        name = self.name

        try:
            nout = self._info[name].num_outputs
        except:
            raise Filter.InvalidName(name)
        if nout is not None:  # arbitrary number allowed
            return nout

        def _concat():
            return int(self.get_option_value("a")) + int(self.get_option_value("v"))

        def _list_var(opt, sep, inc):
            v = self.get_option_value(opt)
            return (
                1
                if v is None
                else (
                    len(v)
                    if sep == r"\|" and not isinstance(v, str)
                    else len(re.split(rf"\s*{sep}\s*", v))
                )
            ) + inc

        def _channelsplit():
            layout = self.get_option_value("channel_layout")
            channels = self.get_option_value("channels")
            return len(
                re.split(
                    rf"\s*\+\s*",
                    layouts()["layouts"][layout] if channels == "all" else channels,
                )
            )

        # fmt:off
        option_name, inc = {
            "afir": ("response", 1),  # +video stream
            "aiir": ("response", 1),  # +video stream
            "anequalizer": ("curves", 1),
            "ebur128": ("video", 1),
            "aphasemeter": ("video", 1),
            "acrossover": ('split',partial( _list_var,"split", " ", 1)),  # split option (space-separated)
            "asegment": ("timestamps", partial( _list_var,"timestamps", r"\|", 1)),
            "segment": ("timestamps", partial( _list_var,"timestamps", r"\|", 1)),
            "astreamselect": ("map", partial( _list_var,"map", " ", 0)),  # parse map?
            "streamselect": ("map", partial( _list_var,"map", " ", 0)),  # parse map?
            "extractplanes": ("planes", partial( _list_var,"planes", r"\+", 0)),  # parse planes
            "amovie": ("streams",partial( _list_var,"streams", r"\+", 0)),
            "movie": ("streams",partial( _list_var,"streams", r"\+", 0)),
            "channelsplit": (('channel_layout', 'channels'),_channelsplit),  # parse channel_layout/channels
            "concat": (('a', 'v'), _concat),  # sum a and v
            # "aselect": (("output", "n"), 0),  # must resolve alias...
            # "asplit": ("outputs", 0),
            # "select": (("output", "n"), 0),
            # "split": ("outputs", 0),
        }.get(name, ("outputs", 0))
        # fmt:on

        return (
            int(self.get_option_value(option_name)) + inc
            if isinstance(inc, int)
            else inc()
        )

    def normalize_pad_index(self, input: bool, index: PAD_INDEX) -> PAD_INDEX:
        """normalize pad index.

        Returns three-element pad index with non-negative indices.

        :param input: True to check the input pad index, False the output.
        :param index: pad index to be normalized
        :return: normalized pad index
        """

        if isinstance(index, int):
            index = (0, 0, index)
        elif len(index) == 1:
            index = (0, 0, *index)
        elif len(index) == 2:
            index = (0, *index)

        if index[-1] < 0:
            numpads = self.get_num_inputs() if input else self.get_num_outputs()
            index = (*index[-3:-1], numpads + index[-1])

        return index

    def get_num_filters(self, chain: int) -> int:
        """get the number of filters of the specfied chain

        :param chain: id of the chain
        """

        if chain:
            raise ValueError(f"{chain=} is invalid. Filter object only has 1 chain.")
        return 1

    def get_num_chains(self) -> int:
        """get the number of chains"""
        return 1

    def add_label(
        self,
        label: str,
        inpad: PAD_INDEX | Sequence[PAD_INDEX] = None,
        outpad: PAD_INDEX = None,
        force: bool = None,
    ) -> fgb.Graph:
        """label a filter pad

        :param label: name of the new label. Square brackets are optional.
        :param inpad: input filter pad index or a sequence of pads, defaults to None
        :param outpad: output filter pad index, defaults to None
        :param force: True to delete existing labels, defaults to None
        :return: actual label name

        Only one of inpad and outpad argument must be given.

        If given label already exists, no new label will be created.

        If inpad indices are given, the label must be an input stream specifier.

        If label has a trailing number, the number will be dropped and replaced with an
        internally assigned label number.

        """

        # must convert to FilterGraph as it's the only object with labels
        fg = fgb.Graph([[self]])
        return fg.add_label(label, inpad, outpad, force)

    def _iter_pads(
        self,
        n: int,
        pad: int | None,
        filter: Literal[0] | None,
        chain: Literal[0] | None,
        exclude_chainable: bool,
        chainable_first: bool,
        chainable_only: bool,
    ) -> Generator[tuple[PAD_INDEX, Filter]]:
        """Iterate over input pads of the filter

        :param n: number of pads
        :param pad: pad id
        :param filter: filter index
        :param chain: chain index
        :param exclude_chainable: True to leave out the last pads
        :param chainable_first: True to yield the last pad first then the rest
        :yield: filter pad index, link label, filter object
        """

        if not n:
            # takes no input, nothing to iterate
            return

        if (isinstance(filter, int) and filter != 0) or (
            isinstance(chain, int) and chain != 0
        ):
            # Filter alone can have no connections so yields no pad
            raise FiltergraphInvalidIndex(f"Invalid {filter=} or {chain=} id")

        if pad is not None:
            if pad < 0:  # resolve negative pad index
                pad += n
            if pad < 0 or pad >= (n - 1 if exclude_chainable else n):
                raise FiltergraphInvalidIndex(f"Invalid {pad=} id")

        if chainable_only:
            if pad is None:
                pad = n - 1

            if pad != n - 1:
                raise FiltergraphInvalidIndex(f"Invalid {pad=} is not chainable pad")

            if not exclude_chainable and pad == n - 1:
                yield (pad,), self, None

        elif pad is None:
            if chainable_first or exclude_chainable:
                n = n - 1
                if chainable_first and not exclude_chainable:
                    yield (n,), self, None
            for j in range(n):
                yield (j,), self, None
        else:
            yield (pad,), self, None

    def iter_input_pads(
        self,
        pad: int | None = None,
        filter: Literal[0] | None = None,
        chain: Literal[0] | None = None,
        *,
        exclude_chainable: bool = False,
        chainable_first: bool = False,
        include_connected: bool = False,
        unlabeled_only: bool = False,
        chainable_only: bool = False,
        full_pad_index: bool = False,
    ) -> Generator[tuple[PAD_INDEX, Filter, None]]:
        """Iterate over input pads of the filter

        :param pad: pad id, defaults to None
        :param filter: filter index, defaults to None
        :param chain: chain index, defaults to None
        :param exclude_chainable: True to leave out the last input pads, defaults to False (all avail pads)
        :param chainable_first: True to yield the last input first then the rest, defaults to False
        :param include_connected: True to include pads connected to input streams, defaults to False
        :param unlabeled_only: True to leave out named inputs, defaults to False to return all inputs
        :param chainable_only: True to only iterate chainable pads, defaults to False to return all inputs
        :param full_pad_index: True to return 3-element index
        :yield: filter pad index, link label, filter object, output pad index of connected filter if connected
        """

        for index, filter, other_index in self._iter_pads(
            self.get_num_inputs(),
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            chainable_only,
        ):
            yield (
                ((0, 0, *index), filter, other_index)
                if full_pad_index
                else (index, filter, other_index)
            )

    def iter_output_pads(
        self,
        pad: int | None = None,
        filter: Literal[0] | None = None,
        chain: Literal[0] | None = None,
        *,
        exclude_chainable: bool = False,
        chainable_first: bool = False,
        include_connected: bool = False,
        unlabeled_only: bool = False,
        chainable_only: bool = False,
        full_pad_index: bool = False,
    ) -> Generator[tuple[PAD_INDEX, Filter, PAD_INDEX | None]]:
        """Iterate over output pads of the filter

        :param pad: pad id, defaults to None
        :param filter: filter index, defaults to None
        :param chain: chain index, defaults to None
        :param exclude_chainable: True to leave out the last output pads, defaults to False (all avail pads)
        :param chainable_first: True to yield the last output first then the rest, defaults to False
        :param include_connected: True to include pads connected to output streams, defaults to False
        :param unlabeled_only: True to leave out named outputs, defaults to False to return only all outputs
        :param chainable_only: True to only iterate chainable pads, defaults to False to return all outputs
        :param full_pad_index: True to return 3-element index
        :yield: filter pad index, link label, filter object, output pad index of connected filter if connected
        """

        for index, filter, other_index in self._iter_pads(
            self.get_num_outputs(),
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            chainable_only,
        ):
            yield (
                ((0, 0, *index), filter, other_index)
                if full_pad_index
                else (index, filter, other_index)
            )

    def iter_chains(
        self,
        skip_if_no_input: bool = False,
        skip_if_no_output: bool = False,
        chainable_only: bool = False,
    ) -> Generator[tuple[int, fgb.Chain]]:
        """iterate over chains of the filtergraphobject

        :param skip_if_no_input: True to skip chains without available input pads, defaults to False
        :param skip_if_no_output: True to skip chains without available output pads, defaults to False
        :param chainable_only: True to further restrict ``skip_if_no_input`` and ``skip_if_no_input``
                               arguments to require chainable input or output, defaults to False to
                               allow any input/output
        :yield: chain id and chain object
        """

        if (not skip_if_no_input or self.get_num_inputs()) and (
            not skip_if_no_output or self.get_num_outputs()
        ):
            yield (0, fgb.Chain([self]))

    def _connect(
        self,
        right: fgb.abc.FilterGraphObject,
        fwd_links: list[tuple[PAD_INDEX, PAD_INDEX]],
        bwd_links: list[tuple[PAD_INDEX, PAD_INDEX]],
        chain_siso: bool = True,
        replace_sws_flags: bool | None = None,
    ) -> fgb.Graph:
        """combine another filtergraph object and make downstream connections (worker)

        :param right: other filtergraph
        :param fwd_links: a list of tuples, pairing self's output pad and right's ipnut pad
        :param bwd_links: a list of tuples, pairing right's output pad and self's ipnut pad
        :param to_right: input pad ids or labels of the `right` fg
        :param chain_siso: True to chain the single-input single-output connection, default: True
        :param replace_sws_flags: True to use `right` sws_flags if present,
                                  False to drop `right` sws_flags,
                                  None to throw an exception (default)
        :return: new filtergraph object

        * link labels may be auto-renamed if there is a conflict

        """

        if not isinstance(right, fgb.Filter):
            # right is more complex filtergraph object
            return right._rconnect(
                self, fwd_links, bwd_links, chain_siso, replace_sws_flags
            )

        if chain_siso and self.get_num_outputs() == 1 and right.get_num_inputs() == 1:
            return fgb.Chain([self, right])

        # create iterators to organize the links in (input, output) of the combined graph
        it_fwd = (((1, 0, r[2]), l) for (l, r) in fwd_links)
        it_bwd = ((l, (1, 0, r[2])) for (r, l) in bwd_links)

        return fgb.Graph(
            [[self], [right]],
            {i: link for i, link in enumerate(chain(it_fwd, it_bwd))},
        )

    def _rconnect(
        self,
        left: fgb.abc.FilterGraphObject,
        fwd_links: list[tuple[PAD_INDEX, PAD_INDEX]],
        bwd_links: list[tuple[PAD_INDEX, PAD_INDEX]],
        chain_siso: bool = True,
        replace_sws_flags: bool | None = None,
    ) -> fgb.Graph:
        """combine another filtergraph object and make upstream connections (worker)

        :param right: other filtergraph
        :param fwd_links: a list of tuples, pairing left's output pad and self's ipnut pad
        :param bwd_links: a list of tuples, pairing self's output pad and left's ipnut pad
        :param chain_siso: True to chain the single-input single-output connection, default: True
        :param replace_sws_flags: True to use `right` sws_flags if present,
                                  False to drop `right` sws_flags,
                                  None to throw an exception (default)
        :return: new filtergraph object

        * link labels may be auto-renamed if there is a conflict

        """

        if not isinstance(left, fgb.Filter):
            # left is more complex filtergraph object
            return left._connect(
                self, fwd_links, bwd_links, chain_siso, replace_sws_flags
            )

        if chain_siso and left.get_num_outputs() == 1 and self.get_num_inputs() == 1:
            return fgb.Chain([left, self])

        # create iterators to organize the links in (input, output) of the combined graph
        it_fwd = (((1, 0, r[2]), l) for (l, r) in fwd_links)
        it_bwd = ((l, (1, 0, r[2])) for (r, l) in bwd_links)

        return fgb.Graph(
            [[left], [self]],
            {i: link for i, link in enumerate(chain(it_fwd, it_bwd))},
        )

    def _stack(
        self,
        other: fgb.abc.FilterGraphObject,
        auto_link: bool = False,
        replace_sws_flags: bool | None = None,
    ) -> fgb.Graph:
        """stack another Graph to this Graph

        :param other: other filtergraph
        :param auto_link: True to connect matched I/O labels, defaults to None
        :param replace_sws_flags: True to use other's sws_flags if present,
                                  False to ignore other's sws_flags,
                                  None to throw an exception (default)
        :return: new filtergraph object

        Remarks
        -------
        - extend() and import links
        - If `auto-link=False`, common labels may be renamed.
        - For more explicit linking rather than the auto-linking, use `connect()` instead.

        TO-CHECK/TO-DO: what happens if common link labels are already linked
        """

        other = fgb.as_filtergraph_object(other)
        # if other is not a filter, elevate self to match first
        return (
            fgb.Graph([[self], [other]])
            if isinstance(other, fgb.Filter)
            else fgb.as_filtergraph_object_like(self, other)._stack(
                other, auto_link, replace_sws_flags
            )
        )

    def apply(self, options, filter_id=None):
        """apply new filter options

        :param options: new option key-value pairs. For ordered option, use positional index (0
                        corresponds to the first option). Set value as None to drop the option.
                        Ordered options can only be dropped in contiguous fashion, including the
                        last ordered option.
        :type options: dict
        :param filter_id: new filter id, defaults to None
        :type filter_id: str, optional
        :return: new filter with modified options
        :rtype: Filter

        .. note::

            To add new ordered options, int-keyed options item must be presented in
            the increasing key order so the option can be expanded one at a time.

        """

        try:
            assert isinstance(self[-1], dict)
            kwopts = dict(self[-1])
            try:
                opts = list(self[1:-1])
            except:
                opts = []
        except:
            kwopts = {}
            try:
                opts = list(self[1:])
            except:
                opts = []

        nopts = len(opts)

        delopts = set()
        for k, v in options.items():
            if type(k) == int:
                if k < 0 or k > nopts:
                    raise Filter.Error(f"invalid positional index [{k}]")
                if v is not None:
                    if k < nopts:
                        opts[k] = v
                    else:
                        opts = [*opts, v]
                        nopts += 1
                elif k < 0 or k > nopts:
                    delopts.add(k)
            else:
                if v is None:
                    del kwopts[k]
                else:
                    kwopts[k] = v

        if len(delopts):
            delopts = sorted(list(delopts))
            o1 = delopts[0] - 1
            on = delopts[-1]
            if on != nopts or len(delopts) != on - o1:
                raise Filter.Error(
                    f"cannot drop specified ordered options {delopts}. They must be contiguous and include the last ordered option."
                )
            opts = opts[:o1]

        return Filter(self[0], *opts, filter_id=filter_id, **kwopts)

    def _input_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        pad_pos = index[2]
        return pad_pos >= 0 and pad_pos < self.get_num_inputs()

    def _output_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        pad_pos = index[2]
        return pad_pos >= 0 and pad_pos < self.get_num_outputs()

    def _check_partial_pad_index(
        self, index: tuple[int | None, int | None, int | None], is_input: bool
    ) -> bool:
        """True if defined values of the partial pad index are valid"""

        if any(i is not None and i > 0 for i in index[:2]):
            return False

        pad = index[2]
        if pad is None:
            pad = 0  # use the smallest pad id

        n = self.get_num_inputs() if is_input else self.get_num_outputs()
        return pad >= 0 and pad < n

    def _input_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified input pad is chainable"""
        if any(i for i in index[:2]):
            return False
        return index[2] == self.get_num_inputs() - 1

    def _output_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified output pad is chainable"""
        if any(i for i in index[:2]):
            return False
        return index[2] == self.get_num_outputs() - 1
