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
    InputSourceDict,
)
from collections.abc import Sequence

from fractions import Fraction
import re, logging

logger = logging.getLogger("ffmpegio")

from io import IOBase

from namedpipe import NPopen

from . import utils, probe
from . import filtergraph as fgb
from .filtergraph.abc import FilterGraphObject
from .filtergraph.presets import (
    merge_audio,
    filter_video_basic,
    remove_video_alpha,
    temp_video_src,
    temp_audio_src,
)
from .utils.concat import FFConcat  # for typing
from ._utils import as_multi_option, is_non_str_sequence
from .stream_spec import (
    stream_spec as compose_stream_spec,
    StreamSpecDict,
    stream_type_to_media_type,
    parse_map_option,
    map_option as compose_map_option,
)
from .errors import FFmpegioError

#################################
## module types

UrlType = Literal["input", "output"]

FFmpegOutputType = Literal["url", "fileobj", "pipe"]

FFmpegInputUrlComposite = Union[FFmpegUrlType, FFConcat, FilterGraphObject, IO, Buffer]
FFmpegOutputUrlComposite = Union[FFmpegUrlType, IO]

FFmpegInputOptionTuple = tuple[FFmpegUrlType | FilterGraphObject, dict]
FFmpegOutputOptionTuple = tuple[FFmpegUrlType, dict]


class FFmpegArgs(TypedDict):
    """FFmpeg arguments"""

    inputs: list[FFmpegInputOptionTuple]
    # list of input definitions (pairs of url and options)
    outputs: list[FFmpegOutputOptionTuple]
    # list of output definitions (pairs of url and options)
    global_options: dict  # FFmpeg global options


