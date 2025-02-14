from ffmpegio import audio, probe, FilterGraph
import tempfile, re, logging
from os import path
import pytest

logging.basicConfig(level=logging.DEBUG)


def test_create():
    fs = 8000
    fs, x = audio.create(
        "aevalsrc",
        "sin(420*2*PI*t)|cos(430*2*PI*t)",
        d=1,
        nb_samples=fs,
        sample_rate=fs,
        show_log=True,
    )
    assert x["shape"] == (fs, 2)

    # x = audio.create(
    #     "flite",
    #     text="The rainbow is a division of white light into many beautiful colors.",
    #     nb_samples=1024 * 8,
    # )
    # assert x['shape'] == (1024 * 8, 1)

    fs, x = audio.create("anoisesrc", d=60, c="pink", r=44100, a=0.5)
    print(x["shape"], 60 * 44100)
    assert x["shape"] == (60 * 44100, 1)

    fs, x = audio.create("sine", f=220, b=4, d=5)
    print(x["shape"], 5 * 44100)
    assert x["shape"] == (5 * 44100, 1)


@pytest.mark.skip(reason="takes too long to test")
def test_read_url():
    rid = 1
    url = f"http://www.stimmdatenbank.coli.uni-saarland.de/csl2wav.php4?file={rid}-i_n"
    fs, x = audio.read(url)
    print(fs, x["shape"])


def test_read():

    url = "tests/assets/testaudio-1m.mp3"

    T = 0.51111
    T = 0.49805
    fs, x = audio.read(url, t=T, show_log=True)
    print(fs * T, round(fs * T), x["shape"])
    assert round(fs * T) == x["shape"][0]

    T = 1.5
    t0 = 0.5
    fs, x1 = audio.read(url, ss=t0, to=t0 + T)
    print(int(fs * T), x1["shape"])
    assert round(fs * T) == x1["shape"][0]

    # n0 = int(t0 * fs)
    # N = int(T * fs)

    # fs, x2 = audio.read(url, ss=n0, t=N, units="samples")
    # assert x1['shape'] == x2.shape
    # assert np.array_equal(x1, x2)


def test_read_write():
    url = "tests/assets/testaudio-1m.mp3"
    outext = ".flac"

    fs, x = audio.read(url)

    with tempfile.TemporaryDirectory() as tmpdirname:

        print(probe.audio_streams_basic(url))
        out_url = path.join(tmpdirname, re.sub(r"\..*?$", outext, path.basename(url)))
        print(out_url)
        print(x["shape"], x["dtype"])
        audio.write(out_url, fs, x)
        fs, y = audio.read(out_url, sample_fmt="flt")
        print(probe.audio_streams_basic(out_url))

        out_url = path.join(tmpdirname, re.sub(r"\..*?$", ".wav", path.basename(url)))
        audio.write(out_url, fs, x, acodec="pcm_s16le", loglevel="fatal", show_log=True)
        print(probe.audio_streams_basic(out_url))


