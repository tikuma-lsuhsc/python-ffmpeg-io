import pytest
from pprint import pprint

import ffmpegio.filtergraph.presets as presets


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(crop=None, flip=None, transpose=None),
        dict(scale=1.2, crop=100, flip="both", transpose=90, square_pixels="upscale"),
    ],
)
def test_video_basic_filter(kwargs):
    print(presets.filter_video_basic(**kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"fill_color": "red"},
        {"fill_color": "red", "input_label": "in", "output_label": "[out]"},
    ],
)
def test_remove_video_alpha(kwargs):
    print(presets.remove_video_alpha(**kwargs))
