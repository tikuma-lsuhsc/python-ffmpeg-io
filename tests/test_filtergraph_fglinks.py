import logging

logging.basicConfig(level=logging.INFO)

from ffmpegio.filtergraph.GraphLinks import GraphLinks
from pprint import pprint
import pytest


@pytest.mark.parametrize(
    ("dsts", "expects"),
    [
        (None, 0),
        ((1, 2, 3), 1),
        (((1, 2, 3), (4, 5, 6)), 2),
    ],
)
def test_iter_inpad_ids(dsts, expects):
    assert len(list(GraphLinks.iter_inpad_ids(dsts))) == expects


@pytest.mark.parametrize(
    ("args", "ok"),
    [
        (("0:v",), True),
        (("label",), True),
        ((0, True), True),
        ((0.0, True), False),
        ((0, False), False),
    ],
)
def test_validate_label(args, ok):
    if ok:
        GraphLinks.validate_label(*args)
    else:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.validate_label(*args)


@pytest.mark.parametrize(
    ("id", "ok"),
    [
        (None, True),
        ((0, 0, 0), True),
        ((0, 0, 0, 0), False),
        ((0, 0, "0"), False),
    ],
)
def test_validate_pad_idx(id, ok):
    if ok:
        GraphLinks.validate_pad_idx(id)
    else:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.validate_pad_idx(id)


@pytest.mark.parametrize(
    ("ids", "ok"),
    [
        (((0, 0, 0), None), True),
        (((0, 0, 0), None, None), False),
        ((None, None), False),
        (((None,), None), False),
        (((None,), (0, 0, 0)), False),
    ],
)
def test_validate_pad_idx_pair(ids, ok):
    if ok:
        GraphLinks.validate_pad_idx_pair(ids)
    else:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.validate_pad_idx_pair(ids)


@pytest.mark.parametrize(
    ("label", "ids", "ok"),
    [
        ("label", ((0, 0, 0), None), True),
        ("0:v", ((0, 0, 0), None), True),
        ("0:v", ((0, 0, 0), (0, 0, 0)), False),
        (0, ((0, 0, 0), None), False),
    ],
)
def test_validate_item(label, ids, ok):
    if ok:
        GraphLinks.validate_item(label, ids)
    else:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.validate_item(label, ids)


@pytest.mark.parametrize(
    ("data", "ok"),
    [
        ({"label": ((0, 0, 0), None)}, True),
        ({"label": ((0, 0, 0), None), "label1": ((0, 0, 0), None)}, False),
    ],
)
def test_validate(data, ok):
    if ok:
        GraphLinks.validate(data)
    else:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.validate(data)


@pytest.mark.parametrize(
    ("args", "expects"),
    [
        (((0, 0, 0), None), ((0, 0, 0), None)),
        (([(0, 0, 0), (1, 0, 0)], None), (((0, 0, 0), (1, 0, 0)), None)),
        (((0, 0, 0), None, lambda id: (id[0] + 1, *id[1:])), ((1, 0, 0), None)),
        (
            ((0, 0, 0), (0, 0, 0), lambda id: (id[0] + 1, *id[1:])),
            ((1, 0, 0), (1, 0, 0)),
        ),
    ],
)
def test_format_value(args, expects):
    if expects is None:
        with pytest.raises(GraphLinks.Error):
            GraphLinks.format_value(*args)
    else:
        assert GraphLinks.format_value(*args) == expects


# fixture links with one of each type of link items
@pytest.fixture()
def base_links():
    yield GraphLinks(
        {
            "l": ((0, 0, 0), (0, 0, 0)),  # regular link
            0: ((1, 1, 0), (0, 1, 0)),  # unnamed link
            "in": ((2, 1, 0), None),  # named input
            "0:v": ([(3, 0, 0), (3, 1, 0)], None),  # named inputs
            "out": (None, (1, 1, 0)),  # named output
            "sout1": (None, (6, 0, 0)),  # split output label#2
            "sout2": ((2, 0, 0), (1, 0, 0)),  # split output label#2
        }
    )


def test_init(base_links):
    GraphLinks()
    base_links


@pytest.mark.parametrize(
    ("labels", "expects"),
    [
        ([0, 3, None], [0, 1, 2]),
        (["a", "b"], ["a", "b"]),
    ],
)
def test_resolve_label(labels, expects):
    links = GraphLinks()

    def update(label):
        links.data[links._resolve_label(label)] = None

    for label in labels:
        update(label)

    assert list(links.keys()) == expects


