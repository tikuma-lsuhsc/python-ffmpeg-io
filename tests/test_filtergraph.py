from os import path
from tempfile import TemporaryDirectory
from ffmpegio import ffmpegprocess, filtergraph as fgb
from ffmpegio.filtergraph import Chain
from pprint import pprint
import pytest


@pytest.mark.parametrize(
    "expr, pad, filter, chain, exclude_chainable, chainable_first, include_connected, unlabeled_only, ret",
    [
        # fmt: off
        ("[0:v][1:v]vstack", None, None, None, False, False, False, False, []),
        ("[0:v][1:v]vstack", None, None, None, False, False, True, False, [(0,0,0),(0,0,1)]),
        ("[0:v][in]vstack,split[out];[out]vstack", None, None, None, False, False, False, False, [(0,0,1),(1,0,1)]),
        ("[0:v][in]vstack,split[out];[out]vstack", None, None, 0, False, False, False, False, [(0,0,1)]),
        ("[0:v][in]vstack,split[out];[out]vstack", None, None, 1, False, False, False, False, [(1,0,1)]),
        ("[0:v][in]vstack,split[out];[out]vstack", None, None, 2, False, False, False, False, None),
        ("[0:v][in]vstack,split[out];[out]vstack", None, None, None, False, False, False, True, [(1,0,1)]),
        # fmt: on
    ],
)
def test_iter_input_pads(
    expr,
    pad,
    filter,
    chain,
    exclude_chainable,
    chainable_first,
    include_connected,
    unlabeled_only,
    ret,
):

    fg = fgb.Graph(expr)

    out_links = fg._links.output_dict()

    it = fg.iter_input_pads(
        pad,
        filter,
        chain,
        exclude_chainable=exclude_chainable,
        chainable_first=chainable_first,
        include_connected=include_connected,
        unlabeled_only=unlabeled_only,
    )

    if ret is None:
        with pytest.raises(fgb.FiltergraphInvalidIndex):
            next(it)
    else:
        for r in ret:
            index, f, out_index = next(it)
            assert index == r and f == fg[r[0]][r[1]]
            if isinstance(out_index, tuple):
                assert out_index in out_links


@pytest.mark.parametrize(
    "expr, pad, filter, chain, exclude_chainable, chainable_first, include_connected, unlabeled_only, ret",
    [
        # fmt: off
        ("split[out0][out1]", None, None, None, False, False, False, False, [(0,0,0),(0,0,1)]),
        ("split[out0][out1]", None, None, None, False, False, False, True, []),
        # fmt: on
    ],
)
def test_iter_output_pads(
    expr,
    pad,
    filter,
    chain,
    exclude_chainable,
    chainable_first,
    include_connected,
    unlabeled_only,
    ret,
):

    fg = fgb.Graph(expr)

    in_links = fg._links.input_dict()

    it = fg.iter_output_pads(
        pad,
        filter,
        chain,
        exclude_chainable=exclude_chainable,
        chainable_first=chainable_first,
        include_connected=include_connected,
        unlabeled_only=unlabeled_only,
    )

    if ret is None:
        with pytest.raises(fgb.FiltergraphInvalidIndex):
            next(it)
    else:
        for r in ret:
            index, f, in_index = next(it)
            assert index == r and f == fg[r[0]][r[1]]
            if isinstance(in_index, tuple):
                assert in_index in in_links


@pytest.mark.parametrize(
    "expr, skip_if_no_input, skip_if_no_output, chainable_only, ret",
    [
        ("fps;scale", False, False, False, 2),
        ("fps;scale", True, True, True, 2),
        ("nullsrc;fps", False, False, False, 2),
        ("nullsrc;fps", True, False, False, 1),
        ("fps;nullsink", False, False, False, 2),
        ("fps;nullsink", False, True, False, 1),
        ("split[L1][L2];[L2]fps", True, False, False, 1),
        ("split[L1][L2];[L2]fps", False, True, False, 1),
        ("split[L1][L2];[L2]fps", False, True, True, 1),
    ],
)
def test_iter_chains(expr, skip_if_no_input, skip_if_no_output, chainable_only, ret):
    f = fgb.Graph(expr)
    chains = [*f.iter_chains(skip_if_no_input, skip_if_no_output, chainable_only)]
    assert len(chains) == ret


