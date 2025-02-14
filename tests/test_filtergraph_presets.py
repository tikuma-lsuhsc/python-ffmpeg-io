import pytest
from pprint import pprint

import ffmpegio.filtergraph.presets as presets

def test_video_basic_filter():
    print(
        presets._build_video_basic_filter(
            fill_color=None,
            remove_alpha=None,
            crop=None,
            flip=None,
            transpose=None,
        )
    )
    print(
        presets._build_video_basic_filter(
            fill_color="red",
            remove_alpha=True,
            # crop=(100, 100, 5, 10),
            # flip="horizontal",
            # transpose="clock",
        )
    )