class RawOutputInfoDict(TypedDict):
    dst_type: FFmpegOutputType  # True if file path/url
    user_map: str | None  # user specified map option
    media_type: MediaType | None  #
    input_id: NotRequired[int | None]
    input_stream_id: NotRequired[int | None]
    media_info: NotRequired[dict[str, Any]]
    pipe: NotRequired[NPopen]


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
    :return: ffmpeg arg dict with empty 'inputs','outputs',and 'global_options' entries.
    """
    return {"inputs": [], "outputs": [], "global_options": global_options or {}}


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
    url: FFmpegUrlType | None,
    opts: dict[str, Any] | None = None,
    update: bool = False,
) -> tuple[int, FFmpegInputOptionTuple | FFmpegOutputOptionTuple]:
    """add new or modify existing url to input or output list

    :param args: ffmpeg arg dict (modified in place)
    :param type: input or output (may use None to update later)
    :param url: url of the new entry
    :param opts: FFmpeg options associated with the url, defaults to None
    :param update: True to update existing input of the same url, default to False
    :return: file index and its entry
    """

    # get current list of in/outputs
    filelist = args[f"{type}s"]
    n = len(filelist)

    # if updating, get the existing id
    file_id = (
        next((i for i in range(n) if filelist[i][0] == url), None) if update else None
    )
    if file_id is None:
        # new entry
        file_id = n
        filelist.append((url, {} if opts is None else {**opts}))
    elif opts is not None:
        # update option dict
        filelist[file_id] = (
            url,
            (
                opts
                if filelist[file_id][1] is None
                else (
                    filelist[file_id][1]
                    if opts is None
                    else {**filelist[file_id][1], **opts}
                )
            ),
        )
    return file_id, filelist[file_id]


def has_filtergraph(args: FFmpegArgs, type: MediaType) -> bool:
    """True if FFmpeg arguments specify a filter graph

    :param args: FFmpeg argument dict
    :param type: filter type
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
    args: FFmpegArgs,
    ofile: int = 0,
    input_info: list[InputSourceDict] = [],
) -> tuple[str, tuple[int, int, int] | None, Fraction | None]:
    """finalize raw video read output options

    :param args: FFmpeg arguments (will be modified)
    :param ofile: output index, defaults to 0
    :param input_info: source information of the inputs, defaults to []
    :return dtype: Numpy-style buffer data type string
    :return s: video shape tuple (height, width, nb_components)
    :return r: video framerate
    """

    options = ["r", "pix_fmt", "s"]
    fields = ["pix_fmt", "width", "height", "r_frame_rate", "avg_frame_rate"]

    def flds2opts(pix_fmt, width, height, r1, r2):
        return r1 or r2, pix_fmt, (width, height) if width and height else None

    outopts = args["outputs"][ofile][1]
    outmap = outopts["map"]
    outmap_fields = parse_map_option(outmap)
    has_simple_filter = "vf" in outopts or "filter:v" in outopts
    fill_color = outopts.get("fill_color", None)
    if fill_color is not None and "remove_alpha" not in outopts:
        outopts.pop("fill_color")

    # use the output option by default
    opt_vals = [outopts.get(o, None) for o in options]

    # get the options of the input/filtergraph output
    if "linklabel" in outmap_fields:  # mapping filtergraph output
        # must be mapped a linklabel of a filter_complex global option
        logger.warning(
            "Pre-analysis of complex filtergraphs is not currently available."
        )
        inopt_vals = [None, None, None]
        # combine all the filtergraphs only for the analysis purpose
        # fg = fgb.stack(args["global_options"]["filter_complex"])
    else:
        # insert basic video filter if specified
        build_basic_vf(args, False, ofile)

        ifile = outmap_fields["input_file_id"]

        # check the input option data
        inurl, inopts = args["inputs"][ifile]

        # get input options
        inopt_vals = [inopts.get(o, None) for o in options]

        # directly from the input url (if not forced via input options)
        if not all(inopt_vals):
            st_vals = flds2opts(
                *utils.analyze_input_stream(
                    fields,
                    outmap_fields["stream_specifier"],
                    "video",
                    inurl,
                    inopts,
                    input_info[ifile],
                )
            )
            inopt_vals = [v or s for v, s in zip(inopt_vals, st_vals)]

        if has_simple_filter:

            # create a source chain with matching spec and attach it to the af graph
            vf = temp_video_src(*inopt_vals) + outopts.get(
                "filter:v", outopts.get("vf", None)
            )

            outpad = next(vf.iter_output_pads(unlabeled_only=True), None)
            if outpad is not None:
                vf = vf >> "[out0]"
            inopt_vals = flds2opts(
                *utils.analyze_input_stream(
                    fields,
                    "0",
                    "video",
                    vf,
                    {"f": "lavfi"},
                    {"src_type": "filtergraph"},
                )
            )

    # assign the values to individual variables
    r, pix_fmt, s = opt_vals
    r_in, pix_fmt_in, s_in = inopt_vals

    # pixel format must be specified
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
        else:
            _, ncomp, dtype, remove_alpha = utils.get_pixel_config(pix_fmt_in, pix_fmt)
            if remove_alpha:
                # append the remove-video-alpha filter chain
                build_basic_vf(args, True, ofile)

    outopts["f"] = "rawvideo"

    # use output option value or else use the input value
    r = r or r_in
    s = s or s_in

    return dtype, None if s is None else (*s[::-1], ncomp), r


def check_alpha_change(args, dir=None, ifile=0, ofile=0):
    # check removal of alpha channel
    inopts = args["inputs"][ifile][1]
    outopts = args["outputs"][ofile][1]
    if inopts is None or outopts is None:
        return None if dir is None else False  # indeterminable
    return utils.alpha_change(inopts.get("pix_fmt", None), outopts.get("pix_fmt", None))