@pytest.mark.parametrize(
    "index_or_label, ret, is_input, chain_id_omittable, filter_id_omittable, pad_id_omittable, resolve_omitted, chain_fill_value, filter_fill_value, pad_fill_value, chainable_first",
    [
        ("in", (2, 0, 0), True, False, False, False, False, None, None, None, False),
        ("[in]", (2, 0, 0), True, False, False, False, False, None, None, None, False),
    ],
)
def test_resolve_pad_index(
    index_or_label,
    ret,
    is_input,
    chain_id_omittable,
    filter_id_omittable,
    pad_id_omittable,
    resolve_omitted,
    chain_fill_value,
    filter_fill_value,
    pad_fill_value,
    chainable_first,
):
    fg = fgb.Graph(
        "color;scale,pad[l1];[in]crop,pad[l2];overlay;[l1]overlay;pad,overlay[l3];trim,[l2]overlay,split=3[l4];[l3][l4]overlay"
    )

    if ret is None:
        with pytest.raises(fgb.FiltergraphPadNotFoundError):
            fg.resolve_pad_index(
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
    else:
        assert (
            fg.resolve_pad_index(
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
            == ret
        )

    # assert fg.resolve_pad_index(True, None) == (1, 0, 0)
    # assert fg.resolve_pad_index(True, "in") == (2, 0, 0)
    # assert fg.resolve_pad_index(True, "[in]") == (2, 0, 0)
    # assert fg.resolve_pad_index(True, 1) == (3, 0, 1)
    # assert fg.resolve_pad_index(True, (1, None)) == (5, 1, 0)

    # pprint(fg._resolve_index(False, 0))
    # pprint(fg._resolve_index(False, 1))


@pytest.mark.parametrize(
    "fg,fc,left_on,right_on,out",
    [
        ("fps;crop", "trim", None, None, "[UNC0]fps,trim[UNC2];[UNC1]crop[UNC3]"),
        ("fps[out];crop", "trim", None, None, "[UNC0]fps,trim[UNC2];[UNC1]crop[UNC3]"),
        ("fps;crop", "trim", (1, 0, 0), None, "[UNC0]fps[UNC2];[UNC1]crop,trim[UNC3]"),
        ("fps;crop[out]", "trim", "out", None, "[UNC0]fps[UNC2];[UNC1]crop,trim[UNC3]"),
        (
            fgb.Graph(["fps", "crop"], {"out": ((1, 0, 0), (0, 0, 0))}),
            "trim",
            "out",
            None,
            None,
        ),
        (
            fgb.Graph(["fps", "crop"], {"out": (None, (0, 0, 0))}),
            "trim",
            "out",
            None,
            "[UNC0]fps,trim[UNC2];[UNC1]crop[UNC3]",
        ),
        ("fps[L];[L]crop", "trim", None, None, "[UNC0]fps[L];[L]crop,trim[UNC1]"),
        (
            "split=2[C];[C]crop",
            "trim",
            None,
            None,
            "[UNC0]split=2[C][L0];[C]crop[UNC1];[L0]trim[UNC2]",
        ),
        (
            "split=2[C][out];[C]crop",
            "trim",
            "out",
            None,
            "[UNC0]split=2[C][L0];[C]crop[UNC1];[L0]trim[UNC2]",
        ),
    ],
)
def test_attach(fg, fc, left_on, right_on, out):
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.FiltergraphPadNotFoundError):
            fg = fg.attach(fc, left_on, right_on)
    else:
        fg = fg.attach(fc, left_on, right_on)
        assert fg.compose() == out


@pytest.mark.parametrize(
    "right,left,right_on,out",
    [
        # fmt: off
        ("fps;crop", "trim", None, "[UNC0]trim,fps[UNC2];[UNC1]crop[UNC3]"),
        ("[in]fps;crop", "trim", None, "[UNC0]trim,fps[UNC2];[UNC1]crop[UNC3]"),
        ("fps;crop", "trim", (1, 0, 0), "[UNC0]fps[UNC2];[UNC1]trim,crop[UNC3]"),
        ("fps;[in]crop", "trim", "in", "[UNC0]fps[UNC2];[UNC1]trim,crop[UNC3]"),
        ("[L]fps;crop[L]", "trim", None, "[L]fps[UNC1];[UNC0]trim,crop[L]"),
        ("[C]overlay;crop[C]", "trim", None, "[UNC0]trim[L0];[C][L0]overlay[UNC2];[UNC1]crop[C]"),
        ("[C][in]overlay;crop[C]", "trim", "in", "[UNC0]trim[L0];[C][L0]overlay[UNC2];[UNC1]crop[C]"),
        # fmt: on
    ],
)
def test_rattach(right, left, right_on, out):
    fg = fgb.Graph(right)
    if out is None:
        with pytest.raises(fgb.Graph.Error):
            fg = fg.rattach(left, right_on=right_on)
    else:
        fg = fg.rattach(left, right_on=right_on)
        assert fg.compose() == out


@pytest.mark.parametrize(
    "fg, other, auto_link, replace_sws_flags, out",
    [
        (
            "fps;crop",
            "trim;scale",
            False,
            None,
            "[UNC0]fps[UNC4];[UNC1]crop[UNC5];[UNC2]trim[UNC6];[UNC3]scale[UNC7]",
        ),
        (
            "fps;crop",
            "trim,scale",
            False,
            None,
            "[UNC0]fps[UNC3];[UNC1]crop[UNC4];[UNC2]trim,scale[UNC5]",
        ),
        (
            "[la]fps;crop[lb]",
            "[lb]trim;scale[la]",
            False,
            None,
            None,
        ),
        (
            "[la]fps;crop[lb]",
            "[lb]trim;scale[la]",
            True,
            None,
            "[la]fps[UNC2];[UNC0]crop[lb];[lb]trim[UNC3];[UNC1]scale[la]",
        ),
        ("sws_flags=w=200;fps;crop", "sws_flags=h=400;trim;scale", False, None, None),
        (
            "sws_flags=w=200;fps;crop",
            "sws_flags=h=400;trim;scale",
            False,
            False,
            "sws_flags=w=200;[UNC0]fps[UNC4];[UNC1]crop[UNC5];[UNC2]trim[UNC6];[UNC3]scale[UNC7]",
        ),
        (
            "sws_flags=w=200;fps;crop",
            "sws_flags=h=400;trim;scale",
            False,
            True,
            "sws_flags=h=400;[UNC0]fps[UNC4];[UNC1]crop[UNC5];[UNC2]trim[UNC6];[UNC3]scale[UNC7]",
        ),
    ],
)
def test_stack(fg, other, auto_link, replace_sws_flags, out):
    # other, auto_link=False, replace_sws_flags=None,
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.Graph.Error):
            fg = fg.stack(other, auto_link, replace_sws_flags)
    else:
        fg = fg.stack(other, auto_link, replace_sws_flags)
        assert fg.compose() == out


