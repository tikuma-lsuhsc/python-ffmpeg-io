from fractions import Fraction
import re, itertools
from collections.abc import Sequence

# Filter string parser/composer
# For FilterGraph class, see ../filtergraph.py

# various regexp objects used in the module
_re_name_id = re.compile(r"\s*([a-zA-Z0-9_]+)(?:\s*@\s*([a-zA-Z0-9_]+))?\s*(?:=|$)")
_re_labels = re.compile(r"\s*\[\s*(.+?)\s*\]")
_re_graph = re.compile(r"(?<!\\)(?:\\\\)*('|;|,|\[)|$")
_re_esc2 = re.compile(r"([\\\'\[\];,])")
_re_quote = re.compile(r"(?<!\\)(?:\\\\)*\'")
_re_args = re.compile(r"\s*(?<!\\)(?:\\\\)*\:\s*")
_re_esc = re.compile(r"\\(.)")
_re_args_kw = re.compile(r"\s*([a-zA-Z0-9_]+)\s*=\s*(.+)\s*")


def parse_filter_args(expr):
    """parse filter argument string

    :param expr: filter argument string
    :type expr: str
    :return: list of argument strings; last element may be a dict of key-value pairs
    :rtype: list of str + dict
    """

    def conv_val(s):
        # convert a numeric option value
        try:
            return int(s)
        except:
            try:
                return float(s)
            except:
                try:
                    return Fraction(s)
                except:
                    return s

    # remove escaped single quotes
    arg_iter = (s for s in _re_quote.split(expr))
    all_args = [""]

    # separate options
    in_quote = False
    while True:
        s = next(arg_iter, None)

        if not in_quote and any((c in s for c in ",;[]")):
            raise ValueError("filter specification includes reserved characters ',;[]'")

        if s is None:
            break
        ss = _re_args.split(s)

        all_args[-1] += ss[0]
        all_args.extend(_re_esc.sub(r"\1", a) for a in ss[1:])
        s = next(arg_iter, None)
        if s is None:
            break
        all_args[-1] += s

        in_quote = not in_quote

    # identify the first named option position
    ikw = next(
        (i for i, arg in enumerate(all_args) if _re_args_kw.match(arg)),
        None,
    )

    # gather ordered options
    args = [conv_val(s.rstrip()) for s in (all_args if ikw is None else all_args[:ikw])]

    if ikw is not None:
        # if named options are given, form a dict
        def get_kw(arg):
            m = _re_args_kw.match(arg)
            return m[1], conv_val(m[2].rstrip())

        kwargs = {k: v for k, v in (get_kw(arg) for arg in all_args[ikw:])}
        args = [*args, kwargs]

    return args


def compose_filter_args(*args):
    """compose once-escaped filter argument string

    :param *args: list of argument strings; last element may be a dict of key-value pairs
    :type *args: list of str + dict
    :return: filter argument string
    :rtype: str
    """

    def finalize_option_value(value):
        # flatten a sequence and add escaping to ' and \
        # A first level escaping affects the content of each filter option value, which may contain
        # the special character : used to separate values, or one of the escaping characters \'.
        if not isinstance(value, str) and isinstance(value, Sequence):
            value = "|".join(str(value))
        elif isinstance(value, bool):
            value = str(value).lower()  # true|false
        else:
            value = str(value)

        # escape special characters
        s = re.sub(r"([':\\,])", r"\\\1", value)

        # use quote if value start or ends with space
        m = re.match(r"^(\s+)", s)
        if m:
            i = m.end()
            "\\" + "\\".join(*s[:i]) + s[i:]
        m = re.match(r"(\s+)$", s)
        if m:
            i = m.start()
            s = s[i:] + "\\" + "\\".join(*s[i:])

        return s

    kwargs = args[-1] if len(args) > 0 and isinstance(args[-1], dict) else None
    if kwargs is not None:
        args = args[:-1]

    args = ":".join([finalize_option_value(i) for i in args])
    if kwargs:
        kwargs = ":".join(
            [f"{k}={finalize_option_value(v)}" for k, v in kwargs.items()]
        )
        args = ":".join([args, kwargs]) if args else kwargs
    return args


