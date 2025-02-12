from __future__ import annotations

from ._typing import (
    Literal,
    get_args,
    Any,
    MediaType,
    FFmpegUrlType,
    Union,
    NotRequired,
    TypedDict,
    IO,
    Buffer,
)
from collections.abc import Sequence

from fractions import Fraction

import re, logging

logger = logging.getLogger("ffmpegio")

from io import IOBase
from . import utils
from . import filtergraph as fgb
from .filtergraph.abc import FilterGraphObject
from .utils.concat import FFConcat  # for typing
from ._utils import as_multi_option, is_non_str_sequence

#################################
## module types

UrlType = Literal["input", "output"]
FFmpegInputType = Literal["url", "filtergraph", "buffer", "fileobj"]
FFmpegOutputType = Literal["url", "fileobj"]

FFmpegInputUrlComposite = Union[FFmpegUrlType, FFConcat, FilterGraphObject, IO, Buffer]
FFmpegOutputUrlComposite = Union[FFmpegUrlType, IO]

FFmpegInputOptionTuple = tuple[FFmpegUrlType | FilterGraphObject, dict | None]
FFmpegOutputOptionTuple = tuple[FFmpegUrlType, dict | None]


class FFmpegArgs(TypedDict):
    """FFmpeg arguments"""

    inputs: list[FFmpegInputOptionTuple]
    # list of input definitions (pairs of url and options)
    outputs: list[FFmpegOutputOptionTuple]
    # list of output definitions (pairs of url and options)
    global_options: NotRequired[dict | None]  # FFmpeg global options


class InputSourceDict(TypedDict):
    """input source info"""

    src_type: FFmpegInputType  # True if file path/url
    buffer: NotRequired[bytes]  # index of the source index
    fileobj: NotRequired[IO]  # file object
    pipe: NotRequired[NPopen]  # pipe


#################################
## module functions


def array_to_video_input(
    rate: int | float | Fraction | None = None,
    data: Any | None = None,
    pipe_id: str | None = None,
    **opts,
) -> tuple[str, dict]:
    """create an stdin input with video stream

    :param rate: input frame rate in frames/second
    :param data: input video frame data, accessed with `video_info` plugin hook, defaults to None (manual config)
    :param pipe_id: named pipe path, defaults None to use stdin
    :param **opts: input options
    :return: tuple of input url and option dict
    """

    if rate is None and "r" not in opts:
        raise ValueError("rate argument must be specified if opts['r'] is not given.")

    return (
        pipe_id or "-",
        {**utils.array_to_video_options(data), f"r": rate, **opts},
    )


def array_to_audio_input(
    rate: int | None = None,
    data: Any | None = None,
    pipe_id: str | None = None,
    **opts: dict[str, Any],
):
    """create an stdin input with audio stream

    :param rate: input sample rate in samples/second
    :param data: input audio data, accessed by `audio_info` plugin hook, defaults to None (manual config)
    :param pipe_id: input named pipe id, defaults to None to use the stdin
    :return: tuple of input url and option dict
    """

    if rate is None and "ar" not in opts:
        raise ValueError("rate argument must be specified if opts['ar'] is not given.")

    return (
        pipe_id or "-",
        {**utils.array_to_audio_options(data), f"ar": rate, **opts},
    )


def empty(global_options: dict = None) -> FFmpegArgs:
    """create empty ffmpeg arg dict

    :param global_options: global options, defaults to None
    :return: empty ffmpeg arg dict with 'inputs','outputs',and 'global_options' entries.
    """
    return {"inputs": [], "outputs": [], "global_options": global_options}


def check_url(
    url: FFmpegInputUrlComposite,
    nodata: bool = True,
    nofileobj: bool = False,
    format: str | None = None,
    pipe_str: str | None = "-",
) -> tuple[
    FFmpegUrlType | FilterGraphObject | FFConcat, IOBase | None, memoryview | None
]:
    """Analyze url argument for non-url input

    :param url: url argument string or data or file or a custom class
    :param nodata: True to raise exception if url is a bytes-like object, default to True
    :param nofileobj: True to raise exception if url is a file-like object, default to False
    :param format: FFmpeg format option, default to None (unspecified)
    :param pipe_str: specify an alternate FFmpeg pipe url or None to leave it blank, default to '-'
    :return: url string, file object, and data object

    Custom Pipe Class
    -----------------

    `url` may be a class instance of which `str(url)` call yields a stdin pipe expression
    (i.e., '-' or 'pipe:' or 'pipe:0') with `url.input` returns the input data. For such `url`,
    `check_url()` returns url and data objects, accordingly.

    """

    def hasmethod(o, name):
        return hasattr(o, name) and callable(getattr(o, name))

    fileobj = None
    data = None

    if format != "lavfi":
        try:
            memoryview(url)
            data = url
            url = pipe_str
        except:
            if hasmethod(url, "fileno"):
                if nofileobj:
                    raise ValueError("File-like object cannot be specified as url.")
                fileobj = url
                url = pipe_str
            elif str(url) in ("-", "pipe:", "pipe:0"):
                try:  # for FFConcat
                    data = url.input
                except:
                    pass

        if nodata and data is not None:
            raise ValueError("Bytes-like object cannot be specified as url.")

    return url, fileobj, data