@pytest.mark.parametrize(
    "fg, id, out",
    [
        # fmt: off
        ("fps;crop", (0,0,0), ((0,0,0),None)),
        ("fps;crop", (1,0,0), ((1,0,0),None)),
        ("fps;crop", (0,1,0), None),
        ("fps;crop", 'fake', None),
        ("[la]fps;crop[lb]", 'la', ((0,0,0),'la')),
        ("[la]fps;crop[lb]", 'lb', None),
        ("[0:v]fps;[0:v]crop", (0,0,0), None),
        ("[0:v]fps;[0:v]crop", '0:v', None),
        # fmt: on
    ],
)
def test_get_input_pad(fg, id, out):
    # other, auto_link=False, replace_sws_flags=None,
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.FiltergraphPadNotFoundError):
            fg.get_input_pad(id)
    else:
        assert fg.get_input_pad(id) == out


@pytest.mark.parametrize(
    "fg, id, out",
    [
        # fmt: off
        ("fps;crop", (0,0,0), ((0,0,0),None)),
        ("fps;crop", (1,0,0), ((1,0,0),None)),
        ("fps;crop", (0,1,0), None),
        ("fps;crop", 'fake', None),
        ("[la]fps;crop[lb]", 'lb', ((1,0,0),'lb')),
        ("[la]fps;crop[lb]", 'la', None),
        # TODO: test split output case
        # fmt: on
    ],
)
def test_get_output_pad(fg, id, out):
    # other, auto_link=False, replace_sws_flags=None,
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.FiltergraphPadNotFoundError):
            fg.get_output_pad(id)
    else:
        assert fg.get_output_pad(id) == out