def build_basic_vf(
    args: FFmpegArgs, remove_alpha: bool | None = None, ofile: int = 0
) -> bool:
    """convert basic VF options to vf option

    :param args: FFmpeg dict (may be modified if vf is added/changed)
    :param remove_alpha: True to add overlay filter to add a background color, defaults to None
    :                    This argument would be ignored if `'remove_alpha'` key is defined in `'args'`.
    :param ofile: output file id, defaults to 0
    :return: True if vf option is added or changed
    """

    # get output opts, nothing to do if no option set
    outopts = args["outputs"][ofile][1]

    # extract the options
    fopts = {
        name: outopts.pop(name, None)
        for name in ("crop", "flip", "transpose", "square_pixels")
    }
    fill_color, remove_alpha = (
        outopts.pop(name, defval)
        for name, defval in zip(("fill_color", "remove_alpha"), (None, remove_alpha))
    )
    if fill_color is not None:
        remove_alpha = True

    # if `s` output option contains negative number, use scale filter
    scale = outopts.get("s", None)
    if scale is not None:
        try:
            # if given a string -s option value
            m = re.match(r"(-?\d+)x(-?\d+)", scale)
            scale = (int(m[1]), int(m[2]))
        except:
            pass

        if len(scale) != 2 or scale[0] <= 0 or scale[1] <= 0:
            # must use scale filter, move the option from output to filter
            outopts.pop("s")
            fopts["scale"] = scale

    basic = any(fopts.values())
    if not (basic or remove_alpha):
        return False  # no filter needed

    # existing simple filter
    vf = outopts.pop("filter:v", outopts.pop("vf", None)) or fgb.Chain()

    if basic:
        vf = vf + filter_video_basic(**fopts)  # Graph is remove alpha else Chain

    if remove_alpha:
        if fill_color is None:
            logger.warning(
                "`fill_color` option not specified, uses white background color by default."
            )
            fill_color = "white"
        vf = vf + remove_video_alpha(fill_color)

    outopts["vf"] = vf

    return True


def finalize_audio_read_opts(
    args: FFmpegArgs,
    ofile: int = 0,
    input_info: list[InputSourceDict] = [],
) -> tuple[str, tuple[int] | None, int | None]:
    """finalize a raw output audio stream

    :param args: FFmpeg arguments. The option dict in args['outputs'][ofile][1] may be modified.
    :param ofile: output file index, defaults to 0
    :param input_info: list of input information, defaults to None
    :return dtype: input data type (Numpy style)
    :return ac: number of channels
    :return ar: sampling rate

    * Possible Output Options Modification
      - "f" and "c:a" - raw audio format and codec will always be set
      - "sample_fmt" - planar format to non-planar equivalent format or 'dbl' if format is unknown
      -

    * args['outputs'][ofile]['map'] is a valid mapping str (not a list of str)
    * If complex filtergraph(s) is used, args['global_options']['filter_complex'] must be a list of fgb.Graph objects

    """

    options = ["ar", "sample_fmt", "ac"]
    fields = ["sample_rate", "sample_fmt", "channels"]

    outopts = args["outputs"][ofile][1]
    outmap = outopts["map"]
    outmap_fields = parse_map_option(outmap)

    # use the output options by default
    opt_vals = [outopts.get(o, None) for o in options]
    if not all(opt_vals):
        if "linklabel" in outmap_fields:  # mapping filtergraph output
            # must be mapped a linklabel of a filter_complex global option
            logger.warning(
                "Pre-analysis of complex filtergraphs is not currently available."
            )
            st_vals = [None, None, None]
            # combine all the filtergraphs only for the analysis purpose
            # fg = fgb.stack(args["global_options"]["filter_complex"])
        else:
            ifile = outmap_fields["input_file_id"]

            # get input option values
            inurl, inopts = args["inputs"][ifile]
            inopt_vals = [inopts.get(o, None) for o in options]

            # fill the still missing values directly from the input url
            if not all(inopt_vals):
                st_vals = utils.analyze_input_stream(
                    fields,
                    outmap_fields["stream_specifier"],
                    "audio",
                    inurl,
                    inopts,
                    input_info[ifile],
                )
                inopt_vals = [v or s for v, s in zip(inopt_vals, st_vals)]

            # if a simple filter is present, use the stream specs of its output
            if "af" in outopts or "filter:a" in outopts:

                # create a source chain with matching specs and attach it to the af graph
                af = temp_audio_src(*inopt_vals)

                af = af + outopts.get("filter:a", outopts.get("af", None))
                inopt_vals = utils.analyze_input_stream(
                    fields,
                    "0",
                    "audio",
                    af,
                    {"f": "lavfi"},
                    {"src_type": "filtergraph"},
                )

            opt_vals = [v or s for v, s in zip(opt_vals, inopt_vals)]

    # assign the values to individual variables
    ar, sample_fmt, ac = opt_vals

    # sample format must be specified
    if sample_fmt is None:
        logger.warning(
            'Sample format of audio stream "%s" could not be retrieved. Uses "dbl".',
            outmap,
        )
        sample_fmt = outopts["sample_fmt"] = "dbl"
    elif sample_fmt[-1] == "p":
        # planar format is not supported
        logger.warning(
            "The audio stream %s uses a planar sample format '%s' which is not supported for audio data IO. Changed to %s.",
            outmap,
            sample_fmt,
            sample_fmt[:-1],
        )
        sample_fmt = sample_fmt[:-1]
        outopts["sample_fmt"] = sample_fmt  # set the format to non-planar

    # set output format and codec
    outopts["c:a"], outopts["f"] = utils.get_audio_codec(sample_fmt)

    # sample_fmt must be given
    dtype, _ = utils.get_audio_format(sample_fmt, ac)

    return dtype, ac and (ac,), ar


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

        outopts["map"] = map