def add_url(
    args: FFmpegArgs,
    type: Literal["input", "output"],
    url: FFmpegUrlType,
    opts: dict[str, Any] | None = None,
    update: bool = False,
) -> tuple[int, FFmpegInputOptionTuple | FFmpegOutputOptionTuple]:
    """add new or modify existing url to input or output list

    :param args: ffmpeg arg dict (modified in place)
    :param type: input or output
    :param url: url of the new entry
    :param opts: FFmpeg options associated with the url, defaults to None
    :param update: True to update existing input of the same url, default to False
    :return: file index and its entry
    """

    type = f"{type}s"
    filelist = args.get(type, None)
    if filelist is None:
        filelist = args[type] = []
    n = len(filelist)
    id = next((i for i in range(n) if filelist[i][0] == url), None) if update else None
    if id is None:
        id = n
        filelist.append((url, opts and {**opts}))
    elif opts is not None:
        filelist[id] = (
            url,
            (
                opts and {**opts}
                if filelist[id][1] is None
                else filelist[id][1] if opts is None else {**filelist[id][1], **opts}
            ),
        )
    return id, filelist[id]


def has_filtergraph(args: FFmpegArgs, type: MediaType) -> bool:
    """True if FFmpeg arguments specify a filter graph

    :param args: FFmpeg argument dict
    :param type: filter type
    :param file_id: specify output file id (ignored if type=='complex'), defaults to None (or 0)
    :param stream_id: stream, defaults to None
    :return: True if filter graph is specified
    """
    try:
        if (
            "filter_complex" in args["global_options"]
            or "lavfi" in args["global_options"]
        ):
            return True
    except:
        pass  # no global_options defined

    # input filter
    if any(
        (
            opts is not None and opts.get("f", None) == "lavfi"
            for _, opts in args["inputs"]
        )
    ):
        return True

    # output filter
    short_opt = {"video": "vf", "audio": "af"}[type]
    other_st = {"video": "a", "audio": "v"}[type]
    re_opt = re.compile(rf"{short_opt}$|filter(?::(?=[^{other_st}]).*?)?$")
    if any(
        (any((re_opt.match(key) for key in opts.keys())) for _, opts in args["outputs"])
    ):
        return True

    return False  # no output options defined


def finalize_video_read_opts(
    args: FFmpegArgs, ofile: int = 0, ifile: int = 0, istream: str | None = None
) -> tuple[str, tuple[int, int], Fraction]:

    inurl, inopts = args["inputs"][ifile]
    if inopts is None:
        inopts = {}
    outopts = args["outputs"][ofile][1]

    pix_fmt_in = inopts.get("pix_fmt", None)
    w_in, h_in = inopts.get("s", (None, None))
    r_in = inopts.get("r", None)

    if (
        isinstance(inurl, (str, Path))
        and inopts.get("f", None) != "lavfi"
        and not (pix_fmt_in and w_in and h_in and r_in)
    ):
        # TODO: handle lavfi filter processing
        try:
            # ["pix_fmt", "width", "height", "avg_frame_rate", "r_frame_rate"]
            v_pix_fmt, v_width, v_height, vr1, vr2 = probe._video_info(
                inurl, istream, None
            )
            pix_fmt_in, w_in, h_in, r_in = (
                x or y
                for x, y in zip(
                    (pix_fmt_in, w_in, h_in, r_in),
                    (v_pix_fmt, v_width, v_height, vr1 or vr2),
                )
            )
        except:
            pass  # not probable, OK... maybe
    s_in = (w_in, h_in) if w_in and h_in else None

    if outopts is None:
        outopts = {}
        args["outputs"][ofile] = (args["outputs"][ofile][0], outopts)

    # pixel format must be specified
    pix_fmt = outopts.get("pix_fmt", None)
    remove_alpha = False
    if pix_fmt is None:
        # deduce output pixel format from the input pixel format
        try:
            outopts["pix_fmt"], ncomp, dtype, _ = utils.get_pixel_config(pix_fmt_in)
        except:
            ncomp = dtype = None
    else:
        # make sure assigned pix_fmt is valid
        if pix_fmt_in is None:
            try:
                dtype, ncomp = utils.get_pixel_format(pix_fmt)
            except:
                ncomp = dtype = None
            remove_alpha = False
        else:
            _, ncomp, dtype, remove_alpha = utils.get_pixel_config(pix_fmt_in, pix_fmt)

    # set up basic video filter if specified
    build_basic_vf(args, remove_alpha, ofile)

    outopts["f"] = "rawvideo"

    # if no filter and video shape and rate are known, all known
    r = s = None
    if not has_filtergraph(args, "video") and ncomp is not None:
        r = outopts.get("r", r_in)
        s = outopts.get("s", s_in)
        if s is not None:
            if isinstance(s, str):
                m = re.match(r"(\d+)x(\d+)", s)
                s = [int(m[1]), int(m[2])]

    return dtype, None if s is None else (*s[::-1], ncomp), r


