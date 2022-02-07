from math import prod
from ffmpegio import plugins


def test_rawdata_bytes():
    hook = plugins.get_hook()

    dtype = "|u8"
    shape = (2, 2, 3)
    b = b"\0" * prod(shape)
    data = hook.bytes_to_video(b=b, dtype=dtype, shape=shape)
    assert data["buffer"] == b
    assert data["dtype"] == dtype
    assert data["shape"][1:] == shape
    assert hook.video_info(obj=data) == (shape, dtype)
    assert hook.video_bytes(obj=data) == b

    dtype = "<f4"
    shape = (2,)
    b = b"\0" * (1024 * prod(shape))
    data = hook.bytes_to_audio(b=b, dtype=dtype, shape=shape)
    assert data["buffer"] == b
    assert data["dtype"] == dtype
    assert data["shape"][1:] == shape
    assert hook.audio_info(obj=data) == (shape, dtype)
    assert hook.audio_bytes(obj=data) == b


if __name__ == "__main__":
    print(plugins.pm.get_plugins())
