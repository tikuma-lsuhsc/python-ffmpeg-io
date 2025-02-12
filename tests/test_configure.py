import pytest

from ffmpegio import configure

vid_url = "tests/assets/testvideo-1m.mp4"
img_url = "tests/assets/ffmpeg-logo.png"
aud_url = "tests/assets/testaudio-1m.mp3"
mul_url = "tests/assets/testmulti-1m.mp4"


def test_array_to_audio_input():
    fs = 44100
    N = 44100
    nchmax = 4
    data = {"buffer": b"0" * N * nchmax * 2, "dtype": "<i2", "shape": (nchmax,)}

    cfg = {"f": "s16le", "c:a": "pcm_s16le", "ac": 4, "ar": 44100, "sample_fmt": "s16"}
    input = configure.array_to_audio_input(fs, data)
    assert input[0] == "-" and input[1] == cfg


def test_array_to_video_input():
    fs = 30
    dtype = "|u1"
    h = 360
    w = 480
    ncomp = 3
    nframes = 10
    data = {
        "buffer": b"0" * nframes * h * w * ncomp,
        "dtype": dtype,
        "shape": (nframes, h, w, ncomp),
    }
    cfg = {
        "f": "rawvideo",
        "c:v": "rawvideo",
        "s": (w, h),
        "r": fs,
        "pix_fmt": "rgb24",
    }

    input = configure.array_to_video_input(fs, data)
    print(input)
    assert input[0] == "-" and input[1] == cfg


def test_add_url():

    url = "test.mp4"
    args = {}
    args_expected = {}
    idx, entry = configure.add_url(args, "input", url, None)
    args_expected["inputs"] = [(url, None)]
    assert idx == 0 and entry == args_expected["inputs"][0] and args == args_expected

    idx, entry = configure.add_url(args, "input", url, {"f": "rawvideo"}, update=True)
    args_expected["inputs"][0] = (url, {"f": "rawvideo"})
    assert idx == 0 and entry == args_expected["inputs"][0] and args == args_expected

    idx, entry = configure.add_url(
        args, "input", url, {"f": "mp4", "codec": "h264"}, update=True
    )
    args_expected["inputs"][0] = (url, {"f": "mp4", "codec": "h264"})
    assert idx == 0 and entry == args_expected["inputs"][0] and args == args_expected

    url2 = "test2.wav"
    idx, entry = configure.add_url(args, "input", url2, {"f": "wav"})
    args_expected["inputs"].append((url2, {"f": "wav"}))
    assert idx == 1 and entry == args_expected["inputs"][1] and args == args_expected


def test_add_urls():

    url = ["test.mp4", "test1.mp4", "test2.mp4", "test3.mp4", "test4.mp4"]
    args = {}

    # urls: str | tuple[str, dict | None] | Sequence[str | tuple[str, dict | None]],
    assert configure.add_urls(args, "input", url[0]) == [(0, (url[0], None))]
    assert configure.add_urls(args, "input", (url[1], None)) == [(1, (url[1], None))]
    assert configure.add_urls(args, "input", (url[2], {})) == [(2, (url[2], {}))]
    assert configure.add_urls(args, "input", [url[3], url[4]]) == [
        (3, (url[3], None)),
        (4, (url[4], None)),
    ]


def test_get_option():

    assert configure.get_option(None, "input", "c") is None
    assert configure.get_option({}, "input", "c") is None
    assert configure.get_option({}, "global_options", "c") is None

    args = {
        "inputs": [("file1", None)],
        "outputs": [("file2", {"c": 0, "c:v": 1, "c:v:0": 2}), ("file3", {"ac": 2})],
        "global_options": {"y": True},
    }
    assert configure.get_option(args, "global", "y") is True
    assert configure.get_option(args, "global", "n") is None
    assert configure.get_option(args, "input", "c") is None
    assert configure.get_option(args, "output", "c") == 0
    assert configure.get_option(args, "output", "c", stream_type="v") == 1
    assert configure.get_option(args, "output", "c", stream_id=0, stream_type="v") == 2
    assert configure.get_option(args, "output", "ac", file_id=1) == 2