@pytest.mark.parametrize(
    "fg, r, to_l,to_r,chain, out",
    [
        # fmt: off
        ("[a1]fps;crop[b]", "[c]trim;scale[d1]", ['b'], ['c'], None, "[a1]fps[UNC2];[UNC0]crop[L0];[L0]trim[UNC3];[UNC1]scale[d1]"),
        ("[la]fps;crop[lb]", "[lb]trim;scale[la]", ['lb'], ['lb'], None, "[la]fps[UNC2];[UNC0]crop[L0];[L0]trim[UNC3];[UNC1]scale[UNC4]"),
        ("[a1]fps;crop[b]", "[c]trim;scale[d1]", ['b'], ['c'], True, "[a1]fps[UNC2];[UNC0]crop[L0];[L0]trim[UNC3];[UNC1]scale[d1]"),
        # fmt: on
    ],
)
def test_connect(fg, r, to_l, to_r, chain, out):
    # other, auto_link=False, replace_sws_flags=None,
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.Graph.Error):
            fg = fg.connect(r, to_l, to_r, chain)
    else:
        fg = fg.connect(r, to_l, to_r, chain_siso=chain)
        assert fg.compose() == out


@pytest.mark.parametrize(
    "fg, r, how, unlabeled_only, out",
    [
        # fmt: off
        ("fps;crop", "trim;scale", None, False, "[UNC0]fps,trim[UNC2];[UNC1]crop,scale[UNC3]"),
        ("[in1]fps;crop[ou1]", "[in2]trim;scale[out2]", None, True, "[in1]fps[L0];[UNC0]crop[ou1];[in2]trim[UNC1];[L0]scale[out2]"),
        ("fps", "overlay", 'per_chain', False, "[UNC0]fps[L0];[L0][UNC1]overlay[UNC2]"),
        # fmt: on
    ],
)
def test_join(fg, r, how, unlabeled_only, out):
    # other, auto_link=False, replace_sws_flags=None,
    fg = fgb.Graph(fg)
    if out is None:
        with pytest.raises(fgb.Graph.Error):
            fg = fg.join(r, how, unlabeled_only=unlabeled_only)
    else:
        fg = fg.join(r, how, unlabeled_only=unlabeled_only)
        assert fg.compose() == out


def test_iter():
    fg = fgb.Graph("[0:v][1:v]vstack=inputs=2,split=outputs=2")
    [*fg.iter_output_pads(pad=1, full_pad_index=True)]


# @pytest.mark.parametrize(
#     "f1e,f2",
#     [
#         ("fps", "trim"),
#         ("fps", "trim,crop"),
#         ("fps", "trim;crop"),
#         ("fps,scale", "trim"),
#         ("fps,scale", "trim,crop"),
#         ("fps,scale", "trim;crop"),
#         ("fps;scale", "trim"),
#         ("fps;scale", "trim,crop"),
#         ("fps;scale", "trim;crop"),
#     ],
# )
def test_filter_arithmetics():
    fg1 = fgb.trim() + fgb.crop()
    assert isinstance(fg1, fgb.Chain)
    assert fg1.compose() == "trim,crop"

    fg2 = fgb.fps() | fgb.scale()
    assert isinstance(fg2, fgb.Graph)
    assert fg2.compose() == "[UNC0]fps[UNC2];[UNC1]scale[UNC3]"

    fg3 = fgb.setpts() * 3
    assert isinstance(fg3, fgb.Graph)
    assert fg3.compose() == "[UNC0]setpts[UNC3];[UNC1]setpts[UNC4];[UNC2]setpts[UNC5]"

    assert (("[in]" >> fgb.geq()) >> "[out]").compose() == "[in]geq[out]"
    assert ("[in]" >> (fgb.geq() >> "[out]")).compose() == "[in]geq[out]"

    assert (
        ["[0:v]", "[1:v]"] >> fgb.vstack(inputs=2)
    ).compose() == "[0:v][1:v]vstack=inputs=2[UNC0]"
    assert (
        fgb.split(2) >> [(1, "[main]"), "[sub]"]
    ).compose() == "[UNC0]split=2[sub][main]"
    fc = fgb.vstack(inputs=2) + fgb.split(outputs=2)
    assert (
        [("[0:v]", 1), "[1:v]"] >> fc
    ).compose() == "[1:v][0:v]vstack=inputs=2,split=outputs=2[UNC0][UNC1]"
    assert (
        fc >> ["[main]", "[sub]"]
    ).compose() == "[UNC0][UNC1]vstack=inputs=2,split=outputs=2[main][sub]"
    assert (
        ["[0:v]", "[1:v]"] >> fgb.Graph(fc) >> [(1, "[main]"), "[sub]"]
    ).compose() == "[0:v][1:v]vstack=inputs=2,split=outputs=2[sub][main]"

    fg1 = fgb.trim() >> fgb.crop()
    assert fg1.compose() == "trim,crop"

    fg1 = "trim" >> fgb.crop()
    assert fg1.compose() == "trim,crop"


