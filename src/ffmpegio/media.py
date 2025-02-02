from __future__ import annotations

from typing_extensions import Unpack
from collections.abc import Sequence
from .utils.typing import Literal, Any, RawStreamDef, ProgressCallable

import contextlib
from io import BytesIO
from fractions import Fraction

from namedpipe import NPopen

from .threading import WriterThread
from .filtergraph.presets import merge_audio

from . import ffmpegprocess, utils, configure, FFmpegError, plugins
from .utils import avi
from .threading import WriterThread

__all__ = ["read", "write"]


def read(*urls, progress=None, show_log=None, **options):
    """Read video and audio frames

    :param *urls: URLs of the media files to read.
    :type *urls: tuple(str)
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                     defaults to None (no show/capture)
                     Ignored if stream format must be retrieved automatically.
    :type show_log: bool, optional
    :param use_ya: True if piped video streams uses `ya8` pix_fmt instead of `gray16le`, default to None
    :type use_ya: bool, optional
    :param \\**options: FFmpeg options, append '_in[input_url_id]' for input option names for specific
                        input url or '_in' to be applied to all inputs. The url-specific option gets the
                        preference (see :doc:`options` for custom options)
    :type \\**options: dict, optional

    :return: frame rate and video frame data, created by `bytes_to_video` plugin hook
    :rtype: (`fractions.Fraction`, object)

    Note: Only pass in multiple urls to implement complex filtergraph. It's significantly faster to run
          `ffmpegio.video.read()` for each url.


    Unlike :py:mod:`video` and :py:mod:`image`, video pixel formats are not autodetected. If output
    'pix_fmt' option is not explicitly set, 'rgb24' is used.

    For audio streams, if 'sample_fmt' output option is not specified, 's16'.


    streams = ['0:v:0','1:a:3'] # pick 1st file's 1st video stream and 2nd file's 4th audio stream

    """

    ninputs = len(urls)
    if not ninputs:
        raise ValueError("At least one URL must be given.")

    # separate the options
    spec_inopts = utils.pop_extra_options_multi(options, r"_in(\d+)$")
    inopts = utils.pop_extra_options(options, "_in")

    # create a new FFmpeg dict
    args = configure.empty()
    configure.add_url(args, "output", "-", options)  # add piped output
    for i, url in enumerate(urls):  # add inputs
        opts = {**inopts, **spec_inopts.get(i, {})}
        # check url (must be url and not fileobj)
        configure.check_url(
            url, nodata=True, nofileobj=True, format=opts.get("f", None)
        )
        configure.add_url(args, "input", url, opts)

    # configure output options
    use_ya = configure.finalize_media_read_opts(args)

    # run FFmpeg
    out = ffmpegprocess.run(
        args,
        progress=progress,
        capture_log=None if show_log else True,
    )
    if out.returncode:
        raise FFmpegError(out.stderr, show_log)

    # fire up the AVI reader and process the stdout bytes
    # TODO: Convert to use pipe/thread
    reader = avi.AviReader()
    reader.start(BytesIO(out.stdout), use_ya)
    # get frame rates and sample rates of all media streams
    rates = {
        v["spec"]: v["frame_rate"] if v["type"] == "v" else v["sample_rate"]
        for v in reader.streams.values()
    }
    data = {k: [] for k in reader.streams}
    for st, frame in reader:
        data[st].append(frame)

    data = {
        reader.streams[k]["spec"]: reader.from_bytes(k, b"".join(v))
        for k, v in data.items()
    }

    return rates, data