###################################################################################################


def parse_filter(expr):
    """Parse FFmpeg filter expression

    :param expr: filter expression, escaped special characters once
    :type expr: str
    :return: filter name followed by arguments, followed by a dict containing id string
             (empty if id not given)
    :rtype: tuple(str, *args, {['id':str]})
    """

    m = _re_name_id.match(expr, 0)

    if not m:
        raise ValueError(
            f'"{expr}" does not start with a valid filter name or not terminated "=" character.'
        )

    name, id = m.groups()
    s_args = expr[m.end() :]

    try:
        args = parse_filter_args(s_args) if s_args else []
    except:
        raise ValueError(f'"{expr}" is not a valid filter expression.')

    return (((name, id) if id else name), *args)


def compose_filter(name, *args):
    """Compose FFmpeg filter expression

    :param name: filter name, optionally seq of name & id
    :type name: str or (str, str)
    :param args: option value sequence
    :type args: seq of stringifyable items + last item may be a dict to hold
                key-value pairs
    :return: filter expression, once escaped
    :rtype: str
    """

    expr = name if isinstance(name, str) else f"{name[0]}@{name[1]}"

    if len(args):
        expr = f"{expr}={compose_filter_args(*args)}"

    return expr


###################################################################################################

# FILTERGRAPH PARSER/COMPOSER


def parse_graph(expr):
    """parse filter graph expression

    :param expr: twice-escaped filter graph string
    :type expr: str
    :return: tuple of unescaped filter graph blob, input labels, output labels, chain links, and sws_flags list
    :rtype: (list of list of (name, args, id), dict, dict, dict, list)
    :return: tuple of unescaped filter graph blob, pad link map, and sws_flags list
    :rtype: (list of list of (name, args, id), dict, list)

    Note
    ----

    - the items of link map dict specifies the link name
      - key: the link label str (no brackets)
      - value: 2-element list: [dst, src]
        - dst: 3-int tuple filter input pad specifier: (chain_id, filter_id, pad_id)
        - src: 3-int tuple filter output pad specifier: (chain_id, filter_id, pad_id)
    - exceptions:
      - if key is input stream specifier str
        - dst: filter input pad specifiers tuples or a list of filter input pad specifiers tuples
        - src: None
      - if key is filtergraph output stream label str
        - dst: None
        - src: filter output pad specifier tuple

    """

    links = {}

    def add_pad(label, output, *padspec):
        sig = links.get(label, None)
        if sig is None:
            # new label
            links[label] = [None, padspec] if output else [padspec, None]
        else:
            # existing label
            padspecs = sig[output]
            if padspecs is None:
                sig[output] = padspec
            elif not output and sig[1] is None: # new input label with the same name as existing input label
                if isinstance(sig[output][0], int):
                    # second matching input label
                    sig[output] = [padspecs, padspec]
                else:
                    # more matching labels
                    padspecs.append(padspec)
            else:
                raise ValueError(
                    f'Filter graph specifies multiple \'{label}\' {"output" if output else "input"} pads.'
                )

    def parse_labels(expr, i, output, *cidfid):
        m = _re_labels.match(expr, i)
        p = 0
        while m:
            add_pad(m[1], output, *cidfid, p)
            i = m.end()
            p += 1
            m = _re_labels.match(expr, i)

        return i

    n = len(expr)

    # get scale flags if given
    m = re.match(r"\s*sws_flags=(.+?);", expr)
    if m:
        sws_flags = parse_filter_args(m[1])
        i = m.end()
    else:
        sws_flags = None
        i = 0  # string position

    fg = []
    fc = []
    cid = 0  # current chain id
    fid = 0  # current filter id in chain <cid>
    fs = ""
    while i < n:
        m = _re_graph.search(expr, i)
        ch = m[1]
        j = m.end()

        s = re.sub(r"\\(.)", r"\1", expr[i : j - 1] if ch else expr[i:])
        if s and not s.isspace():
            fs += s

        if ch == "[":  # input/output labels
            i = parse_labels(expr, j - 1, bool(fs), cid, fid)  # grab all labels
            if i == n:
                # add new filter to the chain
                fc.append(parse_filter(fs))

                # if new chain, add it to the graph
                if not fid:
                    fg.append(fc)

        else:
            i = j

            if ch == "'":
                # add quoted text to fs unchanged
                j = expr.find("'", i) + 1
                if j <= 0:
                    raise ValueError(
                        "a quote in the filter graph string not terminated properly"
                    )
                fs += expr[i - 1 : j]
                i = j

            else:

                # add new filter to the chain
                fc.append(parse_filter(fs))

                # if new chain, add it to the graph
                if not fid:
                    fg.append(fc)

                # update the id's for the next filter element
                if ch == ";":
                    cid += 1
                    fc = []
                    fid = 0
                else:
                    fid += 1
                fs = ""

    return (fg, links, sws_flags)


