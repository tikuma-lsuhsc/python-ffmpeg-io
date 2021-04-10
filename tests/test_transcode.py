# import ffmpegio.ffmpeg as ffmpeg
from src import transcode, probe
import tempfile, re
from os import path

# print(
#     probe.inquire(
#         "tests/assets/testvideo-5m.mpg",
#         show_format=True,
#         show_streams=("index", "codec_name"),
#         show_programs=False,
#     )
# )

# url = "tests/assets/testaudio-one.wav"
# url = "tests/assets/testaudio-two.wav"
url = "tests/assets/testaudio-three.wav"
# url = "tests/assets/testvideo-5m.mpg"
# url = "tests/assets/testvideo-43.avi"
# url = "tests/assets/testvideo-169.avi"
url = r"C:\Users\tikum\Music\(アルバム) [Jazz Fusion] T-SQUARE - T-Square Live featuring F-1 Grand Prix Theme [EAC] (flac+cue).mka"
outext = ".flac"

with tempfile.TemporaryDirectory() as tmpdirname:
    print(probe.audio_streams_basic(url))
    out_url = path.join(tmpdirname, re.sub(r"\..*?$", outext, path.basename(url)))
    print(out_url)
    transcode.transcode_sync(url, out_url)
    print(probe.audio_streams_basic(out_url))

    with open(path.join(tmpdirname, "progress.txt")) as f:
        print(f.read())
