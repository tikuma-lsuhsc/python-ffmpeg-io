from ffmpegio import threading
from ffmpegio import ffmpegprocess
from ffmpegio.ffmpegprocess import Popen
from tempfile import TemporaryDirectory
from os import path
from pprint import pprint


def test_log_popen():
    # with exec({"inputs": [(url, None)], "outputs": [("-", None)], "global_options": None},sp_run=sp.Popen,capture_log=True) as f:
    url = "tests/assets/testmulti-1m.mp4"
    with (
        TemporaryDirectory() as tmpdir,
        Popen(
            {
                "inputs": [(url, {"t": 0.1})],
                "outputs": [(path.join(tmpdir, "test.mp4"), None)],
                "global_options": None,
            },
            capture_log=True,
        ) as proc,
        threading.LoggerThread(proc.stderr, True) as logger,
    ):
        logger.index("Output")
        pprint(logger.output_stream(0, 0))


def test_copyfileobj():
    url = "tests/assets/testaudio-1m.mp3"
    with (
        TemporaryDirectory() as tmpdir,
        open(url, "rb") as fsrc,
        open(path.join(tmpdir, "out.mp3"), "w+b") as fdst,
        threading.CopyFileObjThread(fsrc, fdst) as copier,
    ):

        copier.join()

        fsrc.seek(0)
        data = fsrc.read()
        fdst.seek(0)
        data_out = fdst.read()
        
    assert data == data_out


if __name__ == "__main__":

    url = "tests/assets/testmulti-1m.mp4"
    url1 = "tests/assets/testvideo-1m.mp4"
    url2 = "tests/assets/testaudio-1m.mp3"

    from pprint import pprint

    from ffmpegio import ffmpeg, configure
    import io

    args = {
        "inputs": [(url1, None), (url2, None)],
        "outputs": [
            (
                "-",
                {
                    "vframes": 16,
                },
            )
        ],
    }
    use_ya = configure.finalize_media_read_opts(args)
    pprint(args)

    # create a short example with both audio & video
    f = io.BytesIO(ffmpegprocess.run(args).stdout)

    reader = threading.AviReaderThread()
    reader.start(f, use_ya)
    try:
        reader.wait()
        print(f"thread is running {reader.is_alive()}")
        pprint(reader.streams)
        pprint(reader.rates)
    except:
        reader.join()
