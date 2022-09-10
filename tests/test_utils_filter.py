import logging
from ffmpegio.caps import filters

logging.basicConfig(level=logging.INFO)

from ffmpegio.utils import filter as filter_utils
from pprint import pprint
import pytest


def test_parse_filter():
    f = "loudnorm"
    assert filter_utils.parse_filter(f) == ("loudnorm",)

    f = "loudnorm "
    assert filter_utils.parse_filter(f) == ("loudnorm",)

    with pytest.raises(ValueError):
        f = "loudnorm,"
        filter_utils.parse_filter(f) == ("loudnorm",)

    f = "loudnorm=print_format=summary:linear=true"
    print(filter_utils.parse_filter(f))

    with pytest.raises(ValueError):
        f = "loudnorm=print_format=summary:linear=true:"
        filter_utils.parse_filter(f)

    with pytest.raises(ValueError):
        f = "loudnorm=print_format=summary:linear=true,"
        filter_utils.parse_filter(f)

    f = "scale=iw/2:-1"
    print(filter_utils.parse_filter(f))

    f = r"select='eq(pict_type,I)'"
    print(filter_utils.parse_filter(f))

    f = r"drawtext=fontfile=/usr/share/fonts/truetype/DroidSans.ttf: timecode=09\:57\:00\:00: r=25: x=(w-tw)/2: y=h-(2*lh): fontcolor=white: box=1: boxcolor=0x00000000@1"
    print(filter_utils.parse_filter(f))


def test_compose_filter():
    f = "loudnorm"
    print(filter_utils.compose_filter("loudnorm"))

    f = "loudnorm=print_format=summary:linear=true"
    print(
        filter_utils.compose_filter(
            "loudnorm", dict(print_format="summary", linear=True)
        )
    )

    f = "scale=iw/2:-1"
    print(filter_utils.compose_filter("scale", "iw/2", -1))

    f = r"select='eq(pict_type\,I)'"
    print(filter_utils.compose_filter("select", "eq(pict_type,I)"))

    f = r"drawtext=fontfile=/usr/share/fonts/truetype/DroidSans.ttf: timecode='09\:57\:00\:00': r=25: \
    x=(w-tw)/2: y=h-(2*lh): fontcolor=white: box=1: boxcolor=0x00000000@1"

    print(
        filter_utils.compose_filter(
            "drawtext",
            dict(
                fontfile="/usr/share/fonts/truetype/DroidSans.ttf",
                timecode="09:57:00:00",
                r=25,
                x="(w-tw)/2",
                y="h-(2*lh)",
                fontcolor="white",
                box=1,
                boxcolor="0x00000000@1",
            ),
        )
    )


def test_get_filter_option_value():

    assert filter_utils.get_filter_option_value(("concat", {"n": 3}), "n") == 3
    assert filter_utils.get_filter_option_value(("concat", 3), "n") == 3
    assert filter_utils.get_filter_option_value(("concat",), "n") == 2
    assert filter_utils.get_filter_option_value((["concat", "one"],), "n") == 2
    assert filter_utils.get_filter_option_value(("fade",), "t") == "in"


def test_get_num_filter_inputs():

    assert filter_utils.get_num_filter_inputs(("overlay",)) == 2
    assert filter_utils.get_num_filter_inputs((["overlay", "no1"],)) == 2

    for fspec, ans in [
        (("hstack", {"inputs": 4}), 4),
        (("afir", {"nbirs": 1}), 2),
        (("concat", {"n": 3}), 3),
        (("decimate", {"ppsrc": 1}), 2),
        (("fieldmatch", {"ppsrc": 1}), 2),
        (("headphone", "FL|FR|FC|LFE|BL|BR|SL|SR"), 9),
        (("headphone", ["FL", "FR"]), 3),
        (("headphone", {"map": "FL|FR|FC|LFE|BL|BR|SL|SR", "hrir": "multich"}), 2),
        (("interleave", {"nb_inputs": 2}), 2),
        (("mergeplanes", "0x001020", "yuv444p"), 3),
        (("mergeplanes", "0x00010210", "yuv444p"), 2),
        (("premultiply", {"inplace": 1}), 1),
        (("unpremultiply", {"inplace": 0}), 2),
        (("signature", {"nb_inputs": 2}), 2),
    ]:
        if fspec[0] in filters():
            assert filter_utils.get_num_filter_inputs(fspec) == ans
        else:
            logging.info(f'{fspec[0]} not available in the interfaced version of FFmpeg')


