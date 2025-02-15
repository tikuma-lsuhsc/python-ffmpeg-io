from __future__ import annotations

from collections import UserList
from collections.abc import Callable, Generator, Sequence

from itertools import chain

from . import utils as filter_utils
from .. import filtergraph as fgb

from .typing import PAD_INDEX, Literal
from .exceptions import *


__all__ = ["Chain"]


class Chain(fgb.abc.FilterGraphObject, UserList):
    """List of FFmpeg filters, connected in series

    Chain() to instantiate empty Graph object

    Chain(obj) to copy-instantiate Graph object from another

    Chain('...') to parse an FFmpeg filtergraph expression

    :param filter_specs: single-in-single-out filtergraph description without
                         labels, defaults to None
    :type filter_specs: str or seq(Filter), optional
    """

    class Error(FFmpegioError):
        pass

    def __init__(self, filter_specs=None):
        # convert str to a list of filter_specs

        if isinstance(filter_specs, fgb.Filter):
            filter_specs = [filter_specs]
        elif filter_specs is not None:
            if isinstance(filter_specs, str):
                filter_specs, links, sws_flags = filter_utils.parse_graph(filter_specs)
                if links:
                    raise ValueError(
                        "filter_specs with link labels cannot be represented by the Chain class. Use Graph."
                    )
                if sws_flags:
                    raise ValueError(
                        "filter_specs with sws_flags cannot be represented by the Chain class. Use Graph."
                    )
                if len(filter_specs) != 1:
                    raise ValueError(
                        "filter_specs str must resolve to a single-chain filtergraph. Use the Graph class instead."
                    )
                filter_specs = filter_specs[0]

            filter_specs = (fgb.as_filter(fspec) for fspec in filter_specs)

        UserList.__init__(self, () if filter_specs is None else filter_specs)

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
            fgb.Graph([self.data]).compose(
                show_unconnected_inputs, show_unconnected_outputs
            )
            if show_unconnected_inputs or show_unconnected_outputs
            else filter_utils.compose_graph([self.data])
        )

    def __repr__(self):
        type_ = type(self)
        return f"""<{type_.__module__}.{type_.__qualname__} object at {hex(id(self))}>
    FFmpeg expression: \"{self.compose(True,True)}\"
    Number of filters: {len(self.data)}
    Input pads ({self.get_num_inputs()}): {', '.join((str(id) for id,*_ in self.iter_input_pads()))}
    Output pads: ({self.get_num_outputs()}): {', '.join((str(id) for id,*_ in self.iter_output_pads()))}
"""

    def __getitem__(self, key: int | slice | tuple[int | slice, int | slice]):
        if not isinstance(key, (int, slice)):
            i, key = key
            if i != 0:
                raise IndexError("Invalid chain index")

        return UserList.__getitem__(self, key)

    def __setitem__(self, key, value):
        UserList.__setitem__(self, key, fgb.as_filter(value))

    def get_num_chains(self) -> int:
        """get the number of chains"""
        return len(self)

    def get_num_filters(self, chain: int) -> int:
        """get the number of filters of the specfied chain

        :param chain: id of the chain
        """

        if chain:
            raise ValueError(f"{chain=} is invalid. Filter object only has 1 chain.")
        return len(self)

    def get_num_inputs(self) -> int:
        return len(list(self.iter_input_pads()))

    def get_num_outputs(self) -> int:
        return len(list(self.iter_output_pads()))

    def is_last_filter(self, filter_id: int) -> bool:
        """Returns True if the given id is the last filter of the chain"""
        return filter_id == len(self) - 1

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
        else:
            if len(index) == 2:
                index = (0, *index)
            if index[-2] < 0:
                index = (index[-3], len(self) + index[-2], index[-1])

        return self[index[1]].normalize_pad_index(input, index)

    def add_label(
        self,
        label: str,
        inpad: PAD_INDEX = None,
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

        If label has a trailing number, the number will be dropped and replaced with an
        internally assigned label number.

        """

        # must convert to FilterGraph as it's the only object with labels
        fg = fgb.Graph([self])
        return fg.add_label(label, inpad, outpad, force)

    def append(self, item):
        return UserList.append(self, fgb.as_filter(item))

    def extend(self, other: fgb.Chain | Sequence[fgb.Filter | str]):
        return UserList.extend(self, [fgb.as_filter(f) for f in other])

    def insert(self, i, item):
        return UserList.insert(self, i, fgb.as_filter(item))

    def __contains__(self, item):
        item = fgb.as_filter(item)
        return any((f == item for f in self.data))

    def __ior__(self, other):
        raise Chain.Error("cannot assign operation outcome which is not a filterchain")

    def __iadd__(self, other):

        if len(other):
            fg = self + other if len(self) else Chain(other)

            if isinstance(fg, fgb.Graph):
                raise Chain.Error(
                    "cannot assign operation outcome which is not a filterchain"
                )
            self.data = fg.data
        return self

    def __irshift__(self, other):
        if len(other):
            fg = self >> other if len(self) else Chain(other)

            if isinstance(fg, fgb.Graph):
                raise Chain.Error(
                    "cannot assign operation outcome which is not a filterchain"
                )
            self.data = fg.data
            return self

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

        if not len(self):
            return

        if skip_if_no_input and self.next_input_pad() is None:
            return

        if skip_if_no_output and self.next_output_pad() is None:
            return

        yield (0, self)

    def _iter_pads(
        self,
        iter_filter_pad: Callable,
        i_nochain: int,
        pad: int | None,
        filter: int | None,
        chain: Literal[0] | None,
        exclude_chainable: bool,
        chainable_first: bool,
        include_connected: bool,
        chainable_only: bool,
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, bool]]:
        """Iterate over input pads of the filters on the filterchain

        :param filters: list of filters to iterate
        :param iter_filter_pad: Filter class function to iterate on filter pads
        :param pad: pad id
        :param filter: filter index
        :param chain: chain index
        :param exclude_chainable: True to leave out the last pads
        :param chainable_first: True to yield the last pad first then the rest
        :param include_connected: True to include pads connected to input streams, defaults to False
        :yield: filter pad index, filter object, and True if no connection
        """

        if len(self) == 0:
            return

        if isinstance(chain, int) and chain != 0:
            # Filterchain has only one chain.
            raise FiltergraphInvalidIndex(f"Invalid {chain=} id")

        if chainable_only:
            if filter is not None:
                if filter < 0:
                    filter = len(self) + filter
                if filter != i_nochain:
                    raise FiltergraphInvalidIndex(
                        f"{filter=} id is not chainable filter."
                    )
            filters = [self.data[i_nochain]]
            i_first = i_nochain

        elif filter is None:
            # iterate over all filters
            filters = self.data
            i_first = 0
        else:
            try:
                filters = [self.data[filter]]
            except IndexError:
                raise FiltergraphInvalidIndex(f"Invalid {filter=} id.")
            i_first = filter

        # iterate over all filters
        for i, f in enumerate(filters):
            no_chainables = (not include_connected and i != i_nochain) or (
                exclude_chainable and i == i_nochain
            )
            try:
                for pidx, f, other_pidx in iter_filter_pad(
                    f,
                    pad,
                    exclude_chainable=no_chainables,
                    chainable_first=chainable_first,
                    chainable_only=chainable_only,
                ):
                    yield (i + i_first, *pidx), f, other_pidx
            except FiltergraphInvalidIndex:
                pass

    def iter_input_pads(
        self,
        pad: int | None = None,
        filter: int | None = None,
        chain: Literal[0] | None = None,
        *,
        exclude_chainable: bool = False,
        chainable_first: bool = False,
        include_connected: bool = False,
        unlabeled_only: bool = False,
        chainable_only: bool = False,
        full_pad_index: bool = False,
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, PAD_INDEX | None]]:
        """Iterate over input pads of the filters on the filterchain

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
            fgb.Filter.iter_input_pads,
            0,
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            include_connected,
            chainable_only,
        ):
            if other_index is None:
                out_index = None
            else:
                # get the last output pad of the previous filter
                out_i = index[1] - 1
                out_index = (0, out_i, self[out_i].get_num_outputs() - 1)

            yield (
                ((0, *index), filter, out_index)
                if full_pad_index
                else (index, filter, out_index)
            )

    def iter_output_pads(
        self,
        pad: int | None = None,
        filter: int | None = None,
        chain: int | None = None,
        *,
        exclude_chainable: bool = False,
        chainable_first: bool = False,
        include_connected: bool = False,
        unlabeled_only: bool = False,
        chainable_only: bool = False,
        full_pad_index: bool = False,
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, PAD_INDEX | None]]:
        """Iterate over output pads of the filters on the filterchain

        :param pad: pad id, defaults to None
        :param filter: filter index, defaults to None
        :param chain: chain index, defaults to None
        :param exclude_chainable: True to leave out the last output pads, defaults to False (all avail pads)
        :param chainable_first: True to yield the last output first then the rest, defaults to False
        :param include_connected: True to include pads connected to output streams, defaults to False
        :param unlabeled_only: True to leave out named outputs, defaults to False to return all outputs
        :param chainable_only: True to only iterate chainable pads, defaults to False to return all outputs
        :param full_pad_index: True to return 3-element index
        :yield: filter pad index, link label, filter object, output pad index of connected filter if connected
        """

        for index, filter, other_index in self._iter_pads(
            fgb.Filter.iter_output_pads,
            len(self.data) - 1,
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            include_connected,
            chainable_only,
        ):
            if other_index is None:
                in_index = None
            else:
                # get the last input pad of the next filter
                in_i = index[1] + 1
                in_index = (0, in_i, self[in_i].get_num_inputs() - 1)
            yield (
                ((0, *index), filter, in_index)
                if full_pad_index
                else (index, filter, in_index)
            )

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

        if isinstance(right, fgb.Graph):
            # right is more complex filtergraph object
            return right._rconnect(
                self, fwd_links, bwd_links, chain_siso, replace_sws_flags
            )

        right = fgb.as_filterchain(right)

        if chain_siso and self.get_num_outputs() == 1 and right.get_num_inputs() == 1:
            return fgb.Chain([*self, *right])

        # create iterators to organize the links in (input, output) of the combined graph
        it_fwd = (((1, *r[1:]), l) for (l, r) in fwd_links)
        it_bwd = ((l, (1, *r[1:])) for (r, l) in bwd_links)

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

        if isinstance(left, fgb.Graph):
            # left is more complex filtergraph object
            return left._connect(
                self, fwd_links, bwd_links, chain_siso, replace_sws_flags
            )

        left = fgb.as_filterchain(left)

        if chain_siso and left.get_num_outputs() == 1 and self.get_num_inputs() == 1:
            return fgb.Chain([*left, *self])

        # create iterators to organize the links in (input, output) of the combined graph
        it_fwd = (((1, *r[1:]), l) for (l, r) in fwd_links)
        it_bwd = ((l, (1, *r[1:])) for (r, l) in bwd_links)

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
        """stack another Graph to this Graph (no var check)"""

        other = fgb.atleast_filterchain(other)

        # if other is not a filter, elevate self to match first
        return (
            fgb.Graph([self, other])
            if isinstance(other, fgb.Chain)
            else fgb.Graph(self)._stack(other, auto_link, replace_sws_flags)
        )

        return fgb.as_filtergraph(self)._stack(other, auto_link, replace_sws_flags)

    def _input_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        """returns True if specified input pad index is available"""

        pos = index[1]
        if pos < 0 or pos >= len(self):
            return False

        # if chained to the previous filter, not avail
        pad_pos = index[2]
        n = self[pos].get_num_inputs()
        return pad_pos >= 0 and pad_pos < (n - 1 if pos else n)

    def _output_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        """returns True if specified output pad index is available"""

        pos = index[1]
        nchain = len(self)
        if pos < 0 or pos >= nchain:
            return False

        # if chained to the next filter, not avail
        pad_pos = index[2]
        n = self[pos].get_num_outputs()
        return pad_pos >= 0 and pad_pos < (n - 1 if pos < nchain - 1 else n)

    def _check_partial_pad_index(
        self, index: tuple[int | None, int | None, int | None], is_input: bool
    ) -> bool:
        """True if defined values of the partial pad index are valid"""

        if index[0] is not None and index[0] > 0:
            return False

        filter = index[1]
        if filter is not None:
            if filter < 0 or filter >= len(self):
                return False

        return any(
            f._check_partial_pad_index((None, None, index[2]), is_input) for f in self
        )

    def _input_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified input pad is chainable"""
        if index[0]:
            return False
        try:
            filter = self[index[1]]
        except IndexError:
            return False
        else:
            return filter._input_pad_is_chainable((0, 0, index[2]))

    def _output_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified output pad is chainable"""
        if index[0]:
            return False
        try:
            filter = self[index[1]]
        except IndexError:
            return False
        else:
            return filter._output_pad_is_chainable((0, 0, index[2]))