def check_alpha_change(args, dir=None, ifile=0, ofile=0):
    # check removal of alpha channel
    inopts = args["inputs"][ifile][1]
    outopts = args["outputs"][ofile][1]
    if inopts is None or outopts is None:
        return None if dir is None else False  # indeterminable
    return utils.alpha_change(inopts.get("pix_fmt", None), outopts.get("pix_fmt", None))


def _build_video_basic_filter(
    fill_color: str | None = None,
    remove_alpha: bool = False,
    scale: str | Sequence | None = None,
    crop: str | Sequence | None = None,
    flip: Literal["horizontal", "vertical", "both"] | None = None,
    transpose: str | Sequence | None = None,
    square_pixels: (
        Literal["upscale", "downscale", "upscale_even", "downscale_even"] | None
    ) = None,
) -> FilterGraphObject:
    bg_color = fill_color or "white"

    vfilters = (
        fgb.Graph(
            f"color=c={bg_color}[l1];[l1][in]scale2ref[l2],[l2]overlay=shortest=1"
        )
        if remove_alpha
        else fgb.Chain()
    )

    if square_pixels == "upscale":
        vfilters += "scale='max(iw,ih*dar)':'max(iw/dar,ih)':eval=init,setsar=1/1"
    elif square_pixels == "downscale":
        vfilters += "scale='min(iw,ih*dar)':'min(iw/dar,ih)':eval=init,setsar=1/1"
    elif square_pixels == "upscale_even":
        vfilters += "scale='trunc(max(iw,ih*dar)/2)*2':'trunc(max(iw/dar,ih)/2)*2':eval=init,setsar=1/1"
    elif square_pixels == "downscale_even":
        vfilters += "scale='trunc(min(iw,ih*dar)/2)*2':'trunc(min(iw/dar,ih)/2)*2':eval=init,setsar=1/1"
    elif square_pixels is not None:
        raise ValueError(f"unknown `square_pixels` option value given: {square_pixels}")

    if crop:
        try:
            assert not isinstance(crop, str)
            vfilters += fgb.Filter("crop", *crop)
        except:
            vfilters += fgb.Filter("crop", crop)

    if flip:
        try:
            ftype = ("", "horizontal", "vertical", "both").index(flip)
        except:
            raise Exception("Invalid flip filter specified.")
        if ftype % 2:
            vfilters += "hflip"
        if ftype >= 2:
            vfilters += "vflip"

    if transpose is not None:
        try:
            assert not isinstance(transpose, str)
            vfilters += fgb.Filter("transpose", *transpose)
        except:
            vfilters += fgb.Filter("transpose", transpose)

    if scale:
        try:
            scale = [int(s) for s in scale.split("x")]
        except:
            pass
        try:
            assert not isinstance(scale, str)
            vfilters += fgb.Filter("scale", *scale)
        except:
            vfilters += fgb.Filter("scale", scale)

    return vfilters


