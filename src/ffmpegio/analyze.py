"""media analysis tools module

"""

from __future__ import annotations
from collections import namedtuple
from abc import ABC
import logging

logger = logging.getLogger("ffmpegio")

from . import configure
from .filtergraph import Graph, Filter, Chain, as_filtergraph
from .filtergraph.utils import compose_filter
from .errors import FFmpegError
from .path import devnull
from . import ffmpegprocess as fp
import re
from json import loads

from typing import Any, Tuple, NamedTuple, List, Optional

try:
    from typing import Literal
except ImportError:
    from typing_extensions import Literal


def loudnorm(
    url,
    i=None,
    lra=None,
    tp=None,
    offset=None,
    linear=None,
    dual_mono=None,
    af=None,
    progress=None,
    overwrite=None,
    return_stats=False,
    **options,
):
    """run analysis (first pass) of EBU R128 loudness normalization

    :param url: input url
    :type url: str
    :param i: integrated loudness target, defaults to None
    :type i: float, optional
    :param lra: loudness range target, defaults to None
    :type lra: float, optional
    :param tp: maximum true peak, defaults to None
    :type tp: float, optional
    :param offset: offset gain, defaults to None
    :type offset: float, optional
    :param linear: True to normalize by linearly scaling the source audio, False to normalize dynamically, defaults to None
    :type linear: bool, optional
    :param dual_mono: True to treat mono input files as "dual-mono", defaults to None
    :type dual_mono: bool, optional
    :param af: preceding filter chain, defaults to None
    :type af: str, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param overwrite: True to overwrite if output url exists, defaults to None
                      (auto-select)
    :type overwrite: bool, optional
    :param return_stats: True to return stats instead of loudnorm options, defaults to False
    :type return_stats: bool, optional
    :return: second pass loudnorm filter spec str or analysis stats
    :rtype: str or dict
    """

    loudnorm_opts = {
        k: v
        for k, v in zip(
            ["i", "lra", "tp", "offset", "linear", "dual_mono"],
            [i, lra, tp, offset, linear, dual_mono],
        )
        if v is not None
    }

    loundness_f = Filter("loudnorm", **loudnorm_opts, print_format="json")

    af = (Graph(af) + loundness_f) if af else loundness_f

    args = configure.empty()
    configure.add_url(args, "input", url, options)
    configure.add_url(
        args,
        "output",
        devnull,
        {
            "af": af,
            "f": "null",
            "vn": None,
            "sn": None,
            "ar": "192k",
            "c:a": "pcm_f32le",
        },
    )

    log = fp.run(
        args,
        progress=progress,
        overwrite=overwrite,
        capture_log=True,
        universal_newlines=True,
    ).stderr

    stats = loads(log[re.search(r"\[Parsed_loudnorm_2 @ .+\] \n", log).end() :])

    if return_stats:
        return stats

    for k, src in (
        ("measured_i", "input_i"),
        ("measured_lra", "input_lra"),
        ("measured_tp", "input_tp"),
        ("measured_thresh", "input_thresh"),
    ):
        loudnorm_opts[k] = float(stats[src])

    return compose_filter("loudnorm", loudnorm_opts)


class MetadataLogger(ABC):
    """Abstract class for :py:func:`analyze.run` frame metadata loggers"""

    media_type: Literal["video", "audio"]  #: (static) target stream media type
    meta_names: Tuple[str]  #: (static) metadata names to be logged
    filter_name: str  #: (static) name of the FFmpeg filter to use
    options: dict[str, Any]  #: FFmpeg filter options (value must be stringifiable)

    @property
    def filter(self) -> Filter:
        """filter specification expression to be used in FilterGraph"""
        return Filter(self.filter_name, **self.options)

    @property
    def ref_in(self) -> Optional[str]:
        """stream specifier for reference input url only if applicable (default: None)"""
        return None

    @property
    def output(self) -> NamedTuple:
        """log output as a namedtuple"""
        ...

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        ...


