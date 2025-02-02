import re, os, shlex
from collections import abc

from ..filtergraph import Graph, Chain, Filter
from .. import devices

__all__ = ["parse", "compose", "FLAG"]

FLAG = None

# till v3.7 is dropped
form_shell_cmd = (
    shlex.join
    if hasattr(shlex, "join")
    else lambda args: " ".join(shlex.quote(arg) for arg in args)
)


def parse_options(args):
    """parse command-line option arguments

    :param args: argument string or sequence of arguments
    :type args: str or seq of str
    :return: parsed options. Flag options get None as their values.
    :rtype: dict
    """
    if isinstance(args, str):
        args = shlex.split(args)
    res = {}
    n = len(args)
    i = 0
    while i < n:
        key = args[i][1:]
        i += 1
        if i < n and args[i][0] != "-":
            if key not in res:
                res[key] = args[i]
            elif isinstance(res[key], str):
                res[key] = [res[key], args[i]]
            else:
                res[key].append(args[i])
            i += 1
        else:
            res[key] = FLAG
    return res


def parse(cmdline):
    """parse ffmpeg command line arguments

    :param cmdline: full or partial ffmpeg command line string
    :type cmdline: str or seq(str)
    :return: ffmpegio FFmpeg argument dict
    :rtype: dict
    """

    from ..caps import options

    if isinstance(cmdline, str):
        # remove multi-line command
        cmdline = re.sub(r"\\\n", " ", cmdline)

        # split the command line into its options
        args = shlex.split(cmdline)
    else:  # list of strs
        args = cmdline

    # exclude 'ffmpeg' command if present
    if re.search(r'(?:^|[/\\])?ffmpeg(?:.exe)?"?$', args[0], re.IGNORECASE):
        args = args[1:]

    # extract global options
    all_gopts = options("global")
    all_lopts = options("per-file")
    is_gopt = [len(s) and s[0] == "-" and s[1:] in all_gopts for s in args]
    is_lopt = [not tf for tf in is_gopt]
    gopts = {}
    for i, s in enumerate(args):
        if not is_gopt[i]:
            continue
        k = s[1:]
        if k in ("h", "?", "help", "-help"):  # special case take all args thereafter
            gopts[k] = " ".join(args[i + 1 :])
        elif all_gopts[k] is FLAG:
            gopts[k] = FLAG
        else:
            gopts[k] = args[i + 1]
            is_lopt[i + 1] = False

    args = [s for s, tf in zip(args, is_lopt) if tf]

    # identify -i options
    ipos = [i + 2 for i, v in enumerate(args) if v == "-i"]
    inputs = [
        (args[i1 - 1], parse_options(args[i0 : i1 - 2]))
        for i0, i1 in zip((0, *ipos[:-1]), ipos)
    ]
    if len(inputs):
        args = args[ipos[-1] :]  # drop all input arguments

    # identify output_urls
    opos = [
        i + 1
        for i, v in enumerate(args)
        if v[0] != "-"  # must not be an option name/flag
        and (
            i == 0  # no output options
            or args[i - 1][0] != "-"  # prev arg specifies an output option value
            or all_lopts.get(args[i - 1][1:].split(":", 1)[0], False)
            is FLAG  # prev arg is a flag
        )
    ]
    outputs = [
        (args[i1 - 1], parse_options(args[i0 : i1 - 1]))
        for i0, i1 in zip((0, *opos[:-1]), opos)
    ]

    return dict(global_options=gopts, inputs=inputs, outputs=outputs)


def compose(args, command="", shell_command=False):
    """compose ffmpeg subprocess arguments from argument dict values

    :param global_options: global options, defaults to None
    :type global_options: dict, optional
    :param inputs: list of input files and their options, defaults to []
    :type inputs: seq of seq(str, dict or None), optional
    :param outputs: list of output files and thier options, defaults to []
    :type outputs: seq of seq(str, dict or None), optional
    :param command: ffmpeg command, defaults to ""
    :type command: str, optional
    :param shell_command: True to output shell command ready string, defaults to False
    :type shell_command: bool, optional
    :returns: list of arguments (possibly missing the leading 'ffmpeg' command if `command`
              is not given) or shell command string if `shell_command` is True
    :rtype: list of str or str

    If global_options is None and only 1 output file given, FFmpeg global options
    may be specified as additional output options.
    """

    def finalize_global(key, val):
        return key, val

    def finalize_output(key, val):
        if re.match(r"s(?:\:|$)", key) and not isinstance(val, str):
            val = "x".join((str(v) for v in val))
        return key, val

    def finalize_input(key, val):
        if re.match(r"s(?:\:|$)", key) and not isinstance(val, str):
            val = "x".join((str(v) for v in val))
        return key, val

    def opts2args(opts, finalize):
        # FFmpeg applies the last of the repeated options regardless of overall/per-stream
        # need to parse per-stream options and group repeated options together and
        # apply the overall option first

        opts_parsed = {}
        for itm in opts.items():
            k, v = finalize(*itm)
            oname, *sspec = k.split(":", 1)
            o = opts_parsed.get(oname, None)
            if o is None:
                opts_parsed[oname] = o = {}
            o[sspec[0] if len(sspec) else None] = v

        def set_arg(karg, val):
            if not isinstance(val, (str, Graph, Chain, Filter)) and isinstance(
                val, abc.Sequence
            ):
                for v in val:
                    args.extend([karg, str(v)])
            else:
                args.append(karg)
                if val is not FLAG:
                    args.append(str(val))

        args = []
        for key, vals in opts_parsed.items():
            kbase = f"-{key}"
            if None in vals:
                val = val = vals.pop(None)
                set_arg(kbase, val)
            for st, val in vals.items():
                set_arg(f"{kbase}:{st}", val)

        return args

    def inputs2args(inputs):
        args = []
        for url, opts in inputs:
            # resolve url enumeration if it's a device
            url, opts = devices.resolve_source(url, opts)

            if opts:
                args.extend(opts2args(opts, finalize_input))
            args.extend(
                [
                    "-i",
                    str(url) if url is not None else os.devnull,
                ]
            )
        return args

    def outputs2args(outputs):
        args = []
        for url, opts in outputs:
            # resolve url enumeration if it's a device
            url, opts = devices.resolve_sink(url, opts)

            if opts:
                args.extend(opts2args(opts, finalize_output))
            args.append(
                str(url)
                if url is not None
                else "/dev/null" if os.name != "nt" else "NUL"
            )
        return args

    args = [
        *([command] if command else []),
        *opts2args(args.get("global_options", None) or {}, finalize_global),
        *inputs2args(args.get("inputs", None) or ()),
        *outputs2args(args.get("outputs", None) or ()),
    ]
    return form_shell_cmd(args) if shell_command else args