def build_basic_vf(args, remove_alpha=None, ofile=0):
    """convert basic VF options to vf option

    :param args: FFmpeg dict
    :type args: dict
    :param remove_alpha: True to add overlay filter to add a background color, defaults to None
    :                    This argument would be ignored if `'remove_alpha'` key is defined in `'args'`.
    :type remove_alpha: bool, optional
    :param ofile: output file id, defaults to 0
    :type ofile: int, optional
    """

    # get output opts, nothing to do if no option set
    outopts = args["outputs"][ofile][1]
    if outopts is None:
        return

    # extract the options
    fopts = {
        name: outopts.pop(name)
        for name in (
            "fill_color",
            "crop",
            "flip",
            "transpose",
            "square_pixels",
            "remove_alpha",
        )
        if name in outopts
    }

    # check if output needs to be scaled
    scale = outopts.get("s", None)
    do_scale = scale is not None
    if do_scale:
        try:
            m = re.match(r"(\d+)x(\d+)", scale)
            scale = (int(m[1]), int(m[2]))
        except:
            pass
        try:
            do_scale = len(scale) > 2 or (scale[0] <= 0 or scale[1] <= 0)
        except:
            do_scale = False

    nfo = len(fopts)
    if (nfo and (nfo > 1 or "fill_color" not in fopts)) or remove_alpha or do_scale:
        if do_scale:
            fopts["scale"] = scale
            del outopts["s"]

        if remove_alpha and "remove_alpha" not in fopts:
            fopts["remove_alpha"] = True

        bvf = _build_video_basic_filter(**fopts)  # Graph is remove alpha else Chain
        vf = outopts.get("vf", None)
        if vf:
            try:
                outopts["vf"] = vf + bvf
            except Exception as e:
                raise FFmpegioError(
                    f"Cannot append the basic video filter to the user specified video filter (vf):\n  {e}"
                )
        else:
            outopts["vf"] = bvf


def finalize_audio_read_opts(
    args: FFmpegArgs, ofile: int = 0, ifile: int = 0, istream: str | None = None
) -> tuple[str, int, int]:

    inurl, inopts = args["inputs"][ifile]
    if inopts is None:
        inopts = {}
    outopts = args["outputs"][ofile][1]
    has_filter = has_filtergraph(args, "audio")

    sample_fmt_in = inopts.get("sample_fmt", None)
    ac_in = inopts.get("ac", None)
    ar_in = inopts.get("ar", None)
    if isinstance(inurl, (str, Path)) and not (sample_fmt_in and ac_in and ar_in):
        # TODO: handle lavfi input
        try:
            ar_in, sample_fmt_in, ac_in = (
                x or y
                for x, y in zip(
                    (ar_in, sample_fmt_in, ac_in),
                    probe._audio_info(inurl, istream, None),
                )
            )
        except:
            pass

    if outopts is None:
        outopts = {}
        args["outputs"][ofile] = (args["outputs"][ofile][0], outopts)

    # pixel format must be specified
    sample_fmt = outopts.get("sample_fmt", None)
    if sample_fmt is None:
        # get pixel format from input
        sample_fmt = sample_fmt_in
        if sample_fmt:
            if sample_fmt[-1] == "p":
                # planar format is not supported
                sample_fmt = sample_fmt[:-1]
            outopts["sample_fmt"] = sample_fmt  # set the format

    # set output format and codec
    outopts["c:a"], outopts["f"] = utils.get_audio_codec(sample_fmt)

    ac = ar = None
    if not has_filter:
        ac = outopts.get("ac", ac_in)
        ar = outopts.get("ar", ar_in)

    # sample_fmt must be given
    dtype, shape = utils.get_audio_format(sample_fmt, ac)

    return dtype, ac, ar


################################################################################


def get_option(ffmpeg_args, type, name, file_id=0, stream_type=None, stream_id=None):
    """get ffmpeg option value from ffmpeg args dict

    :param ffmpeg_args: ffmpeg args dict
    :type ffmpeg_args: dict
    :param type: option type: 'video', 'audio', or 'global'
    :type type: str
    :param name: option name w/out stream specifier
    :type name: str
    :param file_id: index of target file, defaults to 0
    :type file_id: int, optional
    :param stream_type: target stream type: 'v' or 'a', defaults to None
    :type stream_type: str, optional
    :param stream_id: target stream index (within specified stream type), defaults to None
    :type stream_id: int, optional
    :return: option value
    :rtype: various

    If stream is specified, several option names are looked up till one is defined. For example,
    3 entries are checked for `name`='c', `stream_type`='v', and `stream_id`=0 in this order:
    "c:v:0", "c:v", then "c". Function returns the first hit.

    """
    if ffmpeg_args is None:
        return None
    names = [name]
    if type.startswith("global"):
        opts = ffmpeg_args.get("global_options", None)
    else:
        filelists = ffmpeg_args.get(f"{type}s", None)
        if filelists is None:
            return None
        entry = filelists[file_id]
        if entry is None:
            return None
        opts = entry[1]
        if stream_type is not None:
            name += f":{stream_type}"
            names.append(name)
            if stream_id is not None:
                name += f":{stream_id}"
                names.append(name)
    if opts is None:
        return None

    v = None
    while v is None and len(names):
        name = names.pop()
        v = opts.get(name, None)

    return v


