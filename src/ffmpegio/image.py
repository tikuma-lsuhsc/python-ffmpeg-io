from . import ffmpegprocess, utils, configure, FFmpegError, plugins
from .utils import log as log_utils

__all__ = ["create", "read", "write", "filter"]


def _run_read(*args, show_log=None, sp_kwargs=None, **kwargs):
    """run FFmpeg and retrieve audio stream data
    :param *args ffmpegprocess.run arguments
    :type *args: tuple
    :param shape: output frame size if known, defaults to None
    :type shape: (int, int), optional
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
    :return: image data, created by `bytes_to_video` plugin hook
    :rtype: object
    """

    outopts = args[0]["outputs"][0][1]
    outopts["map"] = "0:v:0"
    dtype, shape, _ = configure.finalize_video_read_opts(
        args[0],
        input_info=[
            {"src_type": "filtergraph" if outopts.get("f", None) == "lavfi" else "url"}
        ],
    )

    if sp_kwargs is not None:
        kwargs = {**sp_kwargs, **kwargs}

    if shape is None:
        configure.clear_loglevel(args[0])

        out = ffmpegprocess.run(*args, capture_log=True, **kwargs)
        if show_log:
            print(out.stderr)
        if out.returncode:
            raise FFmpegError(out.stderr)

        info = log_utils.extract_output_stream(out.stderr)
        dtype, shape = utils.get_video_format(info["pix_fmt"], info["s"])
    else:
        out = ffmpegprocess.run(
            *args,
            capture_log=None if show_log else True,
            **kwargs,
        )
        if out.returncode:
            raise FFmpegError(out.stderr, show_log)

    nbytes = utils.get_samplesize(shape, dtype)

    return plugins.get_hook().bytes_to_video(
        b=out.stdout[-nbytes:], dtype=dtype, shape=shape, squeeze=True
    )


def create(expr, *args, show_log=None, sp_kwargs=None, **options):
    """Create an image using a source video filter

    :param name: name of the source filter
    :type name: str
    :param \\*args: sequential filter option arguments. Only valid for
                    a single-filter expr, and they will overwrite the
                    options set by expr.
    :type \\*args: seq, optional
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
    :return: image data, created by `bytes_to_video` plugin hook
    :rtype: object

    .. seealso::
        See https://ffmpeg.org/ffmpeg-filters.html#Video-Sources for
        available video source filters

    """

    input_options = utils.pop_extra_options(options, "_in")
    output_options = utils.pop_extra_options(options, "_out")

    url, _, options = configure.config_input_fg(expr, args, options)

    options = {**options, **output_options, "frames:v": 1}

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, {**input_options, "f": "lavfi"})
    configure.add_url(
        ffmpeg_args, "output", "-", {"pix_fmt": "rgb24", **options, "f": "rawvideo"}
    )
    # TODO: filtergraph scanning will remove the default 'pix_fmt' setting

    return _run_read(ffmpeg_args, show_log=show_log, sp_kwargs=sp_kwargs)


def read(url, show_log=None, sp_kwargs=None, **options):
    """Read an image file or a snapshot of a video frame

    :param url: URL of the image or video file to read.
    :type url: str
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
    :return: image data, created by `bytes_to_video` plugin hook
    :rtype: object

    Note on \\**options: To specify the video frame capture time, use `time`
    option which is an alias of `start` standard option.
    """

    input_options = utils.pop_extra_options(options, "_in")

    # get url/file stream
    url, stdin, input = configure.check_url(
        url, False, format=input_options.get("f", None)
    )

    ffmpeg_args = configure.empty()
    configure.add_url(ffmpeg_args, "input", url, input_options)
    outopts = configure.add_url(ffmpeg_args, "output", "-", options)[1][1]
    outopts["f"] = "rawvideo"
    if "frames:v" not in outopts:
        outopts["frames:v"] = 1

    # override user specified stdin and input if given
    sp_kwargs = {**sp_kwargs} if sp_kwargs else {}
    sp_kwargs["stdin"] = stdin
    sp_kwargs["input"] = input

    return _run_read(ffmpeg_args, show_log=show_log, sp_kwargs=sp_kwargs)


def write(
    url,
    data,
    overwrite=None,
    show_log=None,
    extra_inputs=None,
    sp_kwargs=None,
    **options
):
    """Write a NumPy array to an image file.

    :param url: URL of the image file to write.
    :type url: str
    :param data: image data, accessed by `video_info()` and `video_bytes()` plugin hooks
    :type data: object
    :param overwrite: True to overwrite if output url exists, defaults to None
                      (auto-select)
    :type overwrite: bool, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param extra_inputs: list of additional input sources, defaults to None. Each source may be url
                         string or a pair of a url string and an option dict.
    :type extra_inputs: seq(str|(str,dict))
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional
    """

    url, stdout, _ = configure.check_url(url, True)

    input_options = utils.pop_extra_options(options, "_in")

    ffmpeg_args = configure.empty()
    configure.add_url(
        ffmpeg_args,
        "input",
        *configure.array_to_video_input(1, data=data, **input_options),
    )

    # add extra input arguments if given
    if extra_inputs is not None:
        configure.add_urls(ffmpeg_args, "input", extra_inputs)

    outopts = configure.add_url(ffmpeg_args, "output", url, options)[1][1]
    outopts["frames:v"] = 1

    configure.build_basic_vf(ffmpeg_args, configure.check_alpha_change(ffmpeg_args, -1))

    kwargs = {**sp_kwargs} if sp_kwargs else {}
    kwargs.update(
        {
            "input": plugins.get_hook().video_bytes(obj=data),
            "stdout": stdout,
            "overwrite": overwrite,
        }
    )
    kwargs["capture_log"] = None if show_log else False

    out = ffmpegprocess.run(ffmpeg_args, **kwargs)
    if out.returncode:
        raise FFmpegError(out.stderr, show_log)


def filter(expr, input, show_log=None, sp_kwargs=None, **options):
    """Filter image pixels.

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str, None
    :param input: input image data, accessed by `video_info` and `video_bytes` plugin hooks
    :type input: object
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
    :type show_log: bool, optional
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or
                      `subprocess.Popen()` call used to run the FFmpeg, defaults
                      to None
    :type sp_kwargs: dict, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional
    :return: output sampling rate and data, created by `bytes_to_video` plugin hook
    :rtype: (int, object)

    """

    input_options = utils.pop_extra_options(options, "_in")

    ffmpeg_args = configure.empty()
    configure.add_url(
        ffmpeg_args,
        "input",
        *configure.array_to_video_input(1, data=input, **input_options),
    )
    outopts = configure.add_url(ffmpeg_args, "output", "-", options)[1][1]
    outopts["f"] = "rawvideo"
    if expr:
        outopts["filter:v"] = expr

    return _run_read(
        ffmpeg_args,
        input=plugins.get_hook().video_bytes(obj=input),
        show_log=show_log,
        sp_kwargs=sp_kwargs,
    )