def compose_graph(filter_specs, links=None, sws_flags=None):
    """Compose complex filter graph
    :param filter_specs: a nested sequence of argument sequences to compose_filter() to define
               a filter graph. The last element of each filter argument sequence
               may be a dict, defining its keyword arguments.
    :type filter_specs: seq(seq(filter_args))
    :param links: specifies how non-sequential filters are linked. See below for the specification.
    :type links: dict, optional
    :param sws_flags: specify swscale flags for those automatically inserted
                      scalers, defaults to None
    :type sws_flags: seq of stringifyable elements with optional dict as the last
                     element for the keyword flags, optional
    :returns: filter graph expression
    :rtype: str

    Note
    ----

    - the items of link map dict specifies the link name
      - key: the link label str (no brackets) or int. int key indicates internal links
      - value: 2-element list: [dst, src]
        - dst: 3-int tuple filter input pad specifier: (chain_id, filter_id, pad_id)
        - src: 3-int tuple filter output pad specifier: (chain_id, filter_id, pad_id)
    - exceptions:
      - if key is input stream specifier str
        - dst: filter input pad specifiers tuples or a list of filter input pad specifiers tuples
        - src: None
      - if key is filtergraph output stream label str
        - dst: None
        - src: filter output pad specifier tuple

    Examples
    --------

    Here is the "Multiple input overlay in 2x2 grid" example in
    https://trac.ffmpeg.org/wiki/FilteringGuide:

    ```
        [1:v]negate[a]; \\
        [2:v]hflip[b]; \\
        [3:v]edgedetect[c]; \\
        [0:v][a]hstack=inputs=2[top]; \\
        [b][c]hstack=inputs=2[bottom]; \\
        [top][bottom]vstack=inputs=2[out]
    ```

    This filtergraph can be composed by the following Python script:

    ```python
        fg = [
            [("negate",)], # chain #0
            [("hflip",)],  # chain #1
            [("edgedetect",)], # chain #2
            [("hstack", {"inputs": 2})], # chain #3
            [("hstack", {"inputs": 2})], # chain #4
            [("vstack", {"inputs": 2})], # chain #5
        ]

        links = {
            "1:v": [(0, 0, 0), None], # feeds to negate
            "2:v": [(1, 0, 0), None], # feeds to hflip
            "3:v": [(2, 0, 0), None], # feeds to edgedetect
            "0:v": [(3, 0, 0), None], # feeds to the 1st input of 1st hstack
            "out": [None, (5, 0, 0)], # feeds from vstack output
            0: [(3, 0, 1), (0, 0, 0)], # 1st hstack gets its 2nd input from negate
            1: [(4, 0, 0), (1, 0, 0)], # 2nd hstack gets its 1st input from hflip
            2: [(4, 0, 1), (2, 0, 0)], # 2nd hstack gets its 2nd input from edgedetect
            3: [(5, 0, 0), (3, 0, 0)], # vstack gets its 1st input from 1st hstack
            4: [(5, 0, 1), (4, 0, 0)], # vstack gets its 2nd input from 2nd hstack
        }

        compose(fg, links)

    ```

    Note that this filtergraph can be written with a fewer chains using
    side-injections:

    ```
        [2:v]hflip[b]; \\ 
        [1:v]negate, [0:v]hstack=inputs=2[top]; \\ 
        [3:v]edgedetect, [b]hstack=inputs=2, [top]vstack=inputs=2[out]
    ```

    This version can be composed by

    ```python
        fg = [
            [("hflip",)], # chain 0
            [("negate",), ("hstack",{"inputs": 2})], # chain 1
            [("edgedetect",), ("hstack",{"inputs": 2}), ("vstack", {"inputs": 2})], # chain 2
        ]

        links = { # input: output
            "1:v": [(1, 0, 0), None], # feeds to negate
            "2:v": [(0, 0, 0), None], # feeds to hflip
            "3:v": [(2, 0, 0), None], # feeds to edgedetect
            "0:v": [(1, 1, 0), None], # feeds to 1st input of hstack in chain 1
            "out": [None, (2, 0, 0)]
            (2, 1, 0): (0, 0, 0), # chain 0 output feeds to 1st input of hstack in chain 1
            (2, 2, 0): (1, 0, 0), # chain 1 output feeds to 1st input of vstack in chain 2
        }

        compose(fg, input_labels, output_labels, links)

    """

    def escape(expr):
        return "'".join(
            [
                si if i % 2 else _re_esc2.sub(r"\\\1", si)
                for i, si in enumerate(_re_quote.split(expr))
            ]
        )

    def define_filter(info, in_labels, out_labels):
        expr = (
            "".join([f"[{in_labels[pad]}]" for pad in sorted(in_labels)])
            if in_labels is not None
            else ""
        )

        if isinstance(info, str):
            s = info
        else:
            s = compose_filter(*info)
        expr += escape(s)
        if out_labels is not None:
            expr += "".join([f"[{out_labels[pad]}]" for pad in sorted(out_labels)])

        return expr

    def assign_link(d, label, cid, fid, pid):
        key = (cid, fid)
        l = d.get(key, None)
        if l is None:
            d[key] = {pid: label}
        elif pid is None:
            l[pid].append(label)
        else:
            l[pid] = label

    # list labels per input pad and per output pad
    in_labels = {}  # labeled input pads
    out_labels = {}  # labeled input pads
    labels = set()  # collection of all the labels

    if links is not None and len(links):

        # log all named link labels
        labels = {k for k in links.keys() if isinstance(k, str)}

        # name unnamed labels
        def set_link_label(k):
            if not isinstance(k, int):
                return k
            for j in itertools.count(k):
                label = f"L{j}"
                if label not in labels:
                    labels.add(label)
                    return label

        links = {set_link_label(k): v for k, v in links.items()}

        # set links
        for label, (in_pad, out_pad) in links.items():
            if out_pad is None:
                # stream input
                if isinstance(in_pad[0], int):
                    # only 1 filter takes the stream as its input
                    assign_link(in_labels, label, *in_pad)
                else:
                    # multiple filters take the stream as their inputs
                    for id in in_pad:
                        assign_link(in_labels, label, *id)
            elif in_pad is None:
                # fg output
                assign_link(out_labels, label, *out_pad)
            else:
                # internal links
                assign_link(in_labels, label, *in_pad)
                assign_link(out_labels, label, *out_pad)

    # COMPOSE FILTER GRAPH

    # add optional auto-scaling filter arguments
    expr = (
        ""
        if sws_flags is None
        else f"sws_flags={escape(compose_filter_args(*sws_flags))};"
    )

    # form individual filters, form chains, then comine them into graphs
    expr += ";".join(
        [
            ",".join(
                [
                    define_filter(
                        info, in_labels.get((i, j), None), out_labels.get((i, j), None)
                    )
                    for j, info in enumerate(chains)
                ]
            )
            for i, chains in enumerate(filter_specs)
        ]
    )

    return expr