def resolve_raw_output_streams(
    args: FFmpegArgs, input_info: list[InputSourceDict], streams: Sequence[str]
) -> dict[str:RawOutputInfoDict]:
    """resolve the raw output streams from given sequence of map options

    :param args: FFmpeg argument dict
    :param input_info: FFmpeg inputs' additional information, its length must match that of `args['inputs']`
    :param streams: a sequence of map options defining the streams
    :return: output information keyed by a unique map option string
    """

    dst_type = "pipe"

    # parse all mapping option values
    input_file_id = 0 if len(input_info) == 1 else None
    map_options = [
        {"stream_specifier": {}, **opt}
        for opt in (
            parse_map_option(spec, parse_stream=True, input_file_id=input_file_id)
            for spec in streams
        )
    ]

    # check if stream specifiers single out mapping one input stream per output
    if all(
        (opt["stream_specifier"].get("stream_type", None) or "") in "avV"
        and "index" in opt["stream_specifier"]
        for opt in map_options
    ):
        # no need to run the stream mapping analysis
        return {
            compose_map_option(**opt): {
                "dst_type": dst_type,
                "user_map": spec,
                "media_type": stream_type_to_media_type(
                    opt["stream_specifier"].get("stream_type", None)
                ),
                "input_file_id": opt["input_file_id"],
                "input_stream_id": None,
            }
            for spec, opt in zip(streams, map_options)
        }
    else:
        # resolve all the output streams

        # if any linklabel given, analyze the filter_complex global option values
        fg_map: dict[str, MediaType] = (
            analyze_fg_outputs(args)
            if any("linklabel" in opt for opt in map_options)
            else {}
        )

        # given as an FFmpeg map option, convert to the dict format
        inputs = args["inputs"]
        stream_info = {}  # one stream per item, value: map spec & media_type
        for spec, map_option in zip(streams, map_options):

            # filtergraph output
            media_type = fg_map.get(spec, None)

            if media_type is None:
                # input url
                file_index = map_option["input_file_id"]
                info = input_info[file_index]
                stream_spec = map_option["stream_specifier"]
                stream_data = retrieve_input_stream_ids(
                    info, *inputs[file_index], stream_spec=stream_spec
                )
                unique_stream = len(stream_data) == 1
                for stream_index, media_type in stream_data:
                    stream_info[
                        (spec if unique_stream else f"{file_index}:{stream_index}")
                    ] = {
                        "dst_type": dst_type,
                        "user_map": spec,
                        "media_type": media_type,
                        "input_file_id": file_index,
                        "input_stream_id": stream_index,
                    }
            else:
                # filtergraph output
                stream_info[spec] = {
                    "dst_type": dst_type,
                    "user_map": spec,
                    "media_type": media_type,
                    "input_file_id": None,
                    "input_stream_id": None,
                }

        return stream_info


