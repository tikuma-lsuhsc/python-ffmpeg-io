import warnings
from . import ffmpegprocess as fp, utils, configure, FFmpegError, plugins, analyze
from .utils import log as log_utils

__all__ = ["create", "read", "write", "filter", "detect"]


def _run_read(*args, show_log=None, sp_kwargs=None, **kwargs):
    """run FFmpeg and retrieve audio stream data
    :param *args ffmpegprocess.run arguments
    :type *args: tuple
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**kwargs: All additional keyword arguments to call `ffmpegprocess.run`.
                       These keywords take precedence over `sp_kwargs`.
    :type \\**kwargs: dict, optional
    :return: video data, created by `bytes_to_video` plugin hook
    :rtype: object
    """

    dtype, shape, r = configure.finalize_video_read_opts(args[0], istream="v:0")

    if sp_kwargs is not None:
        kwargs = {**sp_kwargs, **kwargs}

    if shape is None or r is None:
        configure.clear_loglevel(args[0])

        out = fp.run(*args, capture_log=True, **kwargs)
        if show_log:
            print(out.stderr)
        if out.returncode:
            raise FFmpegError(out.stderr)

        info = log_utils.extract_output_stream(out.stderr)
        dtype, shape = utils.get_video_format(info["pix_fmt"], info["s"])
        r = info["r"]
    else:
        out = fp.run(
            *args,
            capture_log=None if show_log else False,
            **kwargs,
        )
        if out.returncode:
            raise FFmpegError(out.stderr)
    return r, plugins.get_hook().bytes_to_video(
        b=out.stdout, dtype=dtype, shape=shape, squeeze=False
    )


def create(expr, *args, progress=None, show_log=None, sp_kwargs=None, **options):
    """Create a video using a source video filter

    :param name: name of the source filter
    :type name: str
    :param \\*args: sequential filter option arguments. Only valid for
                    a single-filter expr, and they will overwrite the
                    options set by expr.
    :type \\*args: seq, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**options: Named filter options or FFmpeg options. Items are
                        only considered as the filter options if expr is a
                        single-filter graph, and take the precedents over
                        general FFmpeg options. Append '_in' for input
                        option names (see :doc:`options`), and '_out' for
                        output option names if they conflict with the filter
                        options.
    :type \\**options: dict, optional
    :return: frame rate and video data, created by `bytes_to_video` plugin hook
    :rtype: tuple[Fraction,object]

    ...seealso::
      https://ffmpeg.org/ffmpeg-filters.html#Video-Sources for available
      video source filters

    """

    input_options = utils.pop_extra_options(options, "_in")
    output_options = utils.pop_extra_options(options, "_out")
    url, t_, options = configure.config_input_fg(expr, args, options)
    options = {**options, **output_options}

    if (
        t_ is None
        and not any(a in input_options for a in ("t", "to"))
        and not any(a in options for a in ("t", "to", "frames:v", "vframes"))
    ):
        warnings.warn(
            "neither input nor output duration specified. this function call may hang."
        )

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, {**input_options, "f": "lavfi"})
    configure.add_url(
        ffmpeg_args, "output", "-", {"pix_fmt": "rgb24", **options, "f": "rawvideo"}
    )
    # TODO: filtergraph scanning will remove the default 'pix_fmt' setting

    return _run_read(
        ffmpeg_args, progress=progress, show_log=show_log, sp_kwargs=sp_kwargs
    )


def read(url, progress=None, show_log=None, sp_kwargs=None, **options):
    """Read video frames

    :param url: URL of the video file to read.
    :type url: str
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional

    :return: frame rate and video frame data, created by `bytes_to_video` plugin hook
    :rtype: (fractions.Fraction, object)
    """

    # get pix_fmt of the input file only if needed
    input_options = utils.pop_extra_options(options, "_in")

    # get url/file stream
    url, stdin, input = configure.check_url(
        url, False, format=input_options.get("f", None)
    )

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, input_options)
    configure.add_url(ffmpeg_args, "output", "-", options)

    # override user specified stdin and input if given
    sp_kwargs = {**sp_kwargs} if sp_kwargs else {}
    sp_kwargs["stdin"] = stdin
    sp_kwargs["input"] = input

    return _run_read(
        ffmpeg_args, progress=progress, show_log=show_log, sp_kwargs=sp_kwargs
    )