def test_filter_empty_handling():
    fg1 = fgb.trim() + fgb.crop()
    fg2 = fgb.fps() | fgb.scale()
    fg3 = fgb.Chain()
    fg4 = fgb.Graph()

    assert (fg3 * 2).compose() == ""
    assert (fg4 * 2).compose() == ""

    assert (fg1 + fg3).compose() == "trim,crop"
    assert (fg1 | fg3).compose() == "trim,crop"

    assert (fg2 + fg3).compose() == "[UNC0]fps[UNC2];[UNC1]scale[UNC3]"
    assert (fg2 | fg3).compose() == "[UNC0]fps[UNC2];[UNC1]scale[UNC3]"


def test_script():
    fg = fgb.Graph("trim=duration=1")
    with fg.as_script_file() as script, TemporaryDirectory() as dir:
        out = ffmpegprocess.run(
            {
                "inputs": [(path.join("tests", "assets", "sample.mp4"), None)],
                "outputs": [
                    (path.join(dir, "output.mp4"), {"filter_script:v": script})
                ],
            },
        )
    assert not out.returncode


def test_ops():
    assert (
        Chain("scale") + "overlay"
    ).compose() == "[UNC0]scale[L0];[L0][UNC1]overlay[UNC2]"
    assert (
        "scale" + Chain("overlay")
    ).compose() == "[UNC0]scale[L0];[L0][UNC1]overlay[UNC2]"


def test_readme():
    """make sure readme example works as advertized"""

    v0 = "[0]" >> fgb.trim(start_frame=10, end_frame=20)
    v1 = "[0]" >> fgb.trim(start_frame=30, end_frame=40)
    v3 = "[1]" >> fgb.hflip()
    v2 = (v0 | v1) + fgb.concat(2)
    v5 = (
        (v2 | v3)
        + fgb.overlay(eof_action="repeat")
        + fgb.drawbox(50, 50, 120, 120, "red", t=5)
    )
    print(v5)
    assert v5.get_num_inputs() == 0
    #    <ffmpegio.filtergraph.Graph object at 0x1e67f955b80>
    #        FFmpeg expression: "[0]trim=start_frame=10:end_frame=20[L0];[0]trim=start_frame=30:end_frame=40[L1];[L0][L1]concat=2[L2];[1]hflip[L3];[L2][L3]overlay=eof_action=repeat,drawbox=50:50:120:120:red:t=5"


if __name__ == "__main__":
    from pprint import pprint

    from ffmpegio.filtergraph import Graph, filter_info, FFmpegioError, list_filters

    for k, v in list_filters().items():
        if v.num_inputs is None or v.num_inputs:
            continue
        info = filter_info(k)
        # print(info.options)
        d = None
        for o in info.options:
            if o.name == "duration" or "duration" in o.aliases:
                d = o.default
                print(f"{k} found {o.name} option with default {d} ")
                break

        if d is None:
            print(
                f"{k} has no apparent duration option:\n{[o.name for o in info.options]}"
            )

    pprint(srcs)
    exit()
    type = "audio"
    expr = "aevalsrc"

    fg = Graph(expr)
    # if len(fg) != 1 or len(fg[0]) != 1:
    #     # multi-filter input filtergraph, cannot take arguments
    #     return expr, args, kwargs

    f = fg[0][0]
    info = filter_info(f.name)
    print(info)
    if info.inputs is not None:
        raise FFmpegioError(f"{f.name} filter is not a source filter")

    opts = info.options

    print(len(opts))