def test_get_num_filter_outputs():

    assert filter_utils.get_num_filter_outputs(("split",)) == 2
    assert filter_utils.get_num_filter_outputs((["split", "no1"],)) == 2

    for fspec, ans in [
        (("acrossover", {"split": "1500 8000", "order": "8th"}), 3),
        (("afir", {"response": 0}), 1),
        (("aiir", {"response": 1}), 2),
        (("anequalizer", {"curves": 1}), 2),
        (("asegment", {"timestamps": [60, 150]}), 3),
        (("segment", "60|150"), 3),
        (("astreamselect", {"map": "1  0  2"}), 3),
        # (("streamselect",), None),
        (("channelsplit", {"channel_layout": "5.1"}), 6),
        (("extractplanes", "y+u+v"), 3),
        (("ebur128", {"video": 1}), 2),
        (("aphasemeter", {"video": 0}), 1),
        (("concat", {"n": 3, "v": 1, "a": 2}), 3),
        # "amovie": (None, None),  # streams(+-separated)
        # "movie": (None, None),  # streams(+-separated)
        (("movie", "dvd.vob", {"s": "v:0+#0x81"}), 2),
    ]:
        if fspec[0] in filters():
            assert filter_utils.get_num_filter_outputs(fspec) == ans
        else:
            logging.info(f'{fspec[0]} not available in the interfaced version of FFmpeg')


def test_compose_graph():
    f = "yadif=0:0:0,scale=iw/2:-1"
    pprint(filter_utils.compose_graph([[("yadif", 0, 0, 0), ("scale", "iw/2", -1)]]))

    f = "[1:v]negate[a]; \
    [2:v]hflip[b]; \
    [3:v]edgedetect[c]; \
    [0:v][a]hstack=inputs=2[top]; \
    [b][c]hstack=inputs=2[bottom]; \
    [top][bottom]vstack=inputs=2[out]"

    pprint(
        filter_utils.compose_graph(
            [
                [("negate",)],  # chain #0
                [("hflip",)],  # chain #1
                [("edgedetect",)],  # chain #2
                [("hstack", {"inputs": 2})],  # chain #3
                [("hstack", {"inputs": 2})],  # chain #4
                [("vstack", {"inputs": 2})],  # chain #5
            ],
            {
                "1:v": [(0, 0, 0), None],  # feeds to negate
                "2:v": [(1, 0, 0), None],  # feeds to hflip
                "3:v": [(2, 0, 0), None],  # feeds to edgedetect
                "0:v": [(3, 0, 0), None],  # feeds to the 1st input of 1st hstack
                "out": [None, (5, 0, 0)],  # feeds from vstack output
                0: [(3, 0, 1), (0, 0, 0)],  # 1st hstack gets its 2nd input from negate
                1: [(4, 0, 0), (1, 0, 0)],  # 2nd hstack gets its 1st input from hflip
                2: [(4, 0, 1), (2, 0, 0)],  # 2nd hstack its 2nd input <- edgedetect
                3: [(5, 0, 0), (3, 0, 0)],  # vstack gets its 1st input from 1st hstack
                4: [(5, 0, 1), (4, 0, 0)],  # vstack gets its 2nd input from 2nd hstack
            },
        )
    )

    f = "[0:v]pad=iw*2:ih*2[a]; \
    [1:v]negate[b]; \
    [2:v]hflip[c]; \
    [3:v]edgedetect[d]; \
    [a][b]overlay=w[x]; \
    [x][c]overlay=0:h[y]; \
    [y][d]overlay=w:h[out]"

    pprint(
        filter_utils.compose_graph(
            [
                [("pad", "iw*2", "ih*2")],
                [("negate",)],
                [("hflip",)],
                [("edgedetect",)],
                [("overlay", "w")],
                [("overlay", "0", "h")],
                [("overlay", "w", "h")],
            ],
            {
                "0:v": [(0, 0, 0), None],
                "1:v": [(1, 0, 0), None],
                "2:v": [(2, 0, 0), None],
                "3:v": [(3, 0, 0), None],
                "out": [None, (6, 0, 0)],
                0: [(4, 0, 0), (0, 0, 0)],
                1: [(4, 0, 1), (1, 0, 0)],
                2: [(5, 0, 0), (4, 0, 0)],
                3: [(5, 0, 1), (2, 0, 0)],
                4: [(6, 0, 0), (5, 0, 0)],
                5: [(6, 0, 1), (3, 0, 0)],
            },
        )
    )

    f = "[0:v]hflip,setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]overlay"

    pprint(
        filter_utils.compose_graph(
            [
                [("hflip",), ("setpts", "PTS-STARTPTS")],
                [("setpts", "PTS-STARTPTS")],
                [("overlay",)],
            ],
            {
                "0:v": [(0, 0, 0), None],
                "1:v": [(1, 0, 0), None],
                0: [(2, 0, 0), (0, 1, 0)],
                1: [(2, 0, 1), (1, 0, 0)],
            },
        )
    )


