import logging

logging.basicConfig(level=logging.DEBUG)
import pytest

from ffmpegio import ffmpeg_info
import ffmpegio.probe as probe

# print(
#     probe.inquire(
#         "tests/assets/testvideo-5m.mpg",
#         show_format=True,
#         show_streams=("index", "codec_name"),
#         show_programs=False,
#     )
# )


def test_probe():
    probe.ffprobe("-help")


def test_url_types():
    url = "tests/assets/testmulti-1m.mp4"
    out = probe.query(url)
    del out["filename"]
    with open(url, "rb") as f:
        # identical outcome if use file object, except for 'filename' field
        out1 = probe.query(f)
        f.seek(0)
        del out1["filename"]
        if 'bit_rate' not in out1:
            del out['bit_rate']
        if 'size' not in out1:
            del out['size']
        assert out1 == out

        # piping in byte content of the file yields a few other differences
        probe.query(f.read())
        

def test_all():
    url = "tests/assets/testmulti-1m.mp4"
    print(probe.full_details(url, show_streams="v"))
    print(probe.format_basic(url))
    print(probe.video_streams_basic(url))
    print(probe.audio_streams_basic(url))


def test_query():
    url = "tests/assets/testmulti-1m.mp4"
    assert isinstance(probe.query(url, fields=("duration",)), dict)
    assert isinstance(
        probe.query(
            url, "v", fields=("duration", "r_frame_rate", "avg_frame_rate", "pix_fmt")
        ),
        list,
    )
    assert isinstance(
        probe.query(url, "a:0", fields=("duration", "sample_rate", "sample_fmt")), list
    )

    assert all(
        st["max_bit_rate"] is None
        for st in probe.query(
            url, "a", fields=("duration", "max_bit_rate"), keep_optional_fields=True
        )
    )

    assert (
        probe.query(
            url, "v:0", fields=("duration", "max_bit_rate"), keep_optional_fields=True
        )[0]["max_bit_rate"]
        is None
    )

    # full detail
    print(probe.query(url))
    print(probe.query(url))


def test_frames():
    url = "tests/assets/testmulti-1m.mp4"
    info = probe.frames(
        url,
        streams="a:0",
        intervals=10,
        # intervals='%+#20,30%+#15'
        # intervals=[{"end_offset": 20}, {"start": 30, "end_offset": 12}],
    )
    print(len(info))
    print(info[-1])
    pts_time = probe.frames(
        url,
        "key_frame",
        "a:0",
        intervals=10,
        # intervals='%+#20,30%+#15'
        # intervals=[{"end_offset": 20}, {"start": 30, "end_offset": 12}],
    )
    print(pts_time)
    pts_time1 = probe.frames(
        url,
        "key_frame",
        "a:0",
        intervals=10,
        accurate_time=True,
        # intervals='%+#20,30%+#15'
        # intervals=[{"end_offset": 20}, {"start": 30, "end_offset": 12}],
    )
    print(pts_time1)
    print([t - t1 for t, t1 in zip(pts_time, pts_time1)])

    info = probe.frames(
        url,
        streams="a:0",
        intervals=(23.4, 1),
        accurate_time=True,
        # intervals='%+#20,30%+#15'
        # intervals=[{"end_offset": 20}, {"start": 30, "end_offset": 12}],
    )
    print(info)


if __name__ == "__main__":
    test_all()
    pass