def merge_user_options(ffmpeg_args, type, user_options, file_index=None):
    if type == "global":
        type = "global_options"
        opts = ffmpeg_args.get(type, None)
        if opts is None:
            opts = ffmpeg_args[type] = {**user_options}
        else:
            ffmpeg_args[type] = {**opts, **user_options}
    else:
        type += "s"
        filelist = ffmpeg_args.get(type, None)
        if file_index is None:
            file_index = 0
        if filelist is None or len(filelist) <= file_index:
            raise Exception(f"{type} list does not have file #{file_index}")
        url, opts = ffmpeg_args[type][file_index]
        ffmpeg_args[type][file_index] = (
            url,
            {**user_options} if opts is None else {**opts, **user_options},
        )

    return ffmpeg_args


def get_video_array_format(ffmpeg_args, type, file_id=0):
    try:
        opts = ffmpeg_args[f"{type}s"][file_id][1]
    except:
        raise ValueError(f"{type} file #{file_id} is not specified")
    try:
        dtype, ncomp = utils.get_pixel_format(opts["pix_fmt"])
        shape = [*opts["s"][::-1], ncomp]
    except:
        raise ValueError(f"{type} options must specify both `s` and `pix_fmt` options")

    return shape, dtype


def move_global_options(args):
    """move global options from the output options dicts

    :param args: FFmpeg arguments
    :type args: dict
    :returns: FFmpeg arguments (the same object as the input)
    :rtype: dict
    """

    from .caps import options

    _global_options = options("global", name_only=True)

    global_options = args.get("global_options", None) or {}

    # global options may be given as output options
    for _, inopts in args.get("inputs", ()):
        if inopts:
            for k in (*(k for k in inopts.keys() if k in _global_options),):
                global_options[k] = inopts.pop(k)
    for _, outopts in args.get("outputs", ()):
        if outopts:
            for k in (*(k for k in outopts.keys() if k in _global_options),):
                global_options[k] = outopts.pop(k)
    if len(global_options):
        args["global_options"] = global_options

    return args


def clear_loglevel(args):
    """clear global loglevel option

    :param args: FFmpeg argument dict
    :type args: dict


    """
    try:
        del args["global_options"]["loglevel"]
        logger.warn("loglevel option is cleared by ffmpegio")
    except:
        pass


def finalize_avi_read_opts(args):
    """finalize multiple-input media reader setup

    :param args: FFmpeg dict
    :type args: dict
    :return: use_ya flag - True to expect grayscale+alpha pixel format rather than grayscale
    :rtype: bool

    - assumes options dict of the first output is already present
    - insert `pix_fmt='rgb24'` and `sample_fmt='sa16le'` options if these options are not assigned
    - check for the use of both 'gray16le' and 'ya8', and returns True if need to use 'ya8'
    - set f=avi and vcodec=rawvideo
    - set acodecs according to sample_fmts

    """

    # get output options, create new
    options = args["outputs"][0][1]

    # check to make sure all pixel and sample formats are supported
    gray16le = ya8 = 0
    for k in utils.find_stream_options(options, "pix_fmt"):
        v = options[k]
        if v in ("gray16le", "grayf32le"):
            gray16le += 1
        elif v in ("ya8", "ya16le"):
            ya8 += 1
    if gray16le and ya8:
        raise ValueError(
            "pix_fmts: grayscale with and without transparency cannot be mixed."
        )

    # if pix_fmt and sample_fmt not specified, set defaults
    # user can conditionally override these by stream-specific option
    if "pix_fmt" not in options:
        options["pix_fmt"] = "rgb24"
    if "sample_fmt" not in options:
        options["sample_fmt"] = "s16"

    # add output formats and codecs
    options["f"] = "avi"
    options["c:v"] = "rawvideo"

    # add audio codec
    for k in utils.find_stream_options(options, "sample_fmt"):
        options[f"c:a" + k[10:]] = utils.get_audio_codec(options[k])[0]

    return ya8 > 0