def test_iter_links(base_links):
    res = {
        ("l", (0, 0, 0), (0, 0, 0)),  # regular link
        (0, (1, 1, 0), (0, 1, 0)),  # unnamed link
        ("sout2", (2, 0, 0), (1, 0, 0)),  # split output label#2
    }

    for v in base_links.iter_links():
        assert v in res
        res.discard(v)

    assert not len(res)


def test_iter_inputs(base_links):
    res = {
        ("in", (2, 1, 0)),  # regular link
        ("0:v", (3, 0, 0)),  # unnamed link
        ("0:v", (3, 1, 0)),  # split output label#2
    }

    for v in base_links.iter_inputs(exclude_stream_specs=False):
        assert v in res
        res.discard(v)

    assert not len(res)


def test_iter_outputs(base_links):
    res = {
        ("out", (1, 1, 0)),  # regular link
        ("sout1", (6, 0, 0)),  # split output label#2
    }

    for v in base_links.iter_outputs():
        assert v in res
        res.discard(v)

    assert not len(res)


def test_iter_input_pads(base_links):
    res = {
        ("l", (0, 0, 0), (0, 0, 0)),  # regular link
        (0, (1, 1, 0), (0, 1, 0)),  # unnamed link
        ("in", (2, 1, 0), None),  # named input
        ("0:v", (3, 0, 0), None),  # named inputs
        ("0:v", (3, 1, 0), None),  # named inputs
        ("out", None, (1, 1, 0)),  # named output
        ("sout1", None, (6, 0, 0)),  # split output label#1
        ("sout2", (2, 0, 0), (1, 0, 0)),  # split output label#2
    }

    for v in base_links.iter_input_pads():
        assert v in res
        res.discard(v)

    assert not len(res)


@pytest.mark.parametrize(
    ("key", "expects"),
    [
        ("l", ((0, 0, 0), (0, 0, 0))),
        ((1, 1, 0), (0, (0, 1, 0))),
    ],
)
def test__getitem__(key, expects, base_links):

    assert base_links[key] == expects


@pytest.mark.parametrize(
    ("key", "expects"),
    [("l", 0), (0, 0), ("in", 1), ("0:v", 1), ("sout1", 2)],
)
def test_label_checks(key, expects, base_links):

    for i, ans in enumerate(
        [base_links.is_linked(key), base_links.is_input(key), base_links.is_output(key)]
    ):

        assert ans == (i == expects)


@pytest.mark.parametrize(
    ("id", "label", "input"),
    [
        ((0, 0, 0), "l", False),
        ((2, 1, 0), "in", True),
        (None, None, False),
        ((10, 0, 0), None, False),
    ],
)
def test_find_inpad_labels(id, label, input, base_links):
    retval = base_links.find_inpad_label(id)
    assert retval == label if label else retval is None


@pytest.mark.parametrize(
    ("id", "label", "output"),
    [
        ((0, 0, 0), "l", False),
        ((1, 1, 0), "out", "out"),
        (None, None, False),
    ],
)
def test_find_outpad_labels(id, label, output, base_links):

    retval = base_links.find_outpad_label(id)
    if label is None:
        assert retval is None
    else:
        assert retval == (label if label else [])


@pytest.mark.parametrize(
    ("dst", "src", "res"),
    [
        ((0, 0, 0), (0, 0, 0), True),  # regular link
        ((1, 1, 0), (0, 1, 0), True),  # unnamed link
        ((1, 1, 0), (0, 1, 1), False),  # unnamed link
        ((2, 1, 0), None, False),  # named input
        ((3, 0, 0), None, False),  # named inputs
    ],
)
def test_links(src, dst, res, base_links):

    assert base_links.are_linked(dst, src) == res


def test_unlink(base_links):
    base_links.unlink(label="l")
    assert "l" not in base_links

    base_links.unlink(outpad=(6, 0, 0))
    assert "sout1" not in base_links

    base_links.unlink(inpad=(1, 1, 0))
    assert 0 not in base_links

    base_links.unlink(inpad=(3, 0, 0))
    assert "0:v" in base_links  # the other input still present


