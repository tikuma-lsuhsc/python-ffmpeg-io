from ffmpegio import stream_spec as utils
import pytest


@pytest.mark.parametrize(
    ("arg", "ret"),
    [
        (1, {"index": 1}),
        ("1", {"index": 1}),
        ("v", {"stream_type": "v"}),
        ("p:1", {"program_id": 1}),
        ("p:1:V", {"program_id": 1, "stream_type": "V"}),
        (
            "p:1:a:#6",
            {
                "program_id": 1,
                "stream_type": "a",
                "stream_id": 6,
            },
        ),
        ("d:i:6", {"stream_type": "d", "stream_id": 6}),
        ("t:m:key", {"stream_type": "t", "tag": "key"}),
        ("m:key:value", {"tag": ("key", "value")}),
        ("u", {"usable": True}),
    ],
)
def test_parse_stream_spec(arg, ret):
    assert utils.parse_stream_spec(arg) == ret


def test_stream_spec():
    assert utils.stream_spec() == ""
    assert utils.stream_spec(0) == "0"
    assert utils.stream_spec(stream_type="a") == "a"
    assert utils.stream_spec(1, stream_type="v") == "v:1"
    assert utils.stream_spec(program_id=1) == "p:1"
    assert utils.stream_spec(1, stream_type="v", program_id=1) == "v:p:1:1"
    assert utils.stream_spec(stream_id=342) == "#342"
    assert utils.stream_spec(tag="creation_time") == "m:creation_time"
    assert (
        utils.stream_spec(tag=("creation_time", "2018-05-26T19:36:24.000000Z"))
        == "m:creation_time:2018-05-26T19:36:24.000000Z"
    )
    assert utils.stream_spec(usable=True) == "u"

    # test cases:


@pytest.mark.parametrize(
    ("map", "input_file_id", "ret"),
    [
        ("4", None, {"input_file_id": 4}),
        ("0:1", None, {"input_file_id": 0, "stream_specifier": "1"}),
        ("0:v:0", None, {"input_file_id": 0, "stream_specifier": "v:0"}),
        (
            "-1:v:2:view:back?",
            None,
            {
                "negative": True,
                "input_file_id": 1,
                "stream_specifier": "v:2",
                "view_specifier": "view:back",
                "optional": True,
            },
        ),
        (
            "0:vidx:0",
            None,
            {
                "input_file_id": 0,
                "view_specifier": "vidx:0",
            },
        ),
        ("1:vpos:left", None, {"input_file_id": 1, "view_specifier": "vpos:left"}),
    ],
)
def test_parse_map_option(map, input_file_id, ret):
    assert ret==utils.parse_map_option(map, input_file_id=input_file_id)