def run(
    url,
    *loggers,
    references=None,
    time_units=None,
    start_at_zero=False,
    progress=None,
    show_log=None,
    **input_options,
):
    """analyze media streams' frames with FFmpeg filters

    :param url: video file url
    :type url: str
    :param \*loggers: class object with the metadata logging interface
    :type \*loggers: tuple[MetadataLogger]
    :param references: reference input urls or pairs of url and input option
                       dict, defaults to None
    :type references: seq of str or seq of (str, dict), optional
    :param ss: start time to process, defaults to None
    :type ss: int, float, str, optional
    :param t: duration of data to process, defaults to None
    :type t: int, float, str, optional
    :param to: stop processing at this time (ignored if t is also specified), defaults to None
    :type to: int, float, str, optional
    :param time_units: units of detected time stamps (not for ss, t, or to), defaults to None ('seconds')
    :type time_units: 'seconds', 'frames', 'pts', optional
    :param start_at_zero: ignore start time, defaults to False
    :type start_at_zero: bool, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param \**options: FFmpeg (primary) input options.
    :type \**options: dict, optional
    :returns: logger objects passed in
    :rtype: tuple[MetadataLogger]


    """

    if not len(loggers):
        raise ValueError("At least one logger object must be present.")

    if references is None:
        references = ()
    elif isinstance(references, str):
        references = [references]

    try:
        tunits = ("frames", "pts", "seconds").index(time_units) + 1 if time_units else 3
    except:
        raise ValueError(
            f'time_units "{time_units}" is invalid. Must be one of ("frames", "pts", "seconds")'
        )

    fchains = {"video": Chain([]), "audio": Chain([])}
    for l in loggers:
        # filterchain under consturction
        c = fchains[l.media_type]

        # logging filter (may need to convert to filtergraph if uses a reference stream)
        f = (
            l.filter
            if l.ref_in is None
            else Graph([l.filter], {l.ref_in: ((0, 0, 1), None)})
        )
        # if requires reference stream, make sure the fchain is a Graph object, too
        if l.ref_in and type(c) != Graph:
            fchains[l.media_type] = c = as_filtergraph(c)

        # assign the logger to get the output of the previous logger
        fchains[l.media_type] = c >> f

    if len(fchains["video"]):
        fchains["video"] >>= Filter("metadata", "print", file="-")

    if len(fchains["audio"]):
        fchains["audio"] >>= Filter("ametadata", "print", file="-")

    oopts = {"f": "null"}
    gopts = {"copyts": fp.FLAG}
    if start_at_zero:
        gopts["start_at_zero"] = fp.FLAG

    vf, af = fchains.values()
    if isinstance(vf, Graph) or isinstance(af, Graph):
        # at least one logger requires reference input, must use filter_complex
        if len(vf):
            vf = "[0:v:0]" >> vf  # >> "nullsink"
        if len(af):
            af = "[0:a:0]" >> af  # >> "anullsink"
        gopts["filter_complex"] = vf | af
    else:
        # set filter chains
        for k, fg in zip(("vf", "af"), fchains.values()):
            if len(fg):
                oopts[k] = fg

    # create FFmpeg arguments
    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, input_options)
    for ref in references:
        if isinstance(ref, str):
            configure.add_url(ffmpeg_args, "input", ref)
        else:
            configure.add_url(ffmpeg_args, "input", *ref)
    configure.add_url(ffmpeg_args, "output", devnull, oopts)
    ffmpeg_args["global_options"] = gopts

    # run FFmpeg
    out = fp.run(
        ffmpeg_args,
        progress=progress,
        capture_log=True,
        universal_newlines=True,
        stdout=fp.PIPE,
        stderr=fp.PIPE if show_log else None,
    )

    # if FFmpeg terminated abnormally, return error
    if out.returncode:
        if show_log:
            print(out.stderr)
        raise FFmpegError(out.stderr, show_log)

    # link a logger to each metadata field names (trailing "lavifi.")
    meta_logger = {name: l for l in loggers for name in l.meta_names}

    # stdout analysis
    re_metadata = re.compile(r"lavfi\.(.+?)(?:\.(.+?))?=(.+)")
    for m in re.finditer(
        r"frame:(\d+)\s+pts:(\d+)\s+pts_time:(\d+(?:\.\d+)?)\s*\n(.+?)(?=\nframe:|$)",
        out.stdout,
        re.DOTALL,
    ):
        logger.debug(f"analyze::run: {m[0]}")

        # logged time
        t = (int, int, float)[tunits - 1](m[tunits])

        # log each meta data
        for mm in re_metadata.finditer(m[4]):
            try:
                meta_logger[mm[1]].log(t, *mm.groups())
            except:
                pass  # ignore unknown metadata

    # return the loggers as convenience
    return loggers


