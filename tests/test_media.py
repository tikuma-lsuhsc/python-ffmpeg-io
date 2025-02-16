from tempfile import TemporaryDirectory
from os import path
from pprint import pprint

import ffmpegio as ff


def test_media_read():
    url = "tests/assets/testmulti-1m.mp4"
    url1 = "tests/assets/testvideo-1m.mp4"
    url2 = "tests/assets/testaudio-1m.mp3"
    rates, data = ff.media.read(url, t=1, show_log=True)
    rates, data = ff.media.read(url, map=("v:0", "v:1", "a:1", "a:0"), t=1, show_log=True)
    rates, data = ff.media.read(url1, url2, t=1, show_log=True)
    rates, data = ff.media.read(url2, url, map=("1:v:0", (0, "a:0")), t=1, show_log=True)

    print(rates)
    print([(k, x["shape"], x["dtype"]) for k, x in data.items()])


def test_media_write():
    fs, x = ff.audio.read("tests/assets/testaudio-1m.mp3")

    fps, F = ff.video.read("tests/assets/testvideo-1m.mp4", vframes=120)

    outext = ".mp4"

    print(f"video: {len(F['buffer'])} bytes | audio: {len(x['buffer'])} bytes")

    with TemporaryDirectory() as tmpdirname:
        outfile = path.join(tmpdirname, f"out{outext}")
        ff.media.write(
            outfile, "va", (fps, F), (fs, x), show_log=True, shortest=ff.FLAG
        )

        pprint(ff.probe.format_basic(outfile))
        pprint(ff.probe.streams_basic(outfile))


def test_media_write_audio_merge():
    stream1 = ff.audio.read("tests/assets/testaudio-1m.mp3", ar=8000, sample_fmt="s16")
    stream2 = ff.audio.read("tests/assets/testaudio-1m.mp3", ar=16000, sample_fmt="flt")
    stream3 = ff.audio.read("tests/assets/testaudio-1m.mp3", ar=4000, sample_fmt="dbl")

    outext = ".wav"

    with TemporaryDirectory() as tmpdirname:
        outfile = path.join(tmpdirname, f"out{outext}")
        ff.media.write(
            outfile,
            "aaa",
            stream1,
            stream2,
            stream3,
            merge_audio_streams=True,
            show_log=True,
            shortest=ff.FLAG,
        )
        pprint(ff.probe.format_basic(outfile))
        pprint(ff.probe.audio_streams_basic(outfile))