def config_input_fg(expr, args, kwargs):
    """configure input filtergraph

    :param expr: filtergraph expression
    :type expr: str
    :param args: input argument sequence, all arguments are intended to be
                 used with the filter. Errors if expr yields a multi-filter
                 filtergraph.
    :type args: seq
    :param kwargs: input keyword argument dict. Only keys matching the
                   filter's options are consumed. The rest are returned.
    :type kwargs: dict
    :return: original expression or a Filter object, duration in seconds if
             known and finite, and unprocessed kwarg items.
    :rtype: (str|Filter,float|None,dict)
    """
    fg = fgb.Graph(expr)
    dopt = None  # duration option

    if len(fg) != 1 or len(fg[0]) != 1:
        # multi-filter input filtergraph, cannot take arguments
        if len(args):
            raise FFmpegioError(
                f"filtergraph input expresion cannot take ordered options."
            )
        return expr, dopt, kwargs

    # single-filter graph, can apply its options given in the arguments
    f = fg[0][0]
    info = f.info
    if info.inputs is None or len(info.inputs) > 0:
        raise FFmpegioError(f"{f.name} filter is not a source filter")

    # get the full list of filter options
    opts = set()  #
    for o in info.options:
        if not dopt and o.name == "duration":
            dopt = (o.name, o.aliases, o.default)
        opts.add(o.name)
        opts.update(o.aliases)

    # split filter named option andn other keyword arguments
    fargs = {i: v for i, v in enumerate(args)}
    oargs = {}
    for k, v in kwargs.items():
        (fargs if k in opts else oargs)[k] = v

    if dopt is not None:
        name, aliases, default = dopt
        val = fargs.get(name, None)
        if val is None:
            for a in aliases:
                val = fargs.get(a, None)
                if val is not None:
                    break
        if val is None:
            val = default

        dopt = utils.parse_time_duration(val)
        if dopt <= 0:
            dopt = None  # infinite

    return f.apply(fargs), dopt, oargs


def add_urls(
    ffmpeg_args: dict,
    url_type: UrlType,
    urls: str | tuple[str, dict | None] | Sequence[str | tuple[str, dict | None]],
    *,
    update: bool = False,
) -> list[tuple[int, FFmpegInputOptionTuple | FFmpegOutputOptionTuple]]:
    """add one or more urls to the input or output list at once

    :param args: ffmpeg arg dict (modified in place)
    :param url_type: input or output
    :param urls: a sequence of urls (and optional dict of their options)
    :param opts: FFmpeg options associated with the url, defaults to None
    :param update: True to update existing input of the same url, default to False
    :return: list of file indices and their entries
    """

    def process_one(url):
        return (
            add_url(ffmpeg_args, url_type, url, update=update)
            if isinstance(url, str)
            else (
                add_url(ffmpeg_args, url_type, *url, update=update)
                if (
                    isinstance(url, tuple)
                    and len(url) == 2
                    and isinstance(url[0], str)
                    and isinstance(url[1], (dict, type(None)))
                )
                else None
            )
        )

    ret = process_one(urls)
    return [process_one(url) for url in urls] if ret is None else [ret]


def add_filtergraph(
    args: FFmpegArgs,
    filtergraph: fgb.Graph,
    map: Sequence[str] | None = None,
    automap: bool = True,
    append_filter: bool = True,
    append_map: bool = True,
    ofile: int = 0,
):
    """add a complex filtergraph to FFmpeg arguments

    :param args: FFmpeg argument dict (to be modified in place)
    :param filtergraph: Filtergraph to be added to the FFmpeg arguments
    :param map: output stream mapping, usually the output pad label, defaults to None
    :param automap: True to auto map all the output pads of the filtergraph IF `map` is None, defaults to True.
    :param append_filter: True to append `filtergraph` to the `filter_complex` global option if exists, False to replace, defaults to True
    :param append_map: True to append `map` to the `map` output option if exists, False to replace, defaults to True
    :param ofile: output file id, defaults to 0

    """

    if len(args["outputs"]) <= ofile:
        raise ValueError(
            f"The specified output #{ofile} is not defined in the FFmpegArgs dict."
        )

    if automap and map is None:
        map = [f"[{l[0]}]" for l in filtergraph.iter_output_labels()]

    # add the merging filter graph to the filter_complex argument
    gopts = args.get("global_options", None)

    if append_filter:
        complex_filters = None if gopts is None else gopts.get("filter_complex", None)
        if complex_filters is None:
            complex_filters = filtergraph
        else:
            complex_filters = as_multi_option(
                complex_filters, (str, fgb.Graph, fgb.Chain)
            )
            complex_filters.append(filtergraph)
    else:
        complex_filters = filtergraph

    if gopts is None:
        args["global_options"] = {"filter_complex": complex_filters}
    else:
        gopts["filter_complex"] = complex_filters

    if not len(map):
        # nothing to map
        return

    outopts = args["outputs"][ofile][1]
    if outopts is None:
        args["outputs"][ofile] = (args["outputs"][ofile][0], {"map": map})
    else:
        if append_map and "map" in outopts:

            existing_map = outopts["map"]

            # remove merged streams from output map & append the output stream of the filter
            map = (
                [*existing_map, *map]
                if is_non_str_sequence(existing_map)
                else [existing_map, *map]
            )



