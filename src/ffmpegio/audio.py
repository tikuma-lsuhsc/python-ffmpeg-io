"""Audio Read/Write Module
"""

import warnings
from . import ffmpegprocess, utils, configure, FFmpegError, plugins, analyze
from .utils import log as log_utils

__all__ = ["create", "read", "write", "filter", "detect"]


def _run_read(
    *args,
    show_log=None,
    sp_kwargs=None,
    **kwargs,
):
    """run FFmpeg and retrieve audio stream data
    :param *args ffmpegprocess.run arguments
    :type *args: tuple
    :param sample_fmt_in: input sample format if known but not specified in the ffmpeg arg dict, defaults to None
    :type sample_fmt_in: str, optional
    :param ac_in: number of input channels if known but not specified in the ffmpeg arg dict, defaults to None
    :type ac_in: int, optional
    :param ar_in: input sampling rate if known but not specified in the ffmpeg arg dict, defaults to None
    :type ar_in: int, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param **kwargs ffmpegprocess.run keyword arguments
    :type **kwargs: tuple
    :return: [description]
    :rtype: [type]
    """
    """

    :param show_log: True to show FFmpeg log messages on the console, 
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :rtype: (int, str)
    """

    outopts = args[0]["outputs"][0][1]
    outopts["map"] = "0:a:0"
    dtype, ac, rate = configure.finalize_audio_read_opts(
        args[0],
        input_info=[
            {"src_type": "filtergraph" if outopts.get("f", None) == "lavfi" else "url"}
        ],
    )

    if sp_kwargs is not None:
        kwargs = {**sp_kwargs, **kwargs}

    if ac is None or rate is None:
        configure.clear_loglevel(args[0])

        out = ffmpegprocess.run(*args, capture_log=True, **kwargs)
        if show_log:
            print(out.stderr)
        if out.returncode:
            raise FFmpegError(out.stderr)

        info = log_utils.extract_output_stream(out.stderr)

        ac = info.get("ac", None)
        rate = info.get("ar", None)
    else:
        out = ffmpegprocess.run(
            *args,
            capture_log=None if show_log else True,
            **kwargs,
        )
        if out.returncode:
            raise FFmpegError(out.stderr, show_log)

    return rate, plugins.get_hook().bytes_to_audio(
        b=out.stdout, dtype=dtype, shape=(ac,), squeeze=False
    )


def create(expr, *args, progress=None, show_log=None, sp_kwargs=None, **options):
    """Create audio data using an audio source filter

    :param expr: name of the source filter or full filter expression
    :type expr: str
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
    :return: sampling rate and audio data (a plugin may change this behavior
             with the `bytes_to_audio` hook.)
    :rtype: tuple[int, object]

    .. seealso::
        https://ffmpeg.org/ffmpeg-filters.html#Audio-Sources for available
        audio source filters

    .. warning::
        Nearly all the source filters by default continue outputting
        indefinitely. Set its  `duration` option or FFmpeg's `t` (duration)
        or `to` (end time) input/output options to make sure the function
        returns properly.

    .. note::
        output data object is determined by the selected  hook

    """

    input_options = utils.pop_extra_options(options, "_in")
    output_options = utils.pop_extra_options(options, "_out")
    url, t_, options = configure.config_input_fg(expr, args, options)
    options = {**options, **output_options}

    if (
        t_ is None
        and not any(a in input_options for a in ("t", "to"))
        and not any(a in options for a in ("t", "to", "frames:a", "aframes"))
    ):
        warnings.warn(
            "neither input nor output duration specified. this function call may hang."
        )

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, {**input_options, "f": "lavfi"})[1][1]
    configure.add_url(ffmpeg_args, "output", "-", {"sample_fmt": "dbl", **options})[1][
        1
    ]

    return _run_read(
        ffmpeg_args,
        progress=progress,
        show_log=show_log,
        sp_kwargs=sp_kwargs,
    )


