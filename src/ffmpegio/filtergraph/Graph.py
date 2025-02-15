from __future__ import annotations

from collections import UserList
from collections.abc import Generator, Callable, Sequence

from contextlib import contextmanager
from itertools import chain
from copy import deepcopy
from math import floor, log10
import os
from tempfile import NamedTemporaryFile

from . import utils as filter_utils

from ..stream_spec import is_map_option
from .. import filtergraph as fgb

from .typing import PAD_INDEX
from .exceptions import *
from .GraphLinks import GraphLinks


__all__ = ["Graph"]


class Graph(fgb.abc.FilterGraphObject, UserList):
    """List of FFmpeg filterchains in parallel with interchain link specifications

    Graph() to instantiate empty Graph object

    Graph(obj) to copy-instantiate Graph object from another

    Graph('...') to parse an FFmpeg filtergraph expression

    Graph(filter_specs, links, sws_flags)
    to specify the compose_graph(...) arguments

    :param filter_specs: either an existing Graph instance to copy, an FFmpeg
                         filtergraph expression, or a nested sequence of argument
                         sequences to compose_filter() to define a filtergraph.
                         For the latter option, The last element of each filter argument
                         sequence may be a dict, defining its keyword arguments,
                         defaults to None
    :type filter_specs: Graph, str, or seq(seq(filter_args))
    :param links: specifies filter links
    :type links: dict, optional
    :param sws_flags: specify swscale flags for those automatically inserted
                      scalers, defaults to None
    :type sws_flags: seq of stringifyable elements with optional dict as the last
                     element for the keyword flags, optional

    """

    class Error(FFmpegioError):
        pass

    class FilterPadMediaTypeMismatch(Error):
        def __init__(self, in_name, in_pad, in_type, out_name, out_pad, out_type):
            super().__init__(
                f"mismatched pad types: {in_name}:{in_pad}[{in_type}] => {out_name}:{out_pad}[{out_type}]"
            )

    class InvalidFilterPadId(Error):
        def __init__(self, type, index):
            super().__init__(f"invalid {type} filter pad index: {index}")

    _unc_label: str = "UNC"

    def __init__(
        self,
        filter_specs: (
            Sequence[fgb.Chain | str | Sequence[fgb.Filter]]
            | str
            | fgb.abc.FilterGraphObject
            | None
        ) = None,
        links: (
            dict[
                str | int,
                tuple[
                    PAD_INDEX | Sequence[PAD_INDEX] | None,
                    PAD_INDEX | Sequence[PAD_INDEX] | None,
                ],
            ]
            | GraphLinks
            | None
        ) = None,
        sws_flags: Sequence[str] | None = None,
    ):

        # convert str to a list of filter_specs
        if isinstance(filter_specs, fgb.Graph):
            links = filter_specs._links
            sws_flags = filter_specs.sws_flags and [*filter_specs.sws_flags[1:]]
        elif isinstance(filter_specs, fgb.Chain):
            filter_specs = [filter_specs] if len(filter_specs) else ()
        elif filter_specs is not None:
            if isinstance(filter_specs, fgb.Filter):
                filter_specs = [[filter_specs]]
            elif not len(filter_specs):
                filter_specs = []
                links = sws_flags = None
            elif isinstance(filter_specs, str):
                filter_specs, links, sws_flags = filter_utils.parse_graph(filter_specs)

            if any(not len(fspec) for fspec in filter_specs):
                raise ValueError(
                    "An empty filterchain found. All chains must be populated."
                )

        UserList.__init__(
            self,
            () if filter_specs is None else iter(fgb.Chain(c) for c in filter_specs),
        )

        self._links = GraphLinks(links)
        """utils.fglinks.GraphLinks: filtergraph link specifications
        """

        self.sws_flags = (
            None if sws_flags is None else fgb.Filter(["scale", *sws_flags])
        )
        """Filter|None: swscale flags for automatically inserted scalers
        """

    def get_num_chains(self) -> int:
        """get the number of hains"""
        return len(self)

    def get_num_filters(self, chain: int) -> int:
        """get the number of filters of the specfied chain

        :param chain: id of the chain
        """

        if chain < 0 or chain >= len(self):
            raise ValueError(f"{chain=} is invalid.")
        return len(self[chain])

    def resolve_pad_index(
        self,
        index_or_label: PAD_INDEX | str | None,
        *,
        is_input: bool = True,
        chain_id_omittable: bool = False,
        filter_id_omittable: bool = False,
        pad_id_omittable: bool = False,
        resolve_omitted: bool = True,
        chain_fill_value: int | None = None,
        filter_fill_value: int | None = None,
        pad_fill_value: int | None = None,
        chainable_first: bool = False,
    ) -> PAD_INDEX:
        """Resolve unconnected label or pad index to full 3-element pad index

        :param index_or_label: pad index set or pad label or ``None`` to auto-select
        :param is_input: True to resolve an input pad, else an output pad, defaults to True
        :param chain_id_omittable: True to allow ``None`` chain index, defaults to False
        :param filter_id_omittable: True to allow ``None`` filter index, defaults to False
        :param pad_id_omittable: True to allow ``None`` pad index, defaults to False
        :param resolve_omitted: True to fill each omitted value with the prescribed fill value.
        :param chain_fill_value: if ``chain_id_omittable=True`` and chain index is either not
                                 given or ``None``, this value will be returned, defaults to None,
                                 which returns the first available pad.
        :param filter_fill_value:if ``filter_id_omittable=True`` and filter index is either not
                                 given or ``None``, this value will be returned, defaults to None,
                                 which returns the first available pad.
        :param pad_fill_value: if ``pad_id_omittable=True`` and either ``index`` is None or
                               pad index is ``None``, this value will be returned, defaults to None,
                               which returns the first available pad.
        :param chainable_first: if True, chainable pad is selected first, defaults to False

        One and only one of ``index`` and ``label`` must be specified. If the given index
        or label is invalid, it raises FiltergraphPadNotFoundError.
        """

        # resolve a label string to pad index
        if isinstance(index_or_label, str):  # label given
            label = (
                index_or_label[1:-1]
                if index_or_label[0] == "[" and index_or_label[-1] == "]"
                else index_or_label
            )

            try:
                if is_input:
                    index_or_label = next(
                        index
                        for lbl, index in self.iter_input_labels(
                            exclude_stream_specs=True
                        )
                        if lbl == label
                    )
                else:
                    index_or_label = next(
                        index
                        for lbl, index in self.iter_output_labels()
                        if lbl == label
                    )
            except StopIteration as exc:
                raise FiltergraphPadNotFoundError(
                    f"{index_or_label=} is not defined on the filtergraph."
                ) from exc

        # obtain 3-element tuple index (unvalidated)
        return super().resolve_pad_index(
            index_or_label,
            is_input=is_input,
            chain_id_omittable=chain_id_omittable,
            filter_id_omittable=filter_id_omittable,
            pad_id_omittable=pad_id_omittable,
            resolve_omitted=resolve_omitted,
            chain_fill_value=chain_fill_value,
            filter_fill_value=filter_fill_value,
            pad_fill_value=pad_fill_value,
            chainable_first=chainable_first,
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
        elif index[-3] < 0:
            index = (len(self) + index[-3], *index[-2:])

        return self[index[0]].normalize_pad_index(input, index)

    def _get_label(self, input: bool, index: PAD_INDEX):

        index = self.normalize_pad_index(input, index)

        return getattr(
            self._links, "find_inpad_label" if input else "find_outpad_label"
        )(index)

    def compose(
        self,
        show_unconnected_inputs: bool = True,
        show_unconnected_outputs: bool = True,
    ):
        """compose filtergraph

        :param show_unconnected_inputs: display [UNC#] on all unconnected input pads, defaults to True
        :param show_unconnected_outputs: display [UNC#] on all unconnected output pads, defaults to True
        """

        fg = self

        # label unconnected pads
        label = self._unc_label
        unc_pads = {}
        i = j = -1
        if show_unconnected_inputs:
            for i, (index, _, _) in enumerate(
                self.iter_input_pads(unlabeled_only=True)
            ):
                unc_pads[f"{label}{i}"] = (index, None)
        if show_unconnected_outputs:
            for j, (index, _, _) in enumerate(
                self.iter_output_pads(unlabeled_only=True)
            ):
                unc_pads[f"{label}{i+j+1}"] = (None, index)

        links = {**fg._links, **unc_pads} if i >= 0 or j >= 0 else fg._links

        return filter_utils.compose_graph(fg, links, fg.sws_flags and fg.sws_flags[1:])

    def __repr__(self):
        type_ = type(self)
        expr = self.compose()
        nchains = len(self.data)
        pos = [0] * nchains
        i = n = 0
        for j, chain in enumerate(self):
            for k, filter in enumerate(chain):
                fstr = str(filter)
                i += n
                i = expr[i:].find(fstr) + i
                n = len(fstr)
                pos[j] = i

        pos = [expr.rfind(";", 0, i) + 1 for i in pos]
        pos.append(len(expr))

        prefix = "      chain"
        nzeros = floor(log10(nchains)) + 1
        fmt = f"0{nzeros}"
        chain_list = [
            f"{prefix}[{j:{fmt}}]: {expr[i0:i1]}"
            for j, (i0, i1) in enumerate(zip(pos[:-1], pos[1:]))
        ]
        if self.sws_flags:
            chain_list = [f"{[' ']*(len(prefix)+3+nzeros)}{expr[:pos[0]]}", *chain_list]
        if len(chain_list) > 12:
            chain_list = [
                chain_list[:-4],
                f"{[' ']*(len(prefix)+3+nzeros)}{expr[:pos[0]]}",
                chain_list[-3:],
            ]
        chain_list = "\n".join(chain_list)

        return f"""<{type_.__module__}.{type_.__qualname__} object at {hex(id(self))}>
    FFmpeg expression: \"{str(self)}\"
    Number of chains: {len(self)}
{chain_list}      
    Available input pads ({self.get_num_inputs()}): {', '.join((str(id[0]) for id in self.iter_input_pads()))}
    Available output pads: ({self.get_num_outputs()}): {', '.join((str(id[0]) for id in self.iter_output_pads()))}
"""

    def __setitem__(self, key, value):
        UserList.__setitem__(self, key, fgb.as_filterchain(value, copy=True))
        # TODO purge invalid links

    def __getitem__(self, key):
        """get filterchains/filter

        :param key: filterchain or filter indices
        :type key: int, slice, tuple(int|slice,int|slice)
        :return: selected filterchain(s) or filter
        :rtype: Graph|Chain|Filter
        """
        try:
            return UserList.__getitem__(self, key)
        except (IndexError, StopIteration) as e:
            raise e
        except Exception as e:
            try:
                assert len(key) == 2 and all((isinstance(k, int) for k in key))
                return UserList.__getitem__(self, key[0])[key[1]]
            except:
                raise TypeError(
                    "Graph indies must be integers, slices, or 2-element tuple of int"
                )

    def append(self, item: fgb.Chain | str):

        fc = fgb.as_filterchain(item, copy=True)
        if not len(fc):
            raise ValueError("Empty filterchain cannot be appended to filtergraph.")
        self.data.append(fc)

    def extend(
        self,
        other: Sequence[fgb.Chain | str] | fgb.FilterGraph,
        auto_link: bool = False,
        force_link: bool = False,
    ):
        other = fgb.as_filtergraph(other)
        if any(not len(c) for c in other):
            raise ValueError("Empty filterchain cannot be appended to filtergraph.")
        self._links.update(
            other._links.map_chains(len(self)), auto_link=auto_link, force=force_link
        )
        self.data.extend(other.data)

    def insert(self, i: int, item: fgb.Chain | str):
        fc = fgb.as_filterchain(item)
        if not len(fc):
            raise ValueError("Empty filterchain cannot be appended to filtergraph.")
        self.data.insert(i, fc)
        self._links.adjust_chains(i, 1)

    def __delitem__(self, i: int):

        if i < 0:
            i += len(self)

        # delete the chain
        UserList.__delitem__(self, i)

        # delete all links with the specified chain
        self._links.remove_chains([i])

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

        for i, c in enumerate(self):
            if (
                skip_if_no_output
                and self.next_output_pad(
                    chain=i, filter=-1, chainable_only=chainable_only
                )
                is None
            ) or (
                skip_if_no_input
                and self.next_input_pad(
                    chain=i, filter=0, chainable_only=chainable_only
                )
                is None
            ):
                continue

            yield i, c

    def _iter_pads(
        self,
        iter_filter_pad: Callable,
        pad_links: dict[PAD_INDEX, PAD_INDEX | str],
        pad: int | None,
        filter: int | None,
        chain: Literal[0] | None,
        exclude_chainable: bool,
        chainable_first: bool,
        include_connected: bool,
        unlabeled_only: bool,
        chainable_only: bool,
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, PAD_INDEX | str | None]]:
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

        if chain is None:
            # iterate over all filters
            chains = self.data
            ioff = 0
        else:
            try:
                chains = [self.data[chain]]
            except IndexError:
                raise FiltergraphInvalidIndex(f"Invalid {chain=} id.")
            ioff = chain

        for i, c in enumerate(chains):

            j = (len(c) + filter) if filter is not None and filter < 0 else filter

            for pidx, f, other_pidx in iter_filter_pad(
                c,
                pad,
                j,
                exclude_chainable=exclude_chainable,
                chainable_first=chainable_first,
                include_connected=include_connected,
                chainable_only=chainable_only,
            ):
                index = (i + ioff, *pidx)

                try:
                    assert other_pidx is None
                    # retrieve a connected output pad or a label if just labeled
                    other_pidx = pad_links[index]
                except (AssertionError, KeyError):
                    # fails if chained or no link defined
                    yield index, f, other_pidx
                    continue

                # exclude unlinked label-only pads, including input streams
                # return output label or output pad connected to
                is_str = isinstance(other_pidx, str)
                if (is_str and not unlabeled_only) or (
                    not is_str and include_connected
                ):
                    yield index, f, other_pidx

    def iter_input_pads(
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
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, PAD_INDEX | str | None]]:
        """Iterate over input pads of the filters on the filtergraph

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

        for index, f, other_pidx in self._iter_pads(
            fgb.Chain.iter_input_pads,
            self._links.input_dict(),
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            include_connected,
            unlabeled_only,
            chainable_only,
        ):
            # exclude a pad connected to an input stream
            if (
                not include_connected
                and isinstance(other_pidx, str)
                and is_map_option(other_pidx, allow_missing_file_id=True)
            ):
                continue

            yield index, f, other_pidx

    def iter_output_pads(
        self,
        pad=None,
        filter=None,
        chain=None,
        *,
        exclude_chainable: bool = False,
        chainable_first: bool = False,
        include_connected: bool = False,
        unlabeled_only: bool = False,
        chainable_only: bool = False,
        full_pad_index: bool = False,
    ) -> Generator[tuple[PAD_INDEX, fgb.Filter, PAD_INDEX | str | None]]:
        """Iterate over output pads of the filter

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

        for v in self._iter_pads(
            fgb.Chain.iter_output_pads,
            self._links.output_dict(),
            pad,
            filter,
            chain,
            exclude_chainable,
            chainable_first,
            include_connected,
            unlabeled_only,
            chainable_only,
        ):
            yield v

    def get_num_inputs(self, chainable_only=False):
        return len(list(self.iter_input_pads(chainable_only=chainable_only)))

    def get_num_outputs(self, chainable_only=False):
        return len(list(self.iter_output_pads(chainable_only=chainable_only)))

    def iter_input_labels(
        self, exclude_stream_specs: bool = False
    ) -> Generator[tuple[str, PAD_INDEX]]:
        """iterate over the dangling labeled input pads of the filtergraph object

        :param exclude_stream_specs: True to not include input streams
        :yield: a tuple of 3-tuple pad index and the pad index of the connected output pad if connected
        """
        for label_index in self._links.iter_inputs(exclude_stream_specs):
            yield label_index

    def iter_output_labels(self) -> Generator[tuple[str, PAD_INDEX]]:
        """iterate over the dangling labeled output pads of the filtergraph object

        :yield: a tuple of 3-tuple pad index and the pad index of the connected input pad if connected
        """
        for label_index in self._links.iter_outputs():
            yield label_index

    def copy(self):
        return Graph(self)

    def are_linked(
        self,
        inpad: PAD_INDEX | None,
        outpad: PAD_INDEX | None,
        check_input_stream: bool | str = False,
    ) -> bool:
        """True if given pads are linked

        :param inpad: input pad index, default to ``None`` to check if ``outpad`` is connected to any
                      input pad.
        :param outpad: output pad index, defaults to ``None`` to check if ``inpad`` is connected to any
                       output pad or an input stream.
        :param check_input_stream: True to check inpad is connected to an input stream, or a stream
                                   specifier string to check the connection to a specific stream, defaults
                                   to ``False``.

        ``ValueError`` will be raised if both ``inpad`` and ``outpad`` ``None`` or
        if ``include_input_stream!=False`` and ``outpad`` is ``None``.

        """

        try:
            return self._links.are_linked(inpad, outpad, check_input_stream)
        except ValueError:
            raise

    def unlink(
        self,
        label: str | None = None,
        inpad: PAD_INDEX | None = None,
        outpad: PAD_INDEX | None = None,
    ):
        """unlink specified links

        :param label: specify all the links with this label, defaults to None
        :type label: str|int, optional
        :param inpad: specify the link with this inpad pad, defaults to None
        :type inpad: tuple(int,int,int), optional
        :param outpad: specify all the links with this outpad pad, defaults to None
        :type outpad: tuple(int,int,int), optional
        """
        self._links.unlink(label, inpad, outpad)

    def link(
        self,
        inpad: PAD_INDEX,
        outpad: PAD_INDEX,
        label: str | None = None,
        preserve_label: Literal[False, "input", "output"] = False,
        force: bool = False,
    ) -> str | int:
        """set a filtergraph link

        :param inpad: input pad ids
        :param outpad: output pad index
        :param label: desired label name, defaults to None (=reuse inpad/outpad label or unnamed link)
        :param preserve_label: `False` to remove the labels of the input and output pads (default) or
                               `'input'` to prefer the input label or `'output'` to prefer the output
                               label.
        :param force: True to drop conflicting existing link, defaults to False
        :return: assigned label of the created link. Unnamed links gets a
                 unique integer value assigned to it.

        ..notes:

            - Unless `force=True`, inpad pad must not be already connected
            - User-supplied label name is a suggested name, and the function could
              modify the name to maintain integrity.
            - If inpad or outpad were previously named, their names will be dropped
              unless one matches the user-supplied label.
            - No guarantee on consistency of the link label (both named and unnamed)
              during the life of the object

        """

        if label is not None:
            GraphLinks.validate_label(label, is_link=False, no_stream_spec=True)
        if inpad is not None:
            inpad = self.resolve_pad_index(inpad, is_input=True)
            try:
                f = self.data[inpad[0]][inpad[1]]
                assert inpad[2] >= 0 and inpad[2] < f.get_num_inputs()
            except:
                raise Graph.InvalidFilterPadId("input", inpad)
        if outpad is not None:
            outpad = self.resolve_pad_index(outpad, is_input=False)
            try:
                f = self.data[outpad[0]][outpad[1]]
                assert outpad[2] >= 0 and outpad[2] < f.get_num_outputs()
            except:
                raise Graph.InvalidFilterPadId("output", outpad)

        return self._links.link(inpad, outpad, label, preserve_label, force)

    def add_label(
        self,
        label: str,
        inpad: PAD_INDEX | None = None,
        outpad: PAD_INDEX | None = None,
        force: bool = None,
    ) -> fgb.Graph:
        """label a filter pad

        :param label: name of the new label. Square brackets are optional.
        :type label: str
        :param inpad: input filter pad index or a sequence of pads, defaults to None
        :type inpad: tuple(int,int,int) | seq(tuple(int,int,int)), optional
        :param outpad: output filter pad index, defaults to None
        :type outpad: tuple(int,int,int), optional
        :param force: True to delete existing labels, defaults to None
        :type force: bool, optional
        :return: actual label name
        :rtype: str

        Only one of inpad and outpad argument must be given.

        If given label already exists, no new label will be created.

        If label has a trailing number, the number will be dropped and replaced with an
        internally assigned label number.

        """

        if label[0] == "[" and label[-1] == "]":
            label = label[1:-1]

        GraphLinks.validate_label(
            label, is_link=False, no_stream_spec=outpad is not None
        )
        if inpad is not None:
            GraphLinks.validate_pad_idx_pair((inpad, None))
            for d in GraphLinks.iter_inpad_ids(inpad):
                try:
                    f = self.data[d[0]][d[1]]
                    n = f.get_num_inputs()
                    assert d[2] >= 0 and d[2] < (n - 1 if d[1] > 0 else n)
                except:
                    raise Graph.InvalidFilterPadId("input", d)
        elif outpad is not None:
            GraphLinks.validate_pad_idx(outpad)
            try:
                f = self.data[outpad[0]][outpad[1]]
                assert outpad[2] >= 0 and outpad[2] < f.get_num_outputs()
            except:
                raise Graph.InvalidFilterPadId("output", outpad)
        else:
            raise Graph.Error("filter pad index is not given")

        self._links.create_label(label, inpad, outpad, force)

        return self

    def remove_label(self, label: str):
        """remove an input/output label

        :param label: linkn label
        """

        self._links.remove_label(label)

    def rename_label(self, old_label: str, new_label: str) -> str | None:
        """rename an existing link label

        :param old_label: existing label named
        :param new_label: new desired label name or None to make it unnamed label
        :return: actual label name or None if unnamed

        Note:

        - `new_label` is not guaranteed, and actual label depends on existing labels

        """

        if not (isinstance(old_label, str) and old_label):
            raise Graph.Error(f"old_label [{old_label}] must be a string.")

        if new_label is not None and not (isinstance(new_label, str) and new_label):
            raise Graph.Error(f"new_label [{new_label}] must be None or a string.")

        # return the actual label or None if unnamed
        return new_label or self._links.rename(old_label, new_label)

    def is_chain_siso(
        self,
        chain_id: int,
        check_input: bool = True,
        check_output: bool = True,
        check_link: bool = False,
    ) -> bool:
        """True if specified filter chain is single-input and single-output

        :param chain_id: chain id
        :param check_input: False to check only for single-output, defaults to True
        :param check_output: False to check only for single-input, defaults to True
        :param check_link: True to return True if and only if the chain has no active connection, defaults to True
        """

        try:
            chain = self[chain_id]
        except IndexError:
            raise ValueError(f"{chain_id=} is an invalid chain id.")

        if check_input and chain.get_num_inputs() != 1:
            return False

        if check_output and chain.get_num_outputs() != 1:
            return False

        return not (check_link and self._links.chain_has_link(chain_id))

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

        n = len(self)
        m = len(other)

        if not m:  # other is empty
            return Graph(self)
        if not n:  # self is empty
            return Graph(other)

        if isinstance(other, Graph):

            fg = Graph(self)
            if other.sws_flags is not None:
                if fg.sws_flags is None or replace_sws_flags is True:
                    fg.sws_flags = deepcopy(other.sws_flags)
                elif replace_sws_flags is None:
                    raise Graph.Error(
                        f"sws_flags are defined on both FilterGraphs. Specify replace_sws_flags option to True or False to avoid this error."
                    )

            try:
                fg._links.update(
                    other._links.map_chains(len(self), False), auto_link=auto_link
                )
            except Exception as e:
                if auto_link:
                    raise
                else:
                    raise Graph.Error(e) from e

            fg.data.extend(other)

        else:
            # if other is not filtergraph, copy and append the new chain
            fg = Graph(self)
            fg.append(other)

        return fg

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
        :param chain_siso: True to chain the single-input single-output connection, default: True
        :param replace_sws_flags: True to use `right` sws_flags if present,
                                  False to drop `right` sws_flags,
                                  None to throw an exception (default)
        :return: new filtergraph object

        * link labels may be auto-renamed if there is a conflict

        """

        fg = Graph(self)

        must_link_fwd = [True] * len(fwd_links)
        right_chained = []

        if chain_siso and not len(bwd_links):
            # if linking chains are both siso and free of any other linkages and both pads are not labeled
            # the chain of the right fg is joined to the chain of the left

            right = fgb.as_filtergraph(right, copy=True)

            # chain links if there is no ambiguity
            for i, (outpad, inpad) in enumerate(fwd_links):
                ochain, ichain = outpad[0], inpad[0]

                # label check
                if (
                    fg.is_chain_siso(
                        ochain, check_input=False, check_output=True, check_link=False
                    )
                    and not fg._links.are_linked(None, outpad)
                    and right.is_chain_siso(
                        ichain, check_input=True, check_output=False, check_link=True
                    )
                ):
                    # add the right chain to the matching left chain
                    fg[ochain].extend(right[ichain])

                    label = fg._links.find_outpad_label(outpad)
                    if label:
                        fg._links.remove_label(label)

                    # mark already connected
                    must_link_fwd[i] = False
                    right_chained.append(ichain)

        # stack the remaining chains
        if len(bwd_links) or any(must_link_fwd):

            # sift through the connections for chainable and unchainables
            n0 = fg.get_num_chains()  # chain index offset

            # stack 2 filtergraphs and build right chain id conversion lookup table
            lut = {}
            for i, c in enumerate(right):
                if i not in right_chained:
                    lut[i] = n0
                    n0 += 1
                    fg = fg._stack(c)

            right_links = right._links.drop_labels(tuple(fg._links.keys())).map_chains(
                lut, False
            )

            # transfer the right links to fg (remap chains)
            fg._links.update(right_links)

            # create iterators to organize the links in (input, output) of the combined graph
            it_fwd = (
                ((lut[r[0]], *r[1:]), l)
                for (l, r), do_link in zip(fwd_links, must_link_fwd)
                if do_link
            )
            it_bwd = ((l, (lut[r[0]], *r[1:])) for (r, l) in bwd_links)
            fg._links.update(
                {i: link for i, link in enumerate(chain(it_fwd, it_bwd))},
                validate=False,
            )

        if replace_sws_flags and right.sws_flags:
            fg.sws_flags = right.sws_flags

        return fg

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

        # return fgb.as_filtergraph(left)._connect(
        #     self, fwd_links, bwd_links, chain_siso, replace_sws_flags
        # )

        fg = Graph(self)

        must_link_fwd = [True] * len(fwd_links)
        left_chained = []

        if chain_siso and not len(bwd_links):
            # if linking chains are both siso and free of any other linkages and both pads are not labeled
            # the chain of the right fg is joined to the chain of the left

            left = fgb.as_filtergraph(left, copy=True)

            # chain links if there is no ambiguity
            for i, (outpad, inpad) in enumerate(fwd_links):
                ochain, ichain = outpad[0], inpad[0]

                # label check
                if (
                    fg.is_chain_siso(
                        ichain, check_input=True, check_output=False, check_link=False
                    )
                    and not fg._links.are_linked(inpad, None)
                    and left.is_chain_siso(
                        ochain, check_input=False, check_output=True, check_link=True
                    )
                ):
                    # add the right chain to the matching left chain
                    left_chain = left[ochain]
                    fg[ichain].data = [*left_chain, *fg[ichain]]

                    label = fg._links.find_inpad_label(inpad)
                    if label:
                        fg._links.remove_label(label)

                    fg._links.adjust_filters(ichain, 0, len(left_chain))

                    # mark already connected
                    must_link_fwd[i] = False
                    left_chained.append(ochain)

        # stack the remaining chains
        if len(bwd_links) or any(must_link_fwd):
            fg = left._connect(
                fg,
                [link for link, do_link in zip(fwd_links, must_link_fwd) if do_link],
                bwd_links,
                chain_siso=False,
                replace_sws_flags=replace_sws_flags,
            )

        return fg

    def _iter_io_pads(self, is_input, how, ignore_labels=False):
        """Iterates input/output pads of the filtergraph

        :param is_input: True if input; False if output
        :type is_input: bool
        :param how: pad selection method

                    -----------  -------------------------------------------------------------------
                    'chainable'  only chainable pads.
                    'per_chain'  one pad per chain. Source and sink chains are ignored.
                    'all'        joins all input pads and output pads
                    -----------  -------------------------------------------------------------------

        :type how: "chainable"|"per_chain"|"all"
        :param ignore_labels: True to return labaled (but not linked) pads, defaults to False
        :type ignore_labels: bool, optional
        :yield: pad index, pad label, parent filter
        :rtype: tuple(tuple(int,int,int), label, Filter)
        """
        if how is None or how in ("per_chain", "all"):
            generator = self.iter_input_pads if is_input else self.iter_output_pads

            return (
                generator()
                if how == "all"
                else (
                    info
                    for info in (
                        next(generator(unlabeled_only=not ignore_labels, chain=c), None)
                        for c in range(len(self.data))
                    )
                    if info is not None
                )
            )
        elif how == "chainable":
            return (self.iter_input_pads if is_input else self.iter_output_pads)(
                unlabeled_only=not ignore_labels, chainable_only=True
            )
        else:
            raise ValueError(f"unknown how argument value: {how}")

    @contextmanager
    def as_script_file(self):
        """return script file containing the filtergraph description

        :yield: path of a temporary text file with filtergraph description
        :rtype: str

        This method is intended to work with the `filter_script` and
        `filter_complex_script` FFmpeg options, by creating a temporary text file
        containing the filtergraph description.

        .. note::
          Only use this function when the filtergraph description is too long for
          OS to handle it. Presenting the filtergraph with a `filter_complex` or
          `filter` option to FFmpeg is always a faster solution.

          Moreover, if `stdin` is available, i.e., not for a write or filter
          operation, it is more performant to pass the long filtergraph object
          to the subprocess' `input` argument instead of using this method.

        Use this method with a `with` statement. How to incorporate its output
        with `ffmpegprocess` depends on the `as_file_obj` argument.

        :Example:

          The following example illustrates a usecase for a video SISO filtergraph:

          .. code-block:: python

             # assume `fg` is a SISO video filter Graph object

             with fg.as_script_file() as script_path:
                 ffmpegio.ffmpegprocess.run(
                     {
                         'inputs':  [('input.mp4', None)]
                         'outputs': [('output.mp4', {'filter_script:v': script_path})]
                     })

          As noted above, a performant alternative is to use an input pipe and
          feed the filtergraph description directly:

          .. code-block:: python

             ffmpegio.ffmpegprocess.run(
                 {
                     'inputs':  [('input.mp4', None)]
                     'outputs': [('output.mp4', {'filter_script:v': 'pipe:0'})]
                 },
                 input=str(fg))

          Note that ``pipe:0`` must be used and not the shorthand ``'-'`` unlike
          the input url.

        """

        # populate the file with filtergraph expression
        temp_file = NamedTemporaryFile("wt", delete=False)
        temp_file.write(str(self))
        temp_file.close()

        try:
            # present the file to the caller in the context
            yield temp_file.name

        finally:
            if temp_file:
                os.remove(temp_file.name)

    def _input_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        """returns True if specified input pad index is available"""

        # check linked indices
        if any(
            link[1] == index
            for link in self._links.iter_links(include_input_stream=True)
        ):
            # already connected
            return False

        # check the chain
        return self[index[0]]._input_pad_is_available((0, *index[1:]))

    def _output_pad_is_available(self, index: tuple[int, int, int]) -> bool:
        """returns True if specified output pad index is available"""

        # check linked indices
        if any(link[2] == index for link in self._links.iter_links()):
            # already connected
            return False

        return self[index[0]]._output_pad_is_available((0, *index[1:]))

    def _check_partial_pad_index(
        self, index: tuple[int | None, int | None, int | None], is_input: bool
    ) -> bool:
        """True if defined values of the partial pad index are valid"""

        chain = index[0]
        if chain is not None and (chain < 0 or chain >= len(self)):
            return False

        return any(
            c._check_partial_pad_index((None, *index[1:]), is_input) for c in self
        )

    def _input_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified input pad is chainable"""

        if self.are_linked(index, None, True):
            return False

        i = index[0]
        try:
            chain = self[i]
        except IndexError:
            # invalid chain index
            return False
        else:
            return chain._input_pad_is_chainable((0, *index[1:]))

    def _output_pad_is_chainable(self, index: tuple[int, int, int]) -> bool:
        """True if specified output pad is chainable"""

        if self.are_linked(None, index):
            return False

        i = index[0]
        try:
            chain = self[i]
        except IndexError:
            # invalid chain index
            return False
        else:
            return chain._output_pad_is_chainable((0, *index[1:]))

    def __ior__(self, other):
        if len(other):
            fg = self | other if len(self) else Graph(other)
            self.data = fg.data
            self._links = fg._links
        return self

    def __iadd__(self, other):

        if len(other):
            fg = self + other if len(self) else Graph(other)
            self.data = fg.data
            self._links = fg._links
        return self

    def __irshift__(self, other):

        if len(other):
            fg = self >> other if len(self) else Graph(other)
            self.data = fg.data
            self._links = fg._links
        return self