def auto_map(
    args: FFmpegArgs, input_info: Sequence[InputSourceDict]
) -> dict[str, MediaType | None]:
    """list all available streams from all FFmpeg input sources

    :param args: FFmpeg argument dict. `filter_complex` argument may be modified.
    :param input_info: a list of input data source information
    :return: a map of input/filtergraph output labels to their media types.

    Mapping Input Streams vs. Complex Filtergraph Outputs
    -----------------------------------------------------

    If `filter_complex` global option is defined in `args`, `auto_map()` returns the mapping
    of all the output pads of the complex filtergraphs'. Otherwise, all the audio and video
    streams of the input urls are mapped.

    """

    gopts = args.get("global_options", None) or {}
    if "filter_complex" in gopts:
        map = analyze_fg_outputs(args)
    else:
        # if no filtergraph, get all video & audio streams from all the input urls
        map = {
            f"{i}:{j}": media_type
            for i, ((url, opts), info) in enumerate(zip(args["inputs"], input_info))
            for j, media_type in retrieve_input_stream_ids(info, url, opts or {})
        }

    return map


def analyze_fg_outputs(args: FFmpegArgs) -> dict[str, MediaType | None]:
    """list all available output labels of the complex filtergraphs

    :param args: FFmpeg argument dict. `filter_complex` argument may be modified if present.
    :return: a map of filtergraph output labels to their media types

    Possible Complex Filtergraph Modification
    -----------------------------------------

    To enable auto-mapping, all the output pads must be labeled. Thus, if the complex filtergraphs
    in the `filter_complex` global option have any unlabeled output, they are automatically
    labeled as `outN` where N is a number starting from `0`. If a label has alraedy been assigned
    to another output pad, that label will be skipped.
    """

    gopts = args.get("global_options", None) or {}

    if "filter_complex" not in gopts:
        # no filtergraph
        return {}

    # make sure it's a list of filtergraphs
    filters_complex = utils.as_multi_option(
        gopts["filter_complex"], (str, FilterGraphObject)
    )

    # make sure all are FilterGraphObjects
    filters_complex = [fgb.as_filtergraph_object(fg) for fg in filters_complex]

    # check for unlabeled outputs and log existing output labels
    out_indices = set()
    out_labels = {}
    out_unlabeled = False
    for fg in filters_complex:
        for idx, filter, _ in fg.iter_output_pads(full_pad_index=True):
            label = fg.get_label(outpad=idx)
            if label is None:
                out_unlabeled = True
            elif m := re.match(r"out(\d+)$", label):
                out_indices.add(int(m[1]))
                out_labels[label] = (filter, idx)

    # remove all the output pads connected to an input pad of another filtergraph
    if len(filters_complex) > 1:
        for fg in filters_complex:
            for label, _ in fg.iter_input_labels():
                if label in out_labels:
                    out_labels.pop(label)

    # if there are unlabeled outputs, label them all
    if out_unlabeled:
        out_n = next(i for i in range(len(out_labels) + 1) if i not in out_labels)
        for i, fg in enumerate(filters_complex):
            new_labels = []
            for idx, filter, _ in fg.iter_output_pads(
                unlabeled_only=True, full_pad_index=True
            ):
                label = f"out{out_n}"
                out_labels[label] = (filter, idx)
                new_labels.append({"label": label, "outpad": idx})

                # next index
                while True:
                    out_n += 1
                    if out_n not in out_labels:
                        break

            for kwargs in new_labels:
                fg = fg.add_label(**kwargs)
            filters_complex[i] = fg

    # create the output map
    map = {
        f"[{label}]": filter.get_pad_media_type("output", pad_id)
        for label, (filter, pad_id) in out_labels.items()
    }

    # update the filtergraphs
    args["global_options"]["filter_complex"] = filters_complex

    return map


