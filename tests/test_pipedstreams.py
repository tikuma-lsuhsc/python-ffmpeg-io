import logging

logging.basicConfig(level=logging.DEBUG)

from os import path
import pytest

import ffmpegio as ff

video_url = "tests/assets/testvideo-1m.mp4"
audio_url = "tests/assets/testaudio-1m.mp3"
outext = ".mp4"

@pytest.mark.skip(reason="to be implemented")
def test_transcoder():
    from ffmpegio.streams.PipedStreams import Transcoder

    vsz = path.getsize(video_url) // 100
    asz = path.getsize(audio_url) // 100
    logging.info(f"{vsz=}")
    logging.info(f"{asz=}")

    with (
        open(video_url, "rb") as vf,
        open(audio_url, "rb") as af,
        Transcoder(nb_inputs=2, show_log=True) as merger,
    ):
        while True:
            vdata = vf.read(vsz)
            if vdata:
                merger.write(0, vdata)

            adata = af.read(asz)
            if adata:
                merger.write(1, adata)

            F = merger.read_nowait()
            logging.info(f"read {len(F)} bytes")


if __name__ == "__main__":
    test_merger()