def test_filter():
    input_rate = 44100
    input = {
        "buffer": b"\xef" * input_rate * 2,
        "dtype": "<i2",
        "shape": (input_rate, 1),
    }
    expr = FilterGraph(
        [
            [
                ("channelmap", {"channel_layout": "stereo", "map": "FC|FC"}),
                ("bandpass", {"channels": "FL"}),
                ("aresample", 22050),
            ]
        ],
    )

    output_rate, output = audio.filter(expr, input_rate, input, show_log=True, loglevel ='verbose')
    assert output_rate == 22050
    assert output["shape"] == (22050, 2)
    assert output["dtype"] == input["dtype"]

    # force output format and rate
    output_rate, output = audio.filter(
        expr, input_rate, input, ar=44100, sample_fmt="s16"
    )
    assert output_rate == 44100
    assert output["shape"] == (44100, 2)
    assert output["dtype"] == "<i2"

    # complex filtergraph
    expr = FilterGraph(
        [[("anoisesrc", 44100, {"color": "pink"}), ("amerge",)]],
        {"in": [(0, 1, 0), None], "out": [None, (0, 1, 0)]},
    )
    output_rate, output = audio.filter(expr, input_rate, input)
    assert output_rate == 44100
    assert output["shape"] == (44100, 2)
    assert output["dtype"] == '<f8'

    output_rate, output = audio.filter("[in]bandpass[out]", input_rate, input)


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.DEBUG)

    import ffmpegio

    logging.basicConfig(level=logging.DEBUG)

    url = "tests/assets/sample.mp4"
    # url = "C:/Users/tikuma/Downloads/BigBuckBunny.mp4"
    # url = 'tests/assets/imgs/testimage-%d.png'
    out = ffmpegio.audio.detect(url, d=0.5, noise=0.01, mono=False)
    print(out)

    # silencedetect

    # volumedetect
    # [Parsed_volumedetect_0 @ 0000022d711cabc0] n_samples: 5292000
    # [Parsed_volumedetect_0 @ 0000022d711cabc0] mean_volume: -23.0 dB
    # [Parsed_volumedetect_0 @ 0000022d711cabc0] max_volume: -19.9 dB
    # [Parsed_volumedetect_0 @ 0000022d711cabc0] histogram_19db: 84401

    # astats
    # [Parsed_astats_0 @ 000002212ea9bb40] Channel: 1
    # [Parsed_astats_0 @ 000002212ea9bb40] DC offset: 0.000002
    # [Parsed_astats_0 @ 000002212ea9bb40] Min level: -0.101331
    # [Parsed_astats_0 @ 000002212ea9bb40] Max level: 0.101160
    # [Parsed_astats_0 @ 000002212ea9bb40] Min difference: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Max difference: 0.005601
    # [Parsed_astats_0 @ 000002212ea9bb40] Mean difference: 0.003248
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS difference: 0.003608
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak level dB: -19.885174
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS level dB: -23.023746
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS peak dB: -22.977582
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS trough dB: -23.080884
    # [Parsed_astats_0 @ 000002212ea9bb40] Crest factor: 1.435253
    # [Parsed_astats_0 @ 000002212ea9bb40] Flat factor: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak count: 11
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor dB: -19.958742
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor count: 37870
    # [Parsed_astats_0 @ 000002212ea9bb40] Bit depth: 32/32
    # [Parsed_astats_0 @ 000002212ea9bb40] Dynamic range: 130.766612
    # [Parsed_astats_0 @ 000002212ea9bb40] Zero crossings: 43076
    # [Parsed_astats_0 @ 000002212ea9bb40] Zero crossings rate: 0.016280
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of NaNs: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of Infs: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of denormals: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Channel: 2
    # [Parsed_astats_0 @ 000002212ea9bb40] DC offset: 0.000001
    # [Parsed_astats_0 @ 000002212ea9bb40] Min level: -0.101070
    # [Parsed_astats_0 @ 000002212ea9bb40] Max level: 0.101215
    # [Parsed_astats_0 @ 000002212ea9bb40] Min difference: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Max difference: 0.005738
    # [Parsed_astats_0 @ 000002212ea9bb40] Mean difference: 0.003271
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS difference: 0.003633
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak level dB: -19.895119
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS level dB: -23.024514
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS peak dB: -22.976440
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS trough dB: -23.080960
    # [Parsed_astats_0 @ 000002212ea9bb40] Crest factor: 1.433738
    # [Parsed_astats_0 @ 000002212ea9bb40] Flat factor: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak count: 20
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor dB: -19.948194
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor count: 205458
    # [Parsed_astats_0 @ 000002212ea9bb40] Bit depth: 32/32
    # [Parsed_astats_0 @ 000002212ea9bb40] Dynamic range: 122.661078
    # [Parsed_astats_0 @ 000002212ea9bb40] Zero crossings: 43375
    # [Parsed_astats_0 @ 000002212ea9bb40] Zero crossings rate: 0.016393
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of NaNs: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of Infs: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of denormals: 0
    # [Parsed_astats_0 @ 000002212ea9bb40] Overall
    # [Parsed_astats_0 @ 000002212ea9bb40] DC offset: 0.000002
    # [Parsed_astats_0 @ 000002212ea9bb40] Min level: -0.101331
    # [Parsed_astats_0 @ 000002212ea9bb40] Max level: 0.101215
    # [Parsed_astats_0 @ 000002212ea9bb40] Min difference: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Max difference: 0.005738
    # [Parsed_astats_0 @ 000002212ea9bb40] Mean difference: 0.003259
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS difference: 0.003621
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak level dB: -19.885174
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS level dB: -23.024130
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS peak dB: -22.976440
    # [Parsed_astats_0 @ 000002212ea9bb40] RMS trough dB: -23.080960
    # [Parsed_astats_0 @ 000002212ea9bb40] Flat factor: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Peak count: 15.500000
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor dB: -19.948194
    # [Parsed_astats_0 @ 000002212ea9bb40] Noise floor count: 121664.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Bit depth: 32/32
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of samples: 2646000
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of NaNs: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of Infs: 0.000000
    # [Parsed_astats_0 @ 000002212ea9bb40] Number of denormals: 0.000000