if __name__ == "__main__":
    from ffmpegio.utils.filter import FilterGraph
    from pprint import pprint

    print(
        filter_utils.video_basic_filter(
            fill_color=None,
            remove_alpha=None,
            crop=None,
            flip=None,
            transpose=None,
        )
    )
    print(
        filter_utils.video_basic_filter(
            fill_color="red",
            remove_alpha=True,
            # crop=(100, 100, 5, 10),
            # flip="horizontal",
            # transpose="clock",
        )
    )
    exit()

    fg = FilterGraph(
        "scale=850:240 [inScale]; color=c=black@1.0:s=850x480:r=29.97:d=30.0 [bg]; movie=bf3Sample2.mp4, scale=850:240 [vid2]; [bg][vid2] overlay=0:0 [basis1]; [basis1][inScale] overlay=0:240"
    )
    pprint(fg.filter_specs)
    for f in fg:
        print(f)

    print(fg)

    del fg[2, 1]

    # fg[0] = FilterGraph("negate")
    # fg[0::2] = FilterGraph("test")
    # fg[:3] = FilterGraph("negate")

    print(fg)
    exit()

    # one shot from a full filtergraph expression
    fg = FilterGraph(
        "[1:v]negate[a];  [2:v]hflip[b];  [3:v]edgedetect[c];  [0:v][a]hstack=inputs=2[top]; [b][c]hstack=inputs=2[bottom]; [top][bottom]vstack=inputs=2[out]"
    )
    print(fg)

    # one filter at a time
    fg = FilterGraph("negate")
    fg.append_filter("hflip", 1)
    fg.append_filter("edgedetect", 2)
    fg.append_filter(("hstack", {"inputs": 2}), 3)
    fg.append_filter(("hstack", {"inputs": 2}), 4)
    fg.append_filter(("vstack", {"inputs": 2}), 5)
    fg.set_link((0, 0, 0), "1:v")
    fg.set_link((1, 0, 0), "2:v")
    fg.set_link((2, 0, 0), "3:v")
    fg.set_link((3, 0, 0), "0:v")
    fg.set_link((3, 0, 1), (0, 0, 0))
    fg.set_link((-2, 0, 0), (1, 0, 0))
    fg.set_link((-2, 0, 1), (2, 0, 0))
    fg.set_link((-1, 0, 0), (-2, 0, 0))
    fg.set_link((-1, 0, 1), (-3, 0, 0))
    fg.set_link("out", (-1, 0, 0))
    print(fg)

    # one filter at a time with links
    fg1 = FilterGraph([["negate"]], input_labels={"1:v": (0, 0, 0)})
    fg2 = FilterGraph([["hflip"]], input_labels={"2:v": (0, 0, 0)})
    fg3 = FilterGraph([["edgedetect"]], input_labels={"3:v": (0, 0, 0)})
    fg4 = FilterGraph([[("hstack", {"inputs": 2})]], input_labels={"0:v": (0, 0, 0)})
    fg5 = FilterGraph([[("hstack", {"inputs": 2})]])
    fg6 = FilterGraph([[("vstack", {"inputs": 2})]], output_labels={"out": (0, 0, 0)})

    fgA = fg1.append(fg4, inplace=False, links_to={(0, 0, 1): (0, 0, 0)})
    fgB = fg2.append(fg5, inplace=False, links_to={(0, 0, 0): (0, 0, 0)})
    fgB.append(fg3, links_from={(0, 0, 0): (1, 0, 1)})
    fg = fg6.append(fgA, inplace=False, links_from={(1, 0, 0): (0, 0, 0)})
    fg.append(fgB, links_from={(1, 0, 0): (0, 0, 1)})
    print(fg)

    # fg = fg4.append(fg1,inplace=False,links_from={(0,0,0):(0,0,1)})
    # fg.append(fg5,inplace=True,links_to={(1,0,0):(0,0,1)})

    # fg.set_link((3, 0, 1), (0, 0, 0))
    # fg.set_link((-2, 0, 0), (1, 0, 0))
    # fg.set_link((-2, 0, 1), (2, 0, 0))
    # fg.set_link((-1, 0, 0), (-2, 0, 0))
    # fg.set_link((-1, 0, 1), (-3, 0, 0))
    # fg.set_link("out", (-1, 0, 0))
    # print(fg)