@pytest.mark.parametrize(
    ("args", "ok", "unlinked"),
    [
        # fmt:off
        (((4, 0, 0), (4, 0, 0)), 1, None),  # no conflict
        (((4, 0, 0), (4, 0, 0), "test"), "test", None),  # no conflict
        (((4, 0, 0), (4, 0, 0), 0), 1, None),  # no conflict, label number mod
        ((None, (4, 0, 0)), False, None),  # can't do output label
        (((4, 0, 0), None), False, None),  # can't do input label
        (((4, 0, 0), (0, 0, 0)), False, None),  # duplicate src not ok
        (((0, 0, 0), (4, 0, 0)), False, None),  # duplicate dst not ok
        (((0, 0, 0), (4, 0, 0), None, False, True), 1, "l"),  # forced
        (((0, 0, 0), (4, 0, 0), None, True), False, "1"),  # bad src
        (((2, 1, 0), (4, 0, 0)), 1, None),  # links to not inherit 'in' input label
        (((2, 1, 0), (4, 0, 0), None, 'input'),  "in", None),  # links to inherit 'in' input label
        (((4, 0, 0), (1, 1, 0), None, 'output'), "out", None),  # links to inherit 'out' output label
        (((4, 0, 0), (1, 1, 0), None, True), 1, None),  # new label
        (((3, 0, 0), (4, 0, 0)), 1, None),
        # links to inherit 'in' input label
        # fmt:on
    ],
)
def test_link(args, ok, unlinked, base_links):

    # link(label=None, dst, src, force=False, validate=True):
    if ok:
        label = base_links.link(*args)  # ok link
        assert label == ok
        assert base_links.are_linked(*args[:2])
        if unlinked:
            assert unlinked not in base_links

    else:
        with pytest.raises(GraphLinks.Error):
            base_links.link(*args)


@pytest.mark.parametrize(
    ("args", "ok", "unlinked"),
    [
        (("test", (4, 0, 0)), "test", None),  # no conflict
        (("test", None, (4, 0, 0)), "test", None),  # no conflict
        (("test", (2, 1, 0)), False, None),  # existing input
        (("test", (0, 0, 0)), False, None),  # existing link dst
        (("test", (0, 0, 0), None, True), "test", "l"),  # existing link dst
        (("in", (2, 1, 0)), False, None),  # existing input
        (("0:v", (4, 2, 1)), "0:v", None),  # new input, number adjusted
    ],
)
def test_create_label(args, ok, unlinked, base_links):

    # create_label(label, dst=None, src=None, force):
    if ok:
        label = base_links.create_label(*args)  # ok link
        assert label == ok
        if unlinked:
            assert unlinked not in base_links

    else:
        with pytest.raises(GraphLinks.Error):
            base_links.create_label(*args)


def test_update(base_links):

    base_links.update({})  # no action``

    base_links.update({"test": ((4, 0, 0), (4, 0, 0))})
    assert base_links["test"] == ((4, 0, 0), (4, 0, 0))
    assert "test" in base_links

    # existing dst
    with pytest.raises(GraphLinks.Error):
        base_links.update({"test": ((4, 0, 0), (4, 0, 0))})
    
    base_links.update({"l": ((4, 0, 0), (4, 0, 0))}, force=True)
    assert base_links["l"] == ((4, 0, 0), (4, 0, 0))

    base_links.update({"in": (None, (5, 0, 0))}, auto_link=True)
    assert base_links["in"] == ((2, 1, 0), (5, 0, 0))
    base_links.update({"out": ((6, 0, 0), None)}, auto_link=True)
    assert base_links["out"] == ((6, 0, 0), (1, 1, 0))


@pytest.mark.parametrize(
    ("links", "n", "nin"),
    [
        ((None, (0, 0, 0)), 0, 0),
        (((0, 0, 0), None), 0, 0),
        ((((0, 0, 0), (0, 0, 1)), None), 0, 0),
    ],
)
def test_remove_label(links, n, nin):
    label = "label" if links[0] is None or len(links[0]) < 2 else "0:v"
    o = GraphLinks({label: links})
    o.remove_label(label)
    assert len(o) == n
    assert len(list(o.iter_inputs())) == nin


# base_links
# "l": ((0, 0, 0), (0, 0, 0)),  # regular link
# 0: ((1, 1, 0), (0, 1, 0)),  # unnamed link
# "in": ((2, 1, 0), None),  # named input
# "0:v": ([(3, 0, 0), (3, 1, 0)], None),  # named inputs
# "out": (None, (1, 1, 0)),  # named output
# "sout1": (None, (1, 0, 0)),  # split output label#1
# "sout2": ((2, 0, 0), (1, 0, 0)),  # split output label#2