class ScDet(MetadataLogger):
    """Logger for FFmpeg scdet filter to detect video scene change

    :param all_scores: True to return scene scores on all the frames, defaults to False
    :type all_scores: bool, optional
    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``scdet`` filter options
    -------------------------------

    =========  =====  ===============
    name       type   description
    =========  =====  ===============
    threshold  float  Set the scene change detection threshold as a percentage of maximum change.
                      Good values are in the [8.0, 14.0] range. The range for threshold is [0., 100.].
                      Defaults to 10. Alias param name: **t**

    sc_pass    int    Set the flag to pass scene change frames to the next filter. Default value is
                      0 You can enable it if you want to get snapshot of scene change frames only.
                      Alias param name: **s**
    =========  =====  ===============

    """

    class Scenes(NamedTuple):
        """Default output namedtuple subclass"""

        time: Tuple[float | int]  #: log times
        score: Tuple[float]  #: scene change scores
        mafd: Tuple[float]  #: mafd scores

    class AllScenes(NamedTuple):
        """Output namedtuple subclass for all_scores=True"""

        time: Tuple[float | int]  #: log times
        changed: Tuple[bool]  #: scene change flags
        score: Tuple[float]  #: scene change scores
        mafd: Tuple[float]  #: mafd scores

    #: (static) target stream media type
    media_type = "video"
    #: (static) metadata names to be logged
    meta_names = ("scd",)
    #: (static) name of the FFmpeg filter to use
    filter_name = "scdet"

    def __init__(self, all_scores=False, **options) -> None:
        self.all_scores = all_scores
        #: FFmpeg filter options (value must be stringifiable)
        self.options: dict[str, Any] = options
        self.data = {}

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if key == "mafd":  # always the first entry / frame
            self.data[t] = {"mafd": float(value)}
        elif key == "score":
            self.data[t]["score"] = float(value)
        elif key == "time":
            self.data[t]["changed"] = True

    @property
    def output(self) -> ScDet.Scenes | ScDet.AllScenes:
        """log output. Scenes if all_scores==True else AllScenes"""
        d = self.data
        if self.all_scores:
            times = sorted((t for t, v in self.data.items()))
            return self.AllScenes(
                tuple(times),
                *zip(
                    *(
                        (d[t].get("changed", False), d[t]["score"], d[t]["mafd"])
                        for t in times
                    )
                ),
            )
        else:
            times = sorted((t for t, v in d.items() if v.get("changed", False)))
            return self.Scenes(
                tuple(times), *zip(*((d[t]["score"], d[t]["mafd"]) for t in times))
            )


class BlackDetect(MetadataLogger):
    """Logger for FFmpeg blackdetect filter to detect video intervals that are (almost) black

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``blackdetect`` filter options
    -------------------------------------

    ======================  =====  ===============
    name                    type   description
    ======================  =====  ===============
    black_min_duration      float  set minimum detected black duration in seconds (from 0 to DBL_MAX) (default 2)
    picture_black_ratio_th  float  set the picture black ratio threshold (from 0 to 1) (default 0.98). Alias param name: **pic_th**
    pixel_black_th          float  set the pixel black threshold (from 0 to 1) (default 0.1). Alias param name: **pix_th**
    ======================  =====  ===============
    """

    # The following example sets the maximum pixel threshold to the minimum value, and detects only black intervals of 2 or more seconds:
    # blackdetect=d=2:pix_th=0.00

    class Black(NamedTuple):
        """output log namedtuple subclass"""

        interval: List[
            float | int | None, float | int | None
        ]  #: pairs of start and end timestamps of black intervals

    #: (static) target stream media type
    media_type = "video"
    #: (static) metadata names to be logged
    meta_names = ("black_start", "black_end")
    #: (static) name of the FFmpeg filter to use
    filter_name = "blackdetect"

    def __init__(self, **options):
        self.options = options
        self.interval = []

    def log(self, t: float | int, name: str, *_):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: metadata key
        :type bane: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if name == "black_start":
            self.interval.append([t, None])
        elif len(self.interval):
            self.interval[-1][-1] = t
        else:
            self.interval.append([None, t])

    @property
    def output(self) -> Black:
        """log output"""
        return self.Black(self.interval)