def write(
    url,
    rate_in,
    data,
    progress=None,
    overwrite=None,
    show_log=None,
    two_pass=False,
    pass1_omits=None,
    pass1_extras=None,
    extra_inputs=None,
    sp_kwargs=None,
    **options,
):
    """Write Numpy array to a video file

    :param url: URL of the video file to write.
    :type url: str
    :param rate_in: frame rate in frames/second
    :type rate_in: `float`, `int`, or `fractions.Fraction`
    :param data: video frame data object, accessed by `video_info` and `video_bytes` plugin hooks
    :type data: object
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param overwrite: True to overwrite if output url exists, defaults to None
                      (auto-select)
    :type overwrite: bool, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param two_pass: True to encode in 2-pass
    :param pass1_omits: list of output arguments to ignore in pass 1, defaults to None
    :type pass1_omits: seq(str), optional
    :param pass1_extras: list of additional output arguments to include in pass 1, defaults to None
    :type pass1_extras: dict(int:dict(str)), optional
    :param extra_inputs: list of additional input sources, defaults to None. Each source may be url
                         string or a pair of a url string and an option dict.
    :type extra_inputs: seq(str|(str,dict))
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional
    """

    url, stdout, _ = configure.check_url(url, True)

    input_options = utils.pop_extra_options(options, "_in")

    ffmpeg_args = configure.empty()
    configure.add_url(
        ffmpeg_args,
        "input",
        *configure.array_to_video_input(rate_in, data=data, **input_options),
    )

    # add extra input arguments if given
    if extra_inputs is not None:
        configure.add_urls(ffmpeg_args, "input", extra_inputs)

    configure.add_url(ffmpeg_args, "output", url, options)

    configure.build_basic_vf(ffmpeg_args, configure.check_alpha_change(ffmpeg_args, -1))

    kwargs = {**sp_kwargs} if sp_kwargs else {}
    kwargs.update(
        {
            "input": plugins.get_hook().video_bytes(obj=data),
            "stdout": stdout,
            "progress": progress,
            "overwrite": overwrite,
        }
    )
    kwargs["capture_log"] = None if show_log else False
    if pass1_omits is not None:
        kwargs["pass1_omits"] = [pass1_omits]
    if pass1_extras is not None:
        kwargs["pass1_extras"] = [pass1_extras]

    out = (fp.run_two_pass if two_pass else fp.run)(ffmpeg_args, **kwargs)
    if out.returncode:
        raise FFmpegError(out.stderr, show_log)


def filter(expr, rate, input, progress=None, show_log=None, sp_kwargs=None, **options):
    """Filter video frames.

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str,  None
    :param rate: input frame rate in frames/second
    :type rate: `float`, `int`, or `fractions.Fraction`
    :param input: input video frame data object, accessed by `video_info` and `video_bytes` plugin hooks
    :type input: object
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional
    :return: output frame rate and video frame data, created by `bytes_to_video` plugin hook
    :rtype: object

    """

    input_options = utils.pop_extra_options(options, "_in")

    ffmpeg_args = configure.empty()
    configure.add_url(
        ffmpeg_args,
        "input",
        *configure.array_to_video_input(rate, data=input, **input_options),
    )
    outopts = configure.add_url(ffmpeg_args, "output", "-", options)[1][1]

    if expr:
        outopts["filter:v"] = expr

    # override user specified stdin and input if given
    sp_kwargs = {**sp_kwargs} if sp_kwargs else {}
    sp_kwargs["stdin"] = None
    sp_kwargs["input"] = plugins.get_hook().video_bytes(obj=input)

    return _run_read(
        ffmpeg_args, progress=progress, show_log=show_log, sp_kwargs=sp_kwargs
    )


