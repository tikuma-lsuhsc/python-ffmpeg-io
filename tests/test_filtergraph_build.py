from os import path
from tempfile import TemporaryDirectory
from ffmpegio import ffmpegprocess, filtergraph as fgb, FFmpegioError
from ffmpegio.filtergraph import Chain
from pprint import pprint
import pytest


@pytest.mark.parametrize(
    "left,right, from_left, to_right, chain_siso, ret",
    [
        # fmt: off
        ("scale", "fps",(0,0,0),(0,0,0),True,'scale,fps'),
        ("scale", "fps",(0,0,0),(0,0,0),False,'[UNC0]scale[L0];[L0]fps[UNC1]'),
        ("split", "fps",(0,0,1),(0,0,0),True,'[UNC0]split[UNC1][L0];[L0]fps[UNC2]'),
        ("split", "vstack",[(0,0,0),(0,0,1)],[(0,0,1),(0,0,0)],True,'[UNC0]split[L0][L1];[L1][L0]vstack[UNC1]'),
        ("scale", "fps,eq",(0,0,0),(0,0,0),True,'scale,fps,eq'),
        ("scale,fps", "eq",(0,1,0),(0,0,0),True,'scale,fps,eq'),
        ("scale", "[0:v]vstack[out]",(0,0,0),(0,0,1),True,'[UNC0]scale[L0];[0:v][L0]vstack[out]'),
        ("scale", "[in1][0:v]vstack[out]",(0,0,0),(0,0,0),True,'[UNC0]scale[L0];[L0][0:v]vstack[out]'),
        # fmt: on
    ],
)
def test_connect(left, right, from_left, to_right, chain_siso, ret):

    fg = fgb.connect(left, right, from_left, to_right, chain_siso=chain_siso)

    assert fg.compose() == ret


@pytest.mark.parametrize(
    "left,right,how,n_links,strict,unlabeled_only,ret",
    [
        # fmt: off
        ("scale","fps",'all',0,False,False,'scale,fps'),
        ("scale","fps,eq",'all',0,False,False,'scale,fps,eq'),
        ("scale,fps","eq",'all',0,False,False,'scale,fps,eq'),
        ("split","vstack",'all',0,False,False,'[UNC0]split[L0][L1];[L0][L1]vstack[UNC1]'),
        ("split","vstack",'all',1,False,False,'[UNC0]split[L0][UNC2];[L0][UNC1]vstack[UNC3]'),
        ("[vin]scale;[ain]asplit","vstack[vout];atrim[aout]",'all',0,False,False,'[vin]scale[L0];[ain]asplit[L1][L2];[L0][L1]vstack[vout];[L2]atrim[aout]'),
        ("[vin]scale;[ain]asplit","vstack[vout];atrim[aout]",'per_chain',0,False,False,'[vin]scale[L0];[ain]asplit[L1][UNC1];[L0][UNC0]vstack[vout];[L1]atrim[aout]'),
        ("[vin]scale;[ain]asplit","vstack[vout]",'all',0,False,False,'[vin]scale[L0];[ain]asplit[L1][UNC0];[L0][L1]vstack[vout]'),
        ("[vin]scale;[ain]asplit","vstack[vout]",'all',0,True,False,None),
        ("split[out]","[in]vstack",'all',0,False,True,'[UNC0]split[out][L0];[in][L0]vstack[UNC1]'),
        # fmt: on
    ],
)
def test_join(left, right, how, n_links, strict, unlabeled_only, ret):

    if ret is None:
        with pytest.raises(FFmpegioError):
            fgb.join(left, right, how, n_links, strict, unlabeled_only)
    else:
        fg = fgb.join(left, right, how, n_links, strict, unlabeled_only)
        assert fg.compose() == ret


@pytest.mark.parametrize(
    "left,right,left_on,right_on,ret",
    [
        # fmt: off
        ("scale","fps",(0,0,0),(0,0,0),'scale,fps'),
        ("scale","fps",None,None,'scale,fps'),
        ("scale","[out]",None,None,'[UNC0]scale[out]'),
        ("[in]","scale",None,None,'[in]scale[UNC0]'),
        ("[in]split",["fps","out"],None,None,'[in]split[L0][out];[L0]fps[UNC0]'),
        (["in","fps"],"vstack",None,None,'[UNC0]fps[L0];[in][L0]vstack[UNC1]'),
        # fmt: on
    ],
)
def test_attach(left, right, left_on, right_on, ret):

    if ret is None:
        with pytest.raises(ValueError):
            fgb.attach(left, right, left_on, right_on)
    else:
        fg = fgb.attach(left, right, left_on, right_on)
        assert fg.compose() == ret