def process_url_inputs(
    args: FFmpegArgs,
    urls: list[FFmpegInputUrlComposite | tuple[FFmpegInputUrlComposite, dict]],
    inopts_default: dict[str, Any],
) -> list[InputSourceDict]:
    """analyze and process heterogeneous input url argument

    :param args: FFmpeg argument dict, `args['inputs']` receives all the new inputs.
                 If input is a buffer, a fileobj, or an FFconcat, the first element 
                 of the FFmpeg inputs entry is set to 'None', to be replaced by
                 a pipe expression.
    :param urls: list of input urls/data or a pair of input url and its options
    :param inopts_default: default input options
    :return: list of input information
    """

    input_info_list = [None] * len(urls)
    for i, url in enumerate(urls):  # add inputs
        # get the option dict
        if utils.is_non_str_sequence(url, (str, FilterGraphObject, Buffer)):
            if len(url) != 2:
                raise ValueError(
                    "url-options pair input must be a tuple of the length 2."
                )
            url, opts = url
            opts = inopts_default if opts is None else {**inopts_default, **opts}
        else:
            # only URL given
            url, opts = url, inopts_default

        # check url (must be url and not fileobj)
        is_fg = isinstance(url, FilterGraphObject)
        if is_fg or ("lavfi" == opts.get("f", None) and isinstance(url, str)):
            if is_fg:
                if "f" not in opts:
                    opts["f"] = "lavfi"
                elif opts["f"] != "lavfi":
                    raise ValueError(
                        "input filtergraph must use the `'lavfi'` input format."
                    )

            input_info = {"src_type": "filtergraph"}

        elif utils.is_fileobj(url, readable=True):
            input_info = {"src_type": "fileobj", "fileobj": url}
            url = None
        elif utils.is_url(url, pipe_ok=False):
            input_info = {"src_type": "url"}
        elif isinstance(url, FFConcat):
            # convert to buffer
            input_info = {"src_type": "buffer", "buffer": FFConcat.input}
            url = None
        else:
            try:
                buffer = memoryview(url)
            except:
                raise ValueError("Given input URL argument is not supported.")
            else:
                input_info = {"src_type": "buffer", "buffer": buffer}
                url = None

        url_opts, input_info_list[i] = (url, opts), input_info

        # leave the URL None if data needs to be piped in
        add_url(args, "input", *url_opts)

    return input_info_list


def retrieve_input_stream_ids(
    info: InputSourceDict,
    url: FFmpegUrlType | FilterGraphObject | None,
    opts: dict,
    stream_spec: str | None = None,
) -> list[tuple[int, MediaType]]:
    """Retrieve ids and media types of streams in an input source

    :param info: input file source information
    :param url: URL or local file path of the input media file/device. None if data is provided via pipe
                and data is in the `info` argument
    :param opts: FFmpeg input options
    :param stream_spec: Specify streams to return
    :return: A list of indices and media types of the input streams.
             Maybe empty if failed to probe the media (e.g., data inaccessible
             or in an ffprobe incompatible format, e.g., ffconcat)
    """

    src_type = info["src_type"]
    if src_type == "filtergraph":
        return utils.analyze_input_filtergraph_ids(url)

    # file/network input - process only if seekable
    sp_kwargs = {}  # ffprobe subprocess keywords
    if src_type != "url":
        url = "pipe:0"
        if src_type == "buffer":
            sp_kwargs["input"] = info["buffer"]
        elif src_type == "fileobj":
            f = info["fileobj"]
            if not (f.readable() and f.seekable()):
                logger.warning("file object must be seekable.")
                return []
            pos = f.tell()
            sp_kwargs["stdin"] = f
        else:
            logger.warning("unknown input source type.")
            return []

    # get the stream list if ffprobe can
    try:
        stream_ids = [
            (info["index"], info["codec_type"])
            for info in probe.streams_basic(
                url,
                f=opts.get("f", None),
                sp_kwargs=sp_kwargs,
                stream_spec=stream_spec,
            )
            if info["codec_type"] in get_args(MediaType)
        ]
    except:
        # if failed, return empty
        logger.warning("ffprobe failed.")
        stream_ids = []

    if src_type == "fileobj":
        # restore the read cursor position
        f.seek(pos)
    return stream_ids