def detect(
    url,
    *features,
    ss=None,
    t=None,
    to=None,
    start_at_zero=False,
    time_units=None,
    progress=None,
    show_log=None,
    scene_all_scores=False,
    **options,
):
    """detect video frame features

    :param url: video file url
    :type url: str
    :param \*features: specify frame features to detect:

        ============  ===============  =========================================================
        feature       FFmpeg filter    description
        ============  ===============  =========================================================
        'scene'       `scdet`_          Detect video scene change
        'black'       `blackdetect`_    Detect video intervals that are (almost) completely black
        'blackframe'  `blackframe`_     Detect frames that are (almost) completely black
        'freeze'      `freezedetect`_   Detect frozen video
        ============  ===============  =========================================================

        defaults to include all the features
    :type \*features: tuple, a subset of ('scene', 'black', 'blackframe', 'freeze'), optional
    :param ss: start time to process, defaults to None
    :type ss: int, float, str, optional
    :param t: duration of data to process, defaults to None
    :type t: int, float, str, optional
    :param to: stop processing at this time (ignored if t is also specified), defaults to None
    :type to: int, float, str, optional
    :param start_at_zero: ignore start time, defaults to False
    :type start_at_zero: bool, optional
    :param time_units: units of detected time stamps (not for ss, t, or to), defaults to None ('seconds')
    :type time_units: 'seconds', 'frames', 'pts', optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param scene_all_scores: (only for 'scene' feature) True to return scores for all frames, defaults to False
    :type scene_all_scores: bool, optional
    :param \**options: FFmpeg detector filter options. For a single-feature call, the FFmpeg filter options
        of the specified feature can be specified directly as keyword arguments. For a multiple-feature call,
        options for each individual FFmpeg filter can be specified with <feature>_options dict keyword argument.
        Any other arguments are treated as a common option to all FFmpeg filters. For the available options
        for each filter, follow the link on the feature table above to the FFmpeg documentation.
    :type \**options: dict, optional
    :return: detection outcomes. A namedtuple is returned for each feature in the order specified.
        All namedtuple fields contain a list with the element specified as below:

        .. list-table::
           :header-rows: 1
           :widths: auto

           * - feature
             - named tuple field
             - element type
             - description
           * - 'scene'
             - 'time'
             - numeric
             - Timestamp of the frame
           * -
             - 'change'
             - bool
             - True if scene change detected (only present if ``scene_all_scores=True``)
           * -
             - 'score'
             - 'float'
             - Absolute difference of MAFD of current and previous frame
           * -
             - 'mafd'
             - float
             - Mean absolute frame difference. See `this commentary`_ for detailed discussion of the MAFD.
           * - 'black'
             - 'interval'
             - (numeric, numeric)
             - Interval of black frames
           * - 'blackframe'
             - 'time'
             - numeric
             - Timestamp of a black frame
           * -
             - 'pblack'
             - int
             - Percentage of black pixels
           * - 'freeze'
             - 'interval'
             - (numeric, numeric)
             - Interval of frozen frames

    :rtype: tuple of namedtuples

    Examples
    --------

    .. code-block::python

        ffmpegio.video.detect('video.mp4', 'scene')

    .. _scdet: https://ffmpeg.org/ffmpeg-filters.html#scdet-1
    .. _blackdetect: https://ffmpeg.org/ffmpeg-filters.html#blackdetect
    .. _blackframe: https://ffmpeg.org/ffmpeg-filters.html#blackframe
    .. _freezedetect: https://ffmpeg.org/ffmpeg-filters.html#freezedetect
    .. _this commentary: https://rusty.today/posts/ffmpeg-scene-change-detector

    """

    all_detectors = {
        "black": analyze.BlackDetect,
        "blackframe": analyze.BlackFrame,
        "freeze": analyze.FreezeDetect,
        "scene": analyze.ScDet,
    }

    if not len(features):
        features = [*all_detectors.keys()]

    # pop detector-specific options
    det_options = [options.pop(f"{k}_options", None) for k in features]

    # create loggers
    try:
        loggers = [all_detectors[k](**options) for k in features]
    except:
        raise ValueError(f"Unknown feature(s) specified: {features}")

    # add detector-specific options
    for l, o in zip(loggers, det_options):
        if o is not None:
            l.options.update(**o)
        if l.filter_name == "scdet":
            l.all_frames = bool(scene_all_scores)

    # exclude unspecified input options
    input_opts = {k: v for k, v in zip(("ss", "t", "to"), (ss, t, to)) if v is not None}

    # run analysis
    analyze.run(
        url,
        *loggers,
        start_at_zero=start_at_zero,
        time_units=time_units,
        progress=progress,
        show_log=show_log,
        **input_opts,
    )

    return tuple((l.output for l in loggers))