def read(url, progress=None, show_log=None, sp_kwargs=None, **options):
    """Read audio samples.

    :param url: URL of the audio file to read.
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
    :return: sample rate in samples/second and audio data object specified by `bytes_to_audio` plugin hook
    :rtype: tuple(float, object)

    .. note:: Even if :code:`start_time` option is set, all the prior samples will be read.
        The retrieved data will be truncated before returning it to the caller.
        This is to ensure the timing accuracy. As such, do not use this function
        to perform block-wise processing. Instead use the streaming solution,
        see :py:func:`open`.


    """

    input_options = utils.pop_extra_options(options, "_in")
    url, stdin, input = configure.check_url(
        url, False, format=input_options.get("f", None)
    )

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, input_options)[1][1]
    configure.add_url(ffmpeg_args, "output", "-", options)[1][1]

    # override user specified stdin and input if given
    sp_kwargs = {**sp_kwargs} if sp_kwargs else {}
    sp_kwargs["stdin"] = stdin
    sp_kwargs["input"] = input

    return _run_read(
        ffmpeg_args,
        progress=progress,
        show_log=show_log,
        sp_kwargs=sp_kwargs,
    )


def write(
    url,
    rate_in,
    data,
    progress=None,
    overwrite=None,
    show_log=None,
    extra_inputs=None,
    sp_kwargs=None,
    **options,
):
    """Write a NumPy array to an audio file.

    :param url: URL of the audio file to write.
    :type url: str
    :param rate_in: The sample rate in samples/second.
    :type rate_in: int
    :param data: input audio data object, converted to bytes by `audio_bytes` plugin hook .
    :type data: object
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param overwrite: True to overwrite if output url exists, defaults to None
                      (auto-select)
    :type overwrite: bool, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
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
        *configure.array_to_audio_input(rate_in, data=data, **input_options),
    )

    # add extra input arguments if given
    if extra_inputs is not None:
        configure.add_urls(ffmpeg_args, "input", extra_inputs)

    configure.add_url(ffmpeg_args, "output", url, options)

    kwargs = {**sp_kwargs} if sp_kwargs else {}
    kwargs.update(
        {
            "input": plugins.get_hook().audio_bytes(obj=data),
            "stdout": stdout,
            "progress": progress,
            "overwrite": overwrite,
        }
    )
    kwargs["capture_log"] = None if show_log else False

    out = ffmpegprocess.run(ffmpeg_args, **kwargs)
    if out.returncode:
        raise FFmpegError(out.stderr, show_log)


def filter(
    expr,
    input_rate,
    input,
    progress=None,
    show_log=None,
    sp_kwargs=None,
    **options,
):
    """Filter audio samples.

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str, None
    :param input_rate: Input sample rate in samples/second
    :type input_rate: int
    :param input: input audio data, accessed by `audio_info()` and `audio_bytes()` plugin hooks.
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
    :return: output sampling rate and audio data object, created by `bytes_to_audio` plugin hook
    :rtype: tuple(int, object)

    """

    input_options = utils.pop_extra_options(options, "_in")

    ffmpeg_args = configure.empty()
    configure.add_url(
        ffmpeg_args,
        "input",
        *configure.array_to_audio_input(input_rate, data=input, **input_options),
    )
    outopts = configure.add_url(ffmpeg_args, "output", "-", options)[1][1]
    if expr:
        outopts["filter:a"] = expr

    return _run_read(
        ffmpeg_args,
        input=plugins.get_hook().audio_bytes(obj=input),
        progress=progress,
        show_log=show_log,
        sp_kwargs=sp_kwargs,
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
    **options,
):
    """detect audio stream features

    :param url: audio file url
    :type url: str
    :param \*features: specify features to detect:

        ============  ================  =========================================================
        feature       FFmpeg filter     description
        ============  ================  =========================================================
        'silence'     `silencedetect`_  Detect silence in an audio stream
        ============  ================  =========================================================

        defaults to include all the features
    :type \*features: tuple, a subset of ('silence',), optional
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
           * - 'silence'
             - 'interval'
             - (numeric, numeric)
             - (only if mono=False) Silent interval
           * -
             - 'chX'
             - (numeric, numeric)
             - (only if mono=True) Silent interval of channel X (multiple)

    :rtype: tuple of namedtuples

    Examples
    --------

    .. code-block::python

        ffmpegio.audio.detect('audio.mp3', 'silence')

    .. _silencedetect: https://ffmpeg.org/ffmpeg-filters.html#silencedetect

    """

    all_detectors = {
        "silence": analyze.SilenceDetect,
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
