import logging
from ffmpegio import probe, configure, ffmpegprocess, utils

# logging.basicConfig(level=logging.DEBUG)


def test_run_help():
    ffmpegprocess.run({"global_options": {"help": None}})


def test_run_from_stream():
    url = "tests/assets/testaudio-1m.mp3"
    sample_fmt = "s16"
    out_codec, container = utils.get_audio_codec(sample_fmt)

    with open(url, "rb") as f:
        args = {
            "inputs": [("-", {"f": "mp3"})],
            "outputs": [
                ("-", {"f": container, "c:a": out_codec, "sample_fmt": sample_fmt})
            ],
        }
        out = ffmpegprocess.run(args, capture_log=True, stdin=f)

    print(f"FFmpeg output: {len(out.stdout)} bytes")
    print(out.stderr)


def test_run_bidir():
    url = "tests/assets/testaudio-1m.mp3"
    sample_fmt = "s16"
    out_codec, container = utils.get_audio_codec(sample_fmt)

    with open(url, "rb") as f:
        bytes = f.read()  # byte array

    args = {
        "inputs": [("-", {"f": "mp3"})],
        "outputs": [
            ("-", {"f": container, "c:a": out_codec, "sample_fmt": sample_fmt})
        ],
    }

    out = ffmpegprocess.run(args, input=bytes, capture_log=True)

    print(f"FFmpeg output: {len(out.stdout)} bytes")
    print(out.stderr)


def test_run_progress():
    url = "tests/assets/testaudio-1m.mp3"
    sample_fmt = "s16"
    out_codec, container = utils.get_audio_codec(sample_fmt)

    def progress(*args):
        print(args)
        return False

    with open(url, "rb") as f:
        args = {
            "inputs": [("-", {"f": "mp3"})],
            "outputs": [
                ("-", {"f": container, "c:a": out_codec, "sample_fmt": sample_fmt})
            ],
        }

        ffmpegprocess.run(args, capture_log=True, progress=progress, stdin=f)


def test_popen():
    url = "tests/assets/testaudio-1m.mp3"
    sample_fmt = "s16"
    out_codec, container = utils.get_audio_codec(sample_fmt)

    with open(url, "rb") as f:
        args = {
            "inputs": [("-", {"f": "mp3"})],
            "outputs": [
                ("-", {"f": container, "c:a": out_codec, "sample_fmt": sample_fmt})
            ],
        }

        proc = ffmpegprocess.Popen(args, capture_log=True, stdin=f)
        x = proc.stdout.read()
        if proc.wait(50) is None:
            print("ffmpeg not stopping")
            proc.kill()

    print(f"FFmpeg output: {len(x)} samples")


def test_popen_progress():
    url = "tests/assets/testvideo-1m.mp4"

    def progress(*args):
        global i
        i += 1
        print(i, args)
        return i > 0

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url)
    configure.add_url(ffmpeg_args, "output", "-", {"map": "0:v:0"})

    dtype, shape, r = configure.finalize_video_read_opts(
        ffmpeg_args, input_info=[{"src_type": "url"}]
    )

    samplesize = utils.get_samplesize(shape, dtype)

    with ffmpegprocess.Popen(
        ffmpeg_args,
        capture_log=True,
        progress=progress,
    ) as proc:
        for k in range(15):
            print(proc.stderr.readline())
        for j in range(10):
            out = proc.stdout.read(samplesize)
            assert len(out) == samplesize


if __name__ == "__main__":

    logging.basicConfig(level=logging.DEBUG)