class BlackFrame(MetadataLogger):
    """Logger for FFmpeg blackframe filter to detect frames that are (almost) black

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``blackframe`` filter options
    ------------------------------------

    =========  ====  ===============
    name       type  description
    =========  ====  ===============
    amount     int   percentage of the pixels that have to be below the threshold for the frame to be considered black (from 0 to 100) (default 98)
    threshold  int   threshold below which a pixel value is considered black (from 0 to 255) (default 32). Alias param name: **thresh**
    =========  ====  ===============

    """

    #: (static) target stream media type
    media_type = "video"
    #: (static) metadata names to be logged
    meta_names = "blackframe"
    #: (static) name of the FFmpeg filter to use
    filter_name = "blackframe"

    class BlackFrames(NamedTuple):
        """output log namedtuple subclass"""

        time: List[float | int]  #: timestamps in seconds, frames, or pts
        pblack: List[int]  #: percentage of black pixels

    def __init__(self, **options):
        self.options = options
        self.frames = []

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if key != "pblack":
            raise ValueError(f"Unknown blackframe metadata found: {key}")
        self.frames.append((t, int(value)))

    @property
    def output(self) -> BlackFrames:
        """log output"""
        return self.BlackFrames(*zip(*self.frames))


class FreezeDetect(MetadataLogger):
    """Logger for FFmpeg freezedetect filter to detect frozen video input

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``freezedetect`` filter options
    --------------------------------------

    ========  ========  ===============
    name      type      description
    ========  ========  ===============
    noise     float     noise tolerance (from 0 to 1) (default 0.001). Alias param name: **n**
    duration  duration  minimum duration in seconds (default 2). Alias param name: **d**
    ========  ========  ===============
    """

    #: (static) target stream media type
    media_type: Literal["video"] = "video"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["freeze"]] = ("freeze",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["freezedetect"] = "freezedetect"

    class Frozen(NamedTuple):
        """output log namedtuple subclass"""

        #: pairs of start and end timestamps of frozen frame intervals
        interval: List[float | int | None, float | int | None]

    def __init__(self, **options):
        self.options = options
        self.interval = []

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if key == "freeze_start":
            self.interval.append([t, None])
        elif key == '"freeze_end"':
            if len(self.interval):
                self.interval[-1][-1] = t
            else:
                self.interval.append([None, t])

    @property
    def output(self) -> Frozen:
        """log output"""
        return self.Frozen(self.interval)


class BBox(MetadataLogger):
    """Logger for FFmpeg bbox filter to compute bounding box for each frame

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``bbox`` filter options
    ------------------------------

    =======  ====  ===============
    name     type  description
    =======  ====  ===============
    min_val  int   minimum luminance value for bounding box (from 0 to 65535) (default 16)
    enable   str   support for timeline. See `FFmpeg documentation <https://ffmpeg.org/ffmpeg-filters.html#Timeline-editing>`_.
    =======  ====  ===============
    """

    #: (static) target stream media type
    media_type: Literal["video"] = "video"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["bbox"]] = ("bbox",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["bbox"] = "bbox"

    class BBox(NamedTuple):
        """output log namedtuple subclass"""

        time: List[float | int]  #: timestamps in seconds, frames, or pts
        position: List[List[int, int, int, int]]  #: bbox positions [x0,x1,w,h]

    pos_keys = {"y1": 1, "w": 2, "h": 3}

    def __init__(self, **options):
        self.options = options
        self.time = []
        self.position = []

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if key == "x1":
            self.time.append(t)
            self.position.append([int(value), 0, 0, 0])
        else:
            try:
                key = self.pos_keys[key]
                self.position[-1][key] = int(value)
            except:
                pass

    @property
    def output(self) -> BBox.BBox:
        """log output"""
        return self.BBox(self.time, self.position)


class BlurDetect(MetadataLogger):
    """Logger for FFmpeg blurdetect filter to detect video frames that are blurry

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``blurdetect`` filter options
    ------------------------------------

    ============  =====  ===============
    name          type   description
    ============  =====  ===============
    high          float  high threshold (from 0 to 1) (default 0.117647)
    low           float  low threshold (from 0 to 1) (default 0.0588235)
    radius        int    search radius for maxima detection (from 1 to 100) (default 50)
    block_pct     int    block pooling threshold when calculating blurriness (from 1 to 100) (default 80)
    block_width   int    block width for block-based abbreviation of blurriness (from -1 to INT_MAX) (default -1)
    block_height  int    block height for block-based abbreviation of blurriness (from -1 to INT_MAX) (default -1)
    planes        int    set planes to filter (from 0 to 15) (default 1)
    ============  =====  ===============
    """

    #: (static) target stream media type
    media_type: Literal["video"] = "video"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["blur"]] = ("blur",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["blurdetect"] = "blurdetect"

    class Blur(NamedTuple):
        """output log namedtuple subclass"""

        time: List[float | int]  #: timestamps in seconds, frames, or pts
        blur: List[float]  #: blurness score

    def __init__(self, **options):
        self.options = options
        self.frames = []

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """
        if name != "blur":
            raise ValueError(f"Unknown blurdetect metadata found: {name}")
        self.frames.append((t, float(value)))

    @property
    def output(self) -> BlurDetect.Blur:
        """log output"""
        return self.Blur(*zip(*self.frames))


#  'frame:26   pts:26026   pts_time:0.867533\n'
#  'lavfi.entropy.entropy.normal.Y=4.762884\n'
#  'lavfi.entropy.normalized_entropy.normal.Y=0.595360\n'
#  'lavfi.entropy.entropy.normal.U=4.609038\n'
#  'lavfi.entropy.normalized_entropy.normal.U=0.576130\n'
#  'lavfi.entropy.entropy.normal.V=4.532040\n'
#  'lavfi.entropy.normalized_entropy.normal.V=0.566505\n'
class PSNR(MetadataLogger):
    """Logger for FFmpeg psnr filter to calculate the PSNR between two video streams

    :param ref_stream_spec: stream specifier expression for the reference stream, defaults to '1:v'
    :type ref_stream_spec: str, optional
    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``psnr`` filter options
    ------------------------------

    =============  =====  ===============
    name           type   description
    =============  =====  ===============
    stats_file     str    file where to store per-frame difference information. Alias param name: **f**
    stats_version  int    format version for the stats file. (from 1 to 2) (default 1)
    output_max     bool   add raw stats (max values) to the output log. (default false)
    eof_action            action to take when encountering EOF from secondary input (default repeat)
    \              \        repeat (0) - Repeat the previous frame.
    \              \        endall (1) - End both streams.
    \              \        pass   (2) - Pass through the main input.
    shortest       bool   force termination when the shortest input terminates (default false)
    repeatlast     bool   extend last frame of secondary streams beyond EOF (default true)
    =============  =====  ===============
    """

    #: (static) target stream media type
    media_type: Literal["video"] = "video"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["psnr"]] = ("psnr",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["psnr"] = "psnr"
    re_key = re.compile(r"([^.]+)(?:\.(.))?")

    class PSNR(NamedTuple):
        """output log namedtuple subclass (template)"""

        time: List[float | int]  #: timestamps in seconds, frames, or pts
        mse: List[float]  #: blurness score
        psnr: List[float]  #: blurness score
        # mse.[c]: List[float] #: blurness score
        # psnr.[c]: List[float] #: blurness score

    def __init__(self, ref_stream_spec: str = "1:v", **options):
        self.options = options
        self.time = []
        self.comps = []
        self.stats = {}
        self._first = None
        self._ref = ref_stream_spec or "1:v"

    @property
    def ref_in(self):
        """stream specifier for reference input url only if applicable (default: None)"""
        return self._ref

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """

        m = self.re_key.match(key)
        if not (m and m[1]):
            logger.warning(f"[PSNR.log()] Unknown metadata key: {key}")
            return

        if not self._first:
            self._first = key

        name, comp = m.groups()

        new_row = key == self._first
        if new_row:
            self.time.append(t)
        if comp:
            n = len(t)
            if n == 1:
                self.comps.append(comp)
            try:
                stat = self.stats[name]
            except:
                stat = self.stats[name] = []
            if len(stat) < n:
                l = []
                stat.append(l)
            else:
                l = stat[-1]
        else:
            try:
                l = self.stats[name]
            except:
                l = self.stats[name] = []

        l.append(float(value))

    @property
    def output(self):
        """log output"""
        Output = namedtuple("PSNR", ["time", *self.stats.keys()])
        return Output(self.time, *self.stats.values())


class SilenceDetect(MetadataLogger):
    """Logger for FFmpeg silencedetect filter to detect silent audio intervals

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``silencedetect`` filter options
    ---------------------------------------

    ========  ========  ===============
    name      type      description
    ========  ========  ===============
    noise     double    noise tolerance (from 0 to DBL_MAX) (default 0.001). Alias param name: **n**
    duration  duration  minimum duration in seconds (default 2). Alias param name: **d**
    mono      bool      check each channel separately (default false). Alias param name: **m**
    ========  ========  ===============
    """

    #: (static) target stream media type
    media_type: Literal["audio"] = "audio"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["silence_start", "silence_end"]] = (
        "silence_start",
        "silence_end",
    )
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["silencedetect"] = "silencedetect"

    class Silent(NamedTuple):
        """output log namedtuple subclass for ``mono=False`` (default)"""

        #: pairs of start and end timestamps of frozen frame intervals
        interval: List[float | int | None, float | int | None]

    def __init__(self, **options):
        self.options = options
        self.interval = []
        self.mono_intervals = {}  # mono intervals

    def log(self, t: float | int, name: str, ch: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param ch: audio channel key
        :type ch: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """

        if ch is None:
            i = self.interval
        else:
            ch = int(ch) - 1
            try:
                i = self.mono_intervals[ch]
            except:
                i = self.mono_intervals[ch] = []

        if name == "silence_start":
            i.append([t, None])
        elif len(i):
            i[-1][-1] = t
        else:
            i.append([None, t])

    @property
    def output(self) -> Silent | NamedTuple:
        """log output

        If the silentdetect filter is configured with ``mono=False`` (default), the returned log is
        a :py:class:`SilenceDetect.Silent` object.

        If ``mono=True``, the returned log is a dynamically formed namedtuple of the name **SilentPerCh**,
        each of which field is named ``ch#`` (where ``#`` is an integer) and contains a list of the
        silent intevals of the specified audio channel.

        """
        nch = len(self.mono_intervals)
        if nch:
            channels = sorted(self.mono_intervals.keys())
            ints = [self.mono_intervals[ch] for ch in channels]
            return namedtuple("SilentPerCh", [f"ch{ch}" for ch in channels])(*ints)
        else:
            return self.Silent(self.interval)


class APhaseMeter(MetadataLogger):
    """Logger for FFmpeg aphasemeter filter to measure stereo audio phase differences

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``aphasemeter`` filter options
    -------------------------------------

    =========  ========  ===============
    name       type      description
    =========  ========  ===============
    phasing    bool      mono and out-of-phase detection output (default false)
    tolerance  float     phase tolerance for mono detection (from 0 to 1) (default 0). Alias param name: **t**
    angle      float     angle threshold for out-of-phase detection (from 90 to 180) (default 170). Alias param name: **a**
    duration   duration  minimum mono or out-of-phase duration in seconds (default 2). Alias param name: **d**
    =========  ========  ===============
    """

    #: (static) target stream media type
    media_type: Literal["audio"] = "audio"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["aphasemeter"]] = ("aphasemeter",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["aphasemeter"] = "aphasemeter"

    class Phase(NamedTuple):
        """output log namedtuple subclass"""

        time: List[float | int]  #: timestamps in seconds, frames, or pts
        value: List[float]  #: detected phases
        #: intervals in which stereo stream is in-phase
        mono_interval: List[float | int | None, float | int | None]
        #: intervals in which stereo stream is out-of-phase
        out_phase_interval: List[float | int | None, float | int | None]

    def __init__(self, **options):
        self.options = options
        self.time = []
        self.value = []
        self.mono = []
        self.out_phase = []

    @property
    def filter(self):
        """filter specification expression to be used in FilterGraph"""
        return Filter(self.filter_name, **self.options, video=False, phasing=True)

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """

        if key == "phase":
            self.time.append(t)
            self.value.append(float(value))
        else:
            ptype, action = key.rsplit("_", 1)
            i = getattr(self, ptype)

            if action == ("start"):
                i.append([t, None])
            elif action == "end":
                if len(i):
                    i[-1][-1] = t
                else:
                    i.append([None, t])

    @property
    def output(self) -> APhaseMeter.Phase:
        """log output"""
        return self.Phase(self.time, self.value, self.mono, self.out_phase)


class AStats(MetadataLogger):
    """Logger for FFmpeg astats filter to measure time domain statistics per audio frames

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``astats`` filter options
    --------------------------------

    ==================  =====  ===============
    name                type   description
    ==================  =====  ===============
    length              float  window length (from 0 to 10) (default 0.05)
    metadata            bool   true to inject metadata in the filtergraph (default false)
    reset               int    number of frames over which cumulative stats are calculated before being reset (from 0 to INT_MAX) (default 0)
    measure_perchannel  str    parameters to measure per channel (default "all") "none" to disable
    measure_overall     str    parameters to measure overall (default "all") "none" to disable
    ==================  =====  ===============

    Measurement parameters
    ----------------------

    Following parameters can be used for ``measure_perchannel`` and ``measure_overall``. To specify
    multiple parameters, combine them with ``+`` (plus) signs. E.g., "DC_offset+Min_level".

    - DC_offset
    - Min_level
    - Max_level
    - Min_difference
    - Max_difference
    - Mean_difference
    - RMS_differenc
    - Peak_level
    - RMS_level
    - RMS_peak
    - RMS_trough
    - Crest_factor
    - Flat_factor
    - Peak_count
    - Bit_depth
    - Dynamic_range
    - Zero_crossings
    - Zero_crossings_rate
    - Noise_floor
    - Noise_floor_count
    - Entropy
    - Number_of_samples
    - Number_of_NaNs
    - Number_of_Infs
    - Number_of_denormals
    """

    #: (static) target stream media type
    media_type: Literal["audio"] = "audio"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["astats"]] = ("astats",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["astats"] = "astats"
    re_key = re.compile(r"(?:(\d+|Overall)\.)?([\s\S]+)")

    def __init__(self, **options):
        self.options = options
        self.time = []
        self.stats = {}
        self._first = None

    @property
    def filter(self):
        """filter specification expression to be used in FilterGraph"""
        return Filter(self.filter_name, **self.options, metadata=True)

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        lavfi.astats.1.Number of Infs=0.000000
        lavfi.astats.2.Number of denormals=0.000000
        lavfi.astats.Overall.DC_offset=-0.000003
        lavfi.astats.Overall.Min_level=-0.092316
        lavfi.astats.Overall.Max_level=0.100442
        """

        if not self._first:
            self._first = key

        if key == self._first:
            self.time.append(t)

        m = self.re_key.match(key)
        if not m:
            logger.warning(f"[AStats.log()] Unknown metadata key: {key}")
            return
        ch, name = m.groups()

        # get stat name and storage dict
        meas = name.lower().replace(" ", "_")
        try:
            stat = self.stats[meas]
        except:
            stat = self.stats[meas] = [] if ch is None else {}

        # get channel number and storage list
        try:
            ch = int(ch)
        except:
            ch = "overall"

        try:
            l = stat[ch]
        except:
            l = stat[ch] = []

        # use int or float
        v = float(value)
        l.append(int(v) if value.endswith(".000000") else v)

    @property
    def output(self) -> NamedTuple:
        """log output

        AStats' log output is a dynamically composed namedtuple. Every field
        contains lists of statistics. Except for the :py:obj:`time` field, which is
        a plain list, the fields are a :py:obj:`dict`, each of which item
        keyed by the channel number in :py:obj:`int` (1, 2, ...) or literal
        :py:obj:`"overall"` and contains a :py:obj:`list` of the statistics
        computed at each analysis window. The full list of possible fields
        for FFmpeg v5 and its individual stat datatype is shown below:

        ===================  =========  =====
        field name           datatype   description
        ===================  =========  =====
        time                 float|int  timestamps in seconds, frames, or pts
        dc_offset            float      DC offset
        min_level            float      Min level
        max_level            float      Max level
        min_difference       float      Min difference
        max_difference       float      Max difference
        mean_difference      float      Mean difference
        rms_difference       float      RMS difference
        peak_level           float      Peak level dB
        rms_level            float      RMS level dB
        rms_peak             float      RMS peak dB
        rms_trough           float      RMS trough dB
        crest_factor         float      Crest factor
        flat_factor          float      Flat factor
        peak_count           int        Peak count
        noise_floor          float      Noise floor dB
        noise_floor_count    int        Noise floor count
        entropy              float      Entropy
        bit_depth            int        Bit depth (available)
        bit_depth2           int        Bit depth (used)
        dynamic_range        float      Dynamic range
        zero_crossings       float      Zero crossings
        zero_crossings_rate  float      Zero crossings rate
        number_of_nans       int        Number of NaNs
        number_of_infs       int        Number of Infs
        number_of_denormals  int        Number of denormals
        ===================  =========  =====

        """

        # self._bit_depth2
        Output = namedtuple("AStats", ["time", *self.stats.keys()])

        return Output(self.time, *self.stats.values())


class ASpectralStats(MetadataLogger):
    """Logger for FFmpeg aspectralstats filter to measure frequency domain statistics about audio frames

    :param \**options: FFmpeg filter options (see below)
    :type \**options: dict[str, any]

    FFmpeg ``aspectralstats`` filter options
    ----------------------------------------

    ========  =======  ===============
    name      type     description
    ========  =======  ===============
    win_size  int      set the window size (from 32 to 65536) (default 2048)
    win_func  str|int  set window function (see below for the accepted values) (default hann)
    overlap   float    set window overlap (from 0 to 1) (default 0.5)
    ========  =======  ===============

    Supported ``win_func`` option values
    ------------------------------------

    The ``win_func`` option can be set to any of the following window function by its name or
    id:

    ========  ==  =====
    name      id  desc
    ========  ==  =====
    bartlett   4  Bartlett
    bhann     11  Bartlett-Hann
    bharris    7  Blackman-Harris
    blackman   3  Blackman
    bnuttall   8  Blackman-Nuttall
    bohman    19  Bohman
    cauchy    16  Cauchy
    dolph     15  Dolph-Chebyshev
    flattop    6  Flat-top
    gauss     13  Gauss
    hamming    2  Hamming
    hann       1  Hann
    hanning    1  Hanning
    lanczos   12  Lanczos
    nuttall   10  Nuttall
    parzen    17  Parzen
    poisson   18  Poisson
    rect       0  Rectangular
    sine       9  Sine
    tukey     14  Tukey
    welch      5  Welch
    ========  ==  =====

    """

    #: (static) target stream media type
    media_type: Literal["audio"] = "audio"
    #: (static) metadata names to be logged
    meta_names: Tuple[Literal["aspectralstats"]] = ("aspectralstats",)
    #: (static) name of the FFmpeg filter to use
    filter_name: Literal["aspectralstats"] = "aspectralstats"
    re_key = re.compile(r"(?:(\d+)\.)?(.+)")

    def __init__(self, **options):
        self.options = options
        self.time = []
        self.stats = {}
        self._first = None

    def log(self, t: float | int, name: str, key: Optional[str], value: str):
        """log the metadata

        :param t: timestamps in seconds, frames, or pts
        :type t: float|int
        :param name: one of the class' meta_names
        :type name: str
        :param key: secondary metadata key if found
        :type key: str | None
        :param value: metadata value
        :type value: str

        This method is called by :py:func:`analyze.run` if a metadata line begins
        with one of the class' ``meta_names`` entry. The `log` method shall store
        the metadata info in a private storage property of the class so they can be
        returned later by the `output` property.

        """

        m = self.re_key.match(key)
        if not (m and m[1]):
            logger.warning(f"[ASpectralStats.log()] Unknown metadata key: {key}")
            return

        if not self._first:
            self._first = key

        ch, name = m.groups()

        if key == self._first:
            self.time.append(t)

        try:
            stat = self.stats[name]
        except:
            stat = self.stats[name] = {}

        ch = int(ch)
        try:
            l = stat[ch]
        except:
            l = stat[ch] = []

        l.append(float(value))

    @property
    def output(self):
        """log output

        ASpectalStats' log output is a dynamically composed namedtuple. Each
        statistic is stored in its own named field as a :py:obj:`dict` of
        per-channel :py:obj:`list` of measurements. The :py:obj:`dict` is
        keyed by the audio channel ids (positive :py:obj:`int`).
        One exception is the :py:obj:`time` field, which is a plain :py:obj:`list`
        of the starting timestamps of analysis windows.

        Here is the full list of possible fields for FFmpeg v5:

        * time
        * mean
        * variance
        * centroid
        * spread
        * skewness
        * kurtosis
        * entropy
        * flatness
        * crest
        * flux
        * slope
        * decrease
        * rolloff

        All the stats are computed in the linear scale (not in dB).

        """

        Output = namedtuple("ASpectralStats", ["time", *self.stats.keys()])
        return Output(self.time, *self.stats.values())