def auto_map(
    args: FFmpegArgs, input_info: list[InputSourceDict]
) -> dict[str, RawOutputInfoDict]:
    """list all available streams from all FFmpeg input sources

    :param args: FFmpeg argument dict. `filter_complex` argument may be modified.
    :param input_info: a list of input data source information
    :return: a map of input/filtergraph output labels and their stream information.

    Mapping Input Streams vs. Complex Filtergraph Outputs
    -----------------------------------------------------

    If `filter_complex` global option is defined in `args`, `auto_map()` returns the mapping
    of all the output pads of the complex filtergraphs'. Otherwise, all the audio and video
    streams of the input urls are mapped.

    """

    gopts = args.get("global_options", None) or {}
    if "filter_complex" in gopts:
        return {
            linklabel: {"dst_type": "pipe", "user_map": None, "media_type": media_type}
            for linklabel, media_type in analyze_fg_outputs(args).items()
        }

    counter = {"file": None, "audio": 0, "video": 0}

    def next_map_option(i, media_type):
        if i != counter["file"]:
            counter["audio"] = counter["video"] = 0
            counter["file"] = i
        j = counter[media_type]
        counter[media_type] = j + 1
        return f"{i}:{media_type[0]}:{j}"

    # if no filtergraph, get all video & audio streams from all the input urls
    return {
        next_map_option(i, media_type): {
            "dst_type": "pipe",
            "user_map": None,
            "media_type": media_type,
            "input_file_id": i,
            "input_stream_id": j,
        }
        for i, ((url, opts), info) in enumerate(zip(args["inputs"], input_info))
        for j, media_type in retrieve_input_stream_ids(info, url, opts or {})
    }


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
            opts = inopts_default

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
            except TypeError as e:
                raise TypeError("Given input URL argument is not supported.") from e
            else:
                input_info = {"src_type": "buffer", "buffer": buffer}
                url = None

        url_opts, input_info_list[i] = (url, opts), input_info

        # leave the URL None if data needs to be piped in
        add_url(args, "input", *url_opts)

    return input_info_list


def process_raw_outputs(
    args: FFmpegArgs,
    input_info: list[InputSourceDict],
    streams: Sequence[str] | dict[str, dict[str, Any] | None] | None,
    options: dict[str, Any],
) -> list[RawOutputInfoDict]:
    """analyze and process piped raw outputs

    :param args: FFmpeg argument dict, A new item in`args['outputs']` is
                 appended for each piped output. Output URLs are left `None`.
    :param input_info: list of input information (same length as `args['inputs'])
    :param streams: user's list of map options to be included
    :param options: default output options
    :return: list of output information
    """

    # resolve requested output streams
    stream_info: dict[str, RawOutputInfoDict] = (
        auto_map(args, input_info)  # automatically map all the streams
        if streams is None or len(streams) == 0
        else resolve_raw_output_streams(args, input_info, streams)
    )

    # add outputs to FFmpeg arguments
    get_opts = isinstance(streams, dict)
    for spec, info in stream_info.items():
        opts = (
            {**options, **streams[info["user_map"]], "map": spec}
            if get_opts
            else {**options, "map": spec}
        )
        add_url(args, "output", None, opts)

    # finalize each output streams and identify the output formats
    for i, (_, info) in enumerate(stream_info.items()):
        # append media_info key to the output info dict
        info["media_info"] = (
            finalize_audio_read_opts
            if info["media_type"] == "audio"
            else finalize_video_read_opts
        )(args, i, input_info)

    return list(stream_info.values())


def process_raw_inputs(
    args: FFmpegArgs,
    input_opts: list[dict],
    inopts_default: dict[str, Any],
) -> list[InputSourceDict]:
    # add input streams
    input_info = []
    for opts in input_opts:
        add_url(args, "input", None, {**inopts_default, **opts})
        input_info.append(
            {"src_type": "pipe", "media_type": "audio" if "ar" in opts else "video"}
        )

    return input_info


def assign_input_url(args: FFmpegArgs, ifile: int, url: str):
    """assign a new url to an FFmpeg input

    :param args: FFmpeg arguments (args['inputs'][ifile] to be modified)
    :param ifile: file index
    :param url: new url
    """
    args["inputs"][ifile] = (url, args["inputs"][ifile][1])


def assign_output_url(args: FFmpegArgs, ofile: int, url: str):
    """assign a new url to an FFmpeg output

    :param args: FFmpeg arguments (args['outputs'][ofile] to be modified)
    :param ofile: file index
    :param url: new url
    """
    args["outputs"][ofile] = (url, args["outputs"][ofile][1])


