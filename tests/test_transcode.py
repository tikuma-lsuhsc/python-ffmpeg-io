from ffmpegio import transcode, probe
import tempfile, re
from os import path

def test_transcode():
    # url = "tests/assets/testaudio-one.wav"
    # url = "tests/assets/testaudio-two.wav"
    url = "tests/assets/testaudio-three.wav"
    # url = "tests/assets/testvideo-5m.mpg"
    outext = ".flac"

    # url = "tests/assets/testvideo-43.avi"
    # url = "tests/assets/testvideo-169.avi"
    # outext = ".mp4"

    with tempfile.TemporaryDirectory() as tmpdirname:
        # print(probe.audio_streams_basic(url))
        out_url = path.join(tmpdirname, re.sub(r"\..*?$", outext, path.basename(url)))
        # print(out_url)
        transcode(url, out_url, force=True)
        # print(probe.audio_streams_basic(out_url))
        # transcode(url, out_url)
        transcode(url, out_url, force=False)
        transcode(url, out_url, force=True)
        
        # with open(path.join(tmpdirname, "progress.txt")) as f:
        #     print(f.read())
