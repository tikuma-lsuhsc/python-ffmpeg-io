import math
from ffmpegio import utils
import pytest


def test_string_escaping():
    raw = "Crime d'Amour"
    esc = utils.escape(raw)
    assert esc == r"'Crime d'\''Amour'"
    assert utils.unescape(esc) == raw

    raw = "  this string starts and ends with whitespaces  "
    esc = utils.escape(raw)
    assert esc == "'  this string starts and ends with whitespaces  '"
    assert utils.unescape(esc) == raw

    esc = r"' The string '\'string\'' is a string '"
    raw = r" The string 'string' is a string "
    assert raw == utils.unescape(utils.escape(raw))
    assert raw == utils.unescape(esc)

    esc = r"'c:\foo' can be written as c:\\foo"
    raw = r"c:\foo can be written as c:\foo"
    assert raw == utils.unescape(esc)
    assert raw == utils.unescape(utils.escape(raw))

    raw = "d'Amour"
    esc = utils.escape(raw)
    assert esc == r"d\'Amour"
    assert utils.unescape(esc) == raw

    raw = r"c:\foo"
    esc = utils.escape(raw)
    assert esc == r"c:\\foo"
    assert utils.unescape(esc) == raw


def test_get_pixel_config():
    with pytest.raises(Exception):
        utils.get_pixel_config("yuv")  # unknown format
    cfg = utils.get_pixel_config("rgb24")  # unknown format
    assert cfg[0] == "rgb24" and cfg[1] == 3 and cfg[2] == "|u1"


def test_alpha_change():

    cases = (("rgb24", "rgba", 1), ("rgb24", "rgb24", 0), ("ya8", "gray", -1))

    for input_pix_fmt, output_pix_fmt, dir in cases:
        dout = utils.alpha_change(input_pix_fmt, output_pix_fmt)
        assert dir == dout
        assert utils.alpha_change(input_pix_fmt, output_pix_fmt, dir) is True
        if dir:
            assert utils.alpha_change(input_pix_fmt, output_pix_fmt, -dir) is False
            assert utils.alpha_change(input_pix_fmt, output_pix_fmt, 0) is False
        else:
            assert utils.alpha_change(input_pix_fmt, output_pix_fmt, 1) is False
            assert utils.alpha_change(input_pix_fmt, output_pix_fmt, -1) is False


def test_get_rotated_shape():
    w = 1000
    h = 400
    print(utils.get_rotated_shape(w, h, 30))
    print(utils.get_rotated_shape(w, h, 45))
    print(utils.get_rotated_shape(w, h, 60))
    assert utils.get_rotated_shape(w, h, 90) == (h, w, math.pi / 2.0)


def test_get_audio_codec():
    cfg = utils.get_audio_codec("s16")
    assert cfg[0] == "pcm_s16le" and cfg[1] == "s16le"


def test_get_audio_format():
    cfg = utils.get_audio_format("s16", 2)
    assert cfg[0] == "<i2" and cfg[1] == (2,)


@pytest.mark.parametrize(
    ("fg", "ret"),
    [
        ("color=c=pink", [(0, "video")]),
        (
            "testsrc [out0]; testsrc,hflip [out1]; testsrc,negate [out2]",
            [(0, "video"), (1, "video"), (2, "video")],
        ),
        ("amovie=test.wav", [(0, "audio")]),
        ("movie=test.avi[out0];amovie=test.wav[out1]", [(0, "video"), (1, "audio")]),
        ("movie=test.avi:s='v:0+a:0'[out0][out1]", [(0, "video"), (1, "audio")]),
        ("movie=test.ts[out0+subcc]", [(0, "video")]),
    ],
)
def test_analyze_input_filtergraph_ids(fg, ret):

    out = utils.analyze_input_filtergraph_ids(fg)
    assert out == ret


if __name__ == "__main__":
    import re