def retrieve_input_stream_ids(
    info: InputSourceDict,
    url: FFmpegUrlType | FilterGraphObject | None,
    opts: dict,
    stream_spec: str | StreamSpecDict | None = None,
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

    # file/network input - process only if seekable
    # get ffprobe subprocess keywords
    url, sp_kwargs, exit_fcn = utils.set_sp_kwargs_stdin(url, info)
    if sp_kwargs is None:
        # something failed (warning logged)
        return []

    # get the stream list if ffprobe can
    try:
        stream_ids = [
            (info["index"], info["codec_type"])
            for info in probe.streams_basic(
                url,
                f=opts.get("f", None),
                sp_kwargs=sp_kwargs,
                stream_spec=(
                    compose_stream_spec(**stream_spec)
                    if isinstance(stream_spec, dict)
                    else stream_spec
                ),
            )
            if info["codec_type"] in get_args(MediaType)
        ]
    except:
        # if failed, return empty
        logger.warning("ffprobe failed.")
        stream_ids = []
    finally:
        # clean-up
        exit_fcn()
    return stream_ids


def finalize_media_read_opts(args: FFmpegArgs, out_idx: int) -> tuple[MediaType, tuple]:
    """finalize an output pipe for reading an audio/video stream

    :param args: FFmpeg argument dict
    :param out_idx: output url index to finalize
    :return: a tuple of the media type of the output stream and its data blob info
    """

    inputs = args["inputs"]
    out_url, out_opts = args["outputs"][out_idx]
    gopts = args["global_options"] or {}

    # resolve the stream mapping
    if "map" in out_opts:
        map = out_opts["map"]
        if not isinstance(map, str):
            if len(map) != 1:
                raise ValueError(
                    "Too many stream maps (or none listed). Only one map option is supported."
                )
            map = map[0]

        # parse the map option value
        map_d = parse_map_option(map, 0 if len(inputs) == 1 else None)
    elif len(inputs) != 1:
        raise ValueError(
            "Too many files. Only one input file is supported per reader stream."
        )
    else:
        map_d = {"input_id": 0}

    # resolve the stream media type: 'audio' vs. 'video'
    media_type = None
    if "linklabel" in map_d:
        if "filter_complex" not in gopts:
            raise FFmpegError("`filter_complex` global option not found.")

        fgs = [
            fgb.as_filtergraph_object(fg)
            for fg in as_multi_option(
                gopts["filter_complex"], (str, fgb.Graph, fgb.Chain)
            )
        ]
        linklabel = map_d["linklabel"]

        # get the output filter for the media type
        for fg in fgs:
            try:
                pad_index = fg.get_output_pad(linklabel)
                media_type = fg[*pad_index[:-1]].get_pad_media_type(
                    "output", pad_index[-1]
                )
                # traverse back the chain to look for stream formats
                break
            except fgb.FiltergraphPadNotFoundError:
                pass
    else:
        # get the input stream media type
        input_id = map_d["input_id"]
        in_url, in_opts = inputs[input_id]
        stream_spec = map_d.get("stream_specifier", None)
        st_info = probe.streams_basic(in_url, stream_spec=stream_spec)
        if len(st_info) != 1:
            raise ValueError(
                "Too many streams (or none found). Only one input stream is supported."
            )
        media_type = st_info[0]["codec_type"]
        if in_opts is None:
            in_opts = {}

        media_info = (
            finalize_audio_read_opts(args, out_idx, input_id, stream_spec)
            if media_type == "audio"
            else finalize_video_read_opts(args, out_idx, input_id, stream_spec)
        )

    return media_type, media_info


def init_media_read(
    urls: FFmpegInputUrlComposite,
    map: Sequence[str] | dict[str, dict[str, Any] | None] | None,
    options: dict[str, Any],
) -> tuple[FFmpegArgs, list[InputSourceDict], list[RawOutputInfoDict]]:
    """Initialize FFmpeg arguments for media read

    :param *urls: URLs of the media files to read.
    :param map: output stream mappings:
                - `None` to include all input streams OR all filtergraph outputs
                - a sequence of str to specify stream specifiers with file id's
                - a dict with stream specifier keys to specify output options
    :param **options: FFmpeg options, append '_in[input_url_id]' for input option names for specific
                        input url or '_in' to be applied to all inputs. The url-specific option gets the
                        preference (see :doc:`options` for custom options)
    :return: frame/sampling rates and raw data for each requested stream

    Note: Only pass in multiple urls to implement complex filtergraph. It's significantly faster to run
          `ffmpegio.video.read()` for each url.

    Specify the streams to return by `map` output option:

        map = ['0:v:0','1:a:3'] # pick 1st file's 1st video stream and 2nd file's 4th audio stream

    Unlike :py:mod:`video` and :py:mod:`image`, video pixel formats are not autodetected. If output
    'pix_fmt' option is not explicitly set, 'rgb24' is used.

    For audio streams, if 'sample_fmt' output option is not specified, 's16'.
    """

    ninputs = len(urls)
    if not ninputs:
        raise ValueError("At least one URL must be given.")

    if "n" in options:
        raise ValueError("Cannot have an `n` option set to output to named pipes.")

    # separate the options
    inopts_default = utils.pop_extra_options(options, "_in")

    # create a new FFmpeg dict
    args = empty(utils.pop_global_options(options))
    gopts = args["global_options"]  # global options dict
    gopts["y"] = None

    if "filter_complex" in gopts:
        gopts["filter_complex"] = utils.as_multi_option(
            gopts["filter_complex"], (str, FilterGraphObject)
        )

    # analyze and assign inputs
    input_info = process_url_inputs(args, urls, inopts_default)

    # analyze and assign outputs
    output_info = process_raw_outputs(args, input_info, map, options)

    return args, input_info, output_info


def init_media_write(
    url: FFmpegOutputUrlComposite,
    input_opts: list[dict],
    merge_audio_streams: bool | Sequence[int],
    merge_audio_ar: int | None,
    merge_audio_sample_fmt: str | None,
    merge_audio_outpad: str | None,
    extra_inputs: Sequence[str | tuple[str, dict]] | None,
    options: dict[str, Any],
) -> tuple[FFmpegArgs, list[NPopen], bytes | None]:
    """write multiple streams to a url/file

    :param url: output url
    :param input_opts: list of input option dict must include `'ar'` (audio) or `'r'` (video) to specify the rate.
    :param merge_audio_streams: True to combine all input audio streams as a single multi-channel stream. Specify a list of the input stream id's
                                (indices of `stream_types`) to combine only specified streams.
    :param merge_audio_ar: Sampling rate of the merged audio stream in samples/second, defaults to None to use the sampling rate of the first merging stream
    :param merge_audio_sample_fmt: Sample format of the merged audio stream, defaults to None to use the sample format of the first merging stream
    :param extra_inputs: list of additional input sources, defaults to None. Each source may be url
                         string or a pair of a url string and an option dict.
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

    # analyze input stream_data
    n_in = len(input_opts)

    # separate the input options from the rest of the options
    default_in_opts = utils.pop_extra_options(options, "_in")

    # create FFmpeg argument dict
    ffmpeg_args = empty()

    # add input streams
    pipes = []  # named pipes and their data blobs (one for each input stream)
    n_ain = 0
    for opts in input_opts:
        pipe = NPopen("w", bufsize=0)
        add_url(ffmpeg_args, "input", pipe.path, {**default_in_opts, **opts})
        pipes.append(pipe)
        if "ar" in opts:
            n_ain += 1

    # map all input streams to output unless user specifies the mapping
    map = options["map"] if "map" in options else list(range(n_in))
    do_merge = bool(merge_audio_streams) and n_ain > 1
    if do_merge:
        if merge_audio_streams is True:
            # if True, convert to stream indices of audio inputs
            merge_audio_streams = [
                i for i, opts in enumerate(input_opts) if "ar" in opts
            ]
        else:
            try:
                assert all("ar" in input_opts[i] for i in merge_audio_streams)
            except AssertionError:
                raise ValueError(
                    "merge_audio_streams argument must be bool or a sequence of indices of input audio streams."
                )

        # assign the final map - exclude audio streams if to be merged together
        options["map"] = [i for i in map if i not in merge_audio_streams]

    # add output url and options (may also contain possibly global options)
    add_url(ffmpeg_args, "output", url, options)

    # add extra input arguments if given
    if extra_inputs is not None:
        add_urls(ffmpeg_args, "input", extra_inputs)

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
        add_filtergraph(ffmpeg_args, afilt)

    return ffmpeg_args, pipes