def write(
    url: str,
    stream_types: Sequence[Literal["a", "v"]],
    *stream_args: * tuple[RawStreamDef, ...],
    merge_audio_streams: bool | Sequence[int] = False,
    merge_audio_ar: int | None = None,
    merge_audio_sample_fmt: str | None = None,
    merge_audio_outpad: str | None = None,
    progress: ProgressCallable | None = None,
    overwrite: bool | None = None,
    show_log: bool | None = None,
    extra_inputs: Sequence[str | tuple[str, dict]] | None = None,
    sp_kwargs: dict | None = None,
    **options: Unpack[dict[str, Any]],
):
    """write multiple streams to a url/file

    :param url: output url
    :stream_types: list/string of input stream media types, each element is either 'a' (audio) or 'v' (video)
    :param stream_args: raw input stream data arguments, each input stream is either a tuple of a sample rate (audio) or frame rate (video) followed by a data blob
                         or a tuple of a data blob and a dict of input options. The option dict must include `'ar'` (audio) or `'r'` (video) to specify the rate.
    :param merge_audio_streams: True to combine all input audio streams as a single multi-channel stream. Specify a list of the input stream id's
                                (indices of `stream_types`) to combine only specified streams.
    :param merge_audio_ar: Sampling rate of the merged audio stream in samples/second, defaults to None to use the sampling rate of the first merging stream
    :param merge_audio_sample_fmt: Sample format of the merged audio stream, defaults to None to use the sample format of the first merging stream
    :param progress: progress callback function, defaults to None
    :param overwrite: True to overwrite if output url exists, defaults to None (auto-select)
    :param show_log: True to show FFmpeg log messages on the console, defaults to None (no show/capture)
    :param extra_inputs: list of additional input sources, defaults to None. Each source may be url
                         string or a pair of a url string and an option dict.
    :param sp_kwargs: dictionary with keywords passed to `subprocess.run()` or `subprocess.Popen()` call
                      used to run the FFmpeg, defaults to None
    :param **options: FFmpeg options, append '_in' for input option names (see :doc:`options`). Input options
                      will be applied to all input streams unless the option has been already defined in `stream_data`

    TIPS
    ----

    * All the input streams will be added to the output file by default, unless `map` option is specified
    * If the input streams are of different durations, use `shortest=ffmpegio.FLAG` option to trim all streams to the shortest.
    * Using merge_audio_streams:
      - adds a `filter_complex` global option
      - merged input streams are removed from the `map` option and replaced by the merged stream

    """

    if not all(t in "av" for t in stream_types):
        raise ValueError("Elements of stream_types input must either 'a' or 'v'.")

    # analyze input stream_data
    n_in = len(stream_types)

    if len(stream_args) != n_in:
        raise ValueError(f"Lengths of `stream_args` and `stream_types` not matching.")

    # separate the input options from the rest of the options
    default_in_opts = utils.pop_extra_options(options, "_in")

    url, stdout, _ = configure.check_url(url, True)

    # create FFmpeg argument dict
    ffmpeg_args = configure.empty()

    # add input streams
    pipes = []  # named pipes and their data blobs (one for each input stream)

    for mtype, arg in zip(stream_types, stream_args):

        try:
            a1, a2 = arg
            if isinstance(a1, (int, float, Fraction)):
                opts, data = a1, a2
            else:
                assert isinstance(a2, dict)
                data, opts = a1, a2
        except:
            raise ValueError(
                f"""Invalid raw stream definition: {arg}.\nEach item of `stream_args` must be a two-element tuple: 
                    - a rate (numeric) and a data_blob
                    - a data_blob and a dict of options
                """
            )

        pipe = NPopen("w", bufsize=0)

        if mtype == "a":  # audio
            if not isinstance(opts, dict):
                opts = {"ar": round(opts)}
            elif "ar" not in opts:
                raise ValueError(
                    "audio stream option dict missing the required 'ar' item to set the sampling rate."
                )
            in_args = configure.array_to_audio_input(
                pipe_id=pipe.path, data=data, **{**default_in_opts, **opts}
            )
            byte_data = plugins.get_hook().audio_bytes(obj=data)

        else:  # video
            if not isinstance(opts, dict):
                opts = {"r": opts}
            elif "r" not in opts:
                raise ValueError(
                    "video stream option dict missing the required 'r' item to set the frame rate."
                )
            in_args = configure.array_to_video_input(
                pipe_id=pipe.path, data=data, **{**default_in_opts, **opts}
            )
            byte_data = plugins.get_hook().video_bytes(obj=data)

        pipes.append((pipe, byte_data))

        configure.add_url(
            ffmpeg_args,
            "input",
            *in_args,
        )

    # map all input streams to output unless user specifies the mapping
    map = options["map"] if "map" in options else list(range(n_in))
    do_merge = bool(merge_audio_streams) and stream_types.count("a") > 1
    if do_merge:
        if merge_audio_streams is True:
            # if True, convert to stream indices of audio inputs
            merge_audio_streams = [
                i for i, mtype in enumerate(stream_types) if mtype == "a"
            ]
        else:
            try:
                assert all(stream_types[i] == "a" for i in merge_audio_streams)
            except AssertionError:
                raise ValueError(
                    "merge_audio_streams argument must be bool or a sequence of indices of input audio streams."
                )

        # assign the final map - exclude audio streams if to be merged together
        options["map"] = [i for i in map if i not in merge_audio_streams]

    # add output url and options (may also contain possibly global options)
    configure.add_url(ffmpeg_args, "output", url, options)

    # add extra input arguments if given
    if extra_inputs is not None:
        configure.add_urls(ffmpeg_args, "input", extra_inputs)

    if do_merge:

        # get FFmpeg input list
        ffinputs = ffmpeg_args["inputs"]
        audio_streams = {i: ffinputs[i][1] for i in merge_audio_streams}
        afilt = merge_audio(
            audio_streams,
            merge_audio_ar,
            merge_audio_sample_fmt,
            merge_audio_outpad or "aout",
        )

        # add the merging filter graph to the filter_complex argument
        configure.add_filtergraph(ffmpeg_args, afilt)

    kwargs = {**sp_kwargs} if sp_kwargs else {}
    kwargs.update(
        {
            "stdout": stdout,
            "progress": progress,
            "overwrite": overwrite,
        }
    )
    kwargs["capture_log"] = None if show_log else False

    with contextlib.ExitStack() as stack:
        # run the FFmpeg
        proc = ffmpegprocess.Popen(ffmpeg_args, **kwargs)

        # connect the pipes and queue the stream data
        for p, data in pipes:
            stack.enter_context(p)
            writer = WriterThread(p)
            stack.enter_context(writer)
            writer.write(data)  # send bytes in out_bytes to the client
            writer.write(None)  # sentinel message

        # wait for the FFmpeg to finish processing
        proc.wait()

    if proc.returncode:
        raise FFmpegError(proc.stderr, show_log)