def test_video_basic_filter():
    print(
        configure._build_video_basic_filter(
            fill_color=None,
            remove_alpha=None,
            crop=None,
            flip=None,
            transpose=None,
        )
    )
    print(
        configure._build_video_basic_filter(
            fill_color="red",
            remove_alpha=True,
            # crop=(100, 100, 5, 10),
            # flip="horizontal",
            # transpose="clock",
        )
    )


mul_streams = [(0, "video"), (1, "audio"), (2, "video"), (3, "audio")]
mul_vid_streams = [mul_streams[0], mul_streams[2]]


@pytest.mark.parametrize(
    ("info", "url", "opts", "stream_spec", "ret"),
    [
        ({"src_type": "url"}, mul_url, {}, None, mul_streams),
        ({"src_type": "fileobj"}, mul_url, {}, "v", mul_vid_streams),
        ({"src_type": "buffer"}, mul_url, {}, "v", mul_vid_streams),
        ({"src_type": "filtergraph"}, "color=c=pink [out0]", {}, None, [(0, "video")]),
    ],
)
def test_retrieve_input_stream_ids(info, url, opts, stream_spec, ret):

    open_file = info["src_type"] in ("fileobj", "buffer")
    try:
        if open_file:
            info["fileobj"] = open(url, "rb")
            if info["src_type"] == "buffer":
                info["buffer"] = info["fileobj"].read()
        out = configure.retrieve_input_stream_ids(info, url, opts, stream_spec)
    finally:
        if open_file:
            info["fileobj"].close()

    assert out == ret


@pytest.mark.parametrize(
    ("url", "opts", "defopts", "ret"),
    [
        (mul_url, None, {}, ((mul_url, {}), {"src_type": "url"})),
        (mul_url, None, {}, ((None, {}), {"src_type": "fileobj"})),
        (mul_url, None, {}, ((None, {}), {"src_type": "buffer"})),
        (
            "color=c=pink [out0]",
            None,
            {"f": "lavfi"},
            (("color=c=pink [out0]", {"f": "lavfi"}), {"src_type": "filtergraph"}),
        ),
    ],
)
def test_process_url_inputs(url, opts, defopts, ret):

    info = ret[1]
    open_file = info["src_type"] in ("fileobj", "buffer")
    try:
        if open_file:
            fileobj = open(url, "rb")
            if info["src_type"] == "buffer":
                info["buffer"] = url = fileobj.read()
            else:
                url = info["fileobj"] = fileobj
        args = configure.empty()
        out = configure.process_url_inputs(
            args, [url if opts is None else (url, opts)], defopts
        )
        assert (args["inputs"][0], out[0]) == ret

    finally:
        if open_file:
            fileobj.close()


@pytest.mark.parametrize(
    ("inputs", "input_info", "filters_complex", "ret"),
    [
        (
            [(mul_url, None)],
            [{"src_type": "url"}],
            None,
            {f"0:{i}": mtype for i, mtype in mul_streams},
        ),
        (
            [(vid_url, None), (aud_url, {})],
            [{"src_type": "url"}, {"src_type": "url"}],
            None,
            {"0:0": "video", "1:0": "audio"},
        ),
        (
            [(mul_url, None)],
            [{"src_type": "url"}],
            ["split=n=2"],
            {"[out0]": "video", "[out1]": "video"},
        ),
    ],
)
def test_auto_map(inputs, input_info, filters_complex, ret):
    args = configure.empty()
    args["inputs"].extend(inputs)
    if filters_complex is not None:
        args["global_options"] = {"filter_complex": filters_complex}
    out = configure.auto_map(args, input_info)
    assert out == ret


@pytest.mark.parametrize(
    ("filters_complex", "ret"),
    [(["split=n=2"], {"[out0]": "video", "[out1]": "video"})],
)
def test_analyze_fg_outputs(filters_complex, ret):
    args = configure.empty({"filter_complex": filters_complex})
    out = configure.analyze_fg_outputs(args)
    assert out == ret
