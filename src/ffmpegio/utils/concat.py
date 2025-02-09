"""FFConcat class to build/use ffconcat list file for concat demuxer
"""

from glob import glob
import io, re
import os
from tempfile import NamedTemporaryFile
from functools import partial
import logging

logger = logging.getLogger("ffmpegio")

from .._utils import escape, unescape

# https://trac.ffmpeg.org/wiki/Concatenate
# https://ffmpeg.org/ffmpeg-formats.html#concat


class FFConcat:
    """Create FFmpeg concat demuxer source generator

    :param script: concat script to parse, defaults to None (empty script)
    :type script: str, optional
    :param pipe_url: stdin pipe or None to use a temp file, defaults to None
    :type pipe_url: bool, optional

    FFConcat instance is intended to be used as an input url object when invoking `ffmpegprocess.run`
    or `ffmpegprocess.Popen`. The FFmpeg command parser stringify the ConatDemuxer instance to either the
    temp file path or the pipe name, depending on the chosen operation mode. The temporary listing is
    automatically generated within the FFConcat context. If the listing is send in via pipe, the
    listing data can be obtained via `ffconcat.input`.

    The listing can be populated either by parsing a valid ffconcat script via the constructor or
    `ffconcat.parse()`. Or an individual item (file, stream, option, or chapter) can be added by
    `ffconcat.add_file()`, `ffconcat.add_stream()`, `ffconcat.add_option()`, or
    `ffconcat.add_chapter()`. Files can also be added in batch by `ffconcat.add_files()`.

    Aside from the intended operations with `ffmpegprocess`, a listing file can be explicitly created by
    calling `ffconcat.compose()` with a valid writable text file object.

    Alternately, the files in the listing can be used for a concat filtergraph use with `as_filter()`.

    Examples
    --------

    1. Concatenate mkv files with a temp listing file

       .. code-block:: python

           files = ['/video/video1.mkv','/video/video2.mkv']
           ffconcat = ffmpegio.FFConcat()
           ffconcat.add_files(files)
           with ffconcat: # generates temporary ffconcat file
               ffmpegio.transcode(ffconcat, 'output.mkv', f_in='concat', safe_in=0)

       Note that the files in an ffconcat listing file are defined relative to
       to the location of the ffconcat file. As such, both video files must be
       defined with absolute paths because the temporary ffconcat file is in a
       tempdir. Because the absolute paths are given, `safe_in=0` option must
       be specified.

    2. Save generated ffconcat file in a same folder as the source video files

       .. code-block:: python

           files = ['video1.mkv','video2.mkv']
           ffconcat = ffmpegio.FFConcat(ffconcat_url='/video/concat.txt')
           ffconcat.add_files(files)
           with ffconcat: # generates ffconcat file at ffconcat_url
               ffmpegio.transcode(ffconcat, 'output.mkv', f_in='concat')

       By creating the ffconcat listing file in the directory where the video files
       are, the files in the listing can be defined relatively (i.e., just filenames).
       FFConcat will overwrite the file if exists, and the generated ffconcat file
       will not be deleted.

    3. Concatenate mkv files with listing piped to stdin

       .. code-block:: python

           files = ['file:video1.mkv','file:video2.mkv']
           ffconcat = ffmpegio.FFConcat(pipe_url='-')
           ffconcat.add_files(files)
           ffmpegio.transcode(ffconcat, 'output.mkv', f_in='concat',
                              protocol_whitelist_in="pipe,file", safe_in=0)

       Because of files are specified by data passed in via pipe (protocol)
       the files in the `FFConcat` must specify the protocol: `file:`. Also,
       additional input options are necessary: `protocol_whitelist_in="pipe,file"`
       and `safe_in=0`.

    4. The concat script may be populated/altered inside the `with` statement,
       but `update()` must be called to update the prepared script:

       .. code-block:: python

           files = ['video1.mkv','video2.mkv']
           with ffmpegio.FFConcat(ffconcat_url='/video/concat.txt') as ffconcat:
               ffconcat.add_files(files)
               ffconcat.update() # must call update() before transcode
               ffmpegio.transcode(ffconcat,'output.mkv', f_in='concat')

    4. Rather than using demuxer, it can be used to compose concat filter command:

       .. code-block:: python

           inputs,fg = ffconcat.as_filter(v=1, a=1)

           ffmpegio.ffmpeg(
               {
                   "inputs": inputs,
                   "outputs": [("output.mkv", None)],
                   "global_options": {"filter_complex": fg},
               }
           )

    """

    class FileItem:
        """File listing item

        :param filepath: url of the file to be included
        :type filepath: str
        :param duration: duration of the file, defaults to None
        :type duration: str or numeric, optional
        :param inpoint: in point of the file, defaults to None
        :type inpoint: str or numeric, optional
        :param outpoint: out point of the file, defaults to None
        :type outpoint: str or numeric, optional
        :param metadata: Metadata of the packets of the file, defaults to None
        :type metadata: dict, optional
        :param options: Option to access, open and probe the file, defaults to None
        :type options: dict, optional
        """

        def __init__(
            self,
            filepath,
            duration=None,
            inpoint=None,
            outpoint=None,
            metadata=None,
            options=None,
        ):
            #:str: url of the file
            self.path = filepath
            #:str or numeric or None: duration of the file, optional
            self.duration = duration
            #:str or numeric or None: start time of the file, optional
            self.inpoint = inpoint
            #:str or numeric or None: end time of the file, optional
            self.outpoint = outpoint
            #:dict or None: metadata of the packets of the file, optional
            self.metadata = metadata or {}
            #:dict[str,Any]: option key-value pairs to be included
            self.options = options or {}

        @property
        def lines(self):
            """:List[str]: ffconcat lines of the file"""
            if not self.path:
                raise RuntimeError("Invalid FileItem. File path must be set.")
            lines = [
                f"file {escape(self.path)}\n",
                *(
                    f"{k} {getattr(self,k)}\n"
                    for k in ("duration", "inpoint", "outpoint")
                    if getattr(self, k) is not None
                ),
            ]
            if self.metadata is not None:
                lines.extend(
                    [
                        f"file_packet_meta {k} {escape(v)}\n"
                        for k, v in self.metadata.items()
                    ]
                )
            if self.options is not None:
                lines.extend(
                    [f"option {k} {escape(v)}\n" for k, v in self.options.items()]
                )
            return lines

    class StreamItem:
        """Stream listing item

        :param id: ID of the stream, defaults to None
        :type id: str, optional
        :param codec: Codec for the stream, defaults to None
        :type codec: str, optional
        :param metadata: Metadata for the stream, defaults to None
        :type metadata: dict, optional
        :param extradata: Extradata for the stream in hexadecimal, defaults to None
        :type extradata: str or bytes-like, optional
        """

        def __init__(self, id=None, codec=None, metadata=None, extradata=None):
            #:str or None: id of the stream, optional
            self.id = id
            #:str or None: codec of the stream, optional
            self.codec = codec
            #:dict or None: of the stream, optional
            self.metadata = metadata or {}
            #:bytes or str or None: extra data of the stream, optional
            self.extradata = extradata

        @property
        def lines(self):
            """:List[str]: ffconcat lines of the stream"""

            if all(
                (getattr(self, k) is None for k in ("id", "codec", "extradata"))
            ) and not len(self.metadata):
                raise RuntimeError(
                    "Invalid StreamItem. At least one attribute must be set."
                )

            lines = ["stream\n"]
            if self.id is not None:
                lines.append(f"exact_stream_id {self.id}\n")
            if self.codec is not None:
                lines.append(f"stream_codec {self.codec}\n")
            if self.metadata is not None:
                lines.extend(
                    [f"stream_meta {k} {escape(v)}\n" for k, v in self.metadata.items()]
                )
            if self.extradata is not None:
                lines.append(
                    f"stream_extradata {self.extradata if isinstance(self.extradata,str) else memoryview(self.extradata).hex()}\n"
                )

            return lines

    def __init__(self, script=None, pipe_url=None, ffconcat_url=None):
        # :List[FFConcat.FileItem]: list of files to be included in the order of appearance
        self.files = []
        #:ListConcatDemuxer.StreamItem]: list of streams to be included in the order of appearance
        self.streams = []
        #:dict[str,tuple]: chapter id-(start,end) pairs to be included
        self.chapters = {}
        #:str|None: specify pipe url if concat script to be loaded via stdin; None via a temp file
        self.pipe_url = pipe_url
        #:str|None: specify url to save generated ffconcat file instead of a temp file
        self.ffconcat_url = ffconcat_url
        # used by context manager
        self._temp_file = None

        if script is not None:
            self.parse(script)

    @property
    def last_file(self):
        """:FFConcat.FileItem: Last added file item"""
        try:
            return self.files[-1]
        except:
            raise ValueError("No file defined.")

    @property
    def last_stream(self):
        """:FFConcat.StreamItem: Last added stream item"""
        try:
            return self.streams[-1]
        except:
            raise ValueError("No stream defined.")

    def add_file(
        self,
        filepath,
        duration=None,
        inpoint=None,
        outpoint=None,
        metadata=None,
        options=None,
    ):
        """append a file to the list

        :param filepath: url of the file to be included
        :type filepath: str
        :param duration: duration of the file, defaults to None
        :type duration: str or numeric, optional
        :param inpoint: in point of the file, defaults to None
        :type inpoint: str or numeric, optional
        :param outpoint: out point of the file, defaults to None
        :type outpoint: str or numeric, optional
        :param metadata: Metadata of the packets of the file, defaults to None
        :type metadata: dict, optional
        :param options: Option to access, open and probe the file, defaults to None
        :type options: dict, optional
        """
        self.files.append(
            self.FileItem(filepath, duration, inpoint, outpoint, metadata, options)
        )

    def add_files(self, files):
        """append files to the list

        :param files: list of files
        :type files: Sequence[str]
        """

        for file in files:
            self.files.append(self.FileItem(file))

    def add_glob(self, expr, root_dir=None, recursive=False):
        """append files with glob expression

        :param expr: glob expression
        :type expr: str
        :param root_dir: the root directory for searching, defaults to None (uses the current directory)
        :type root_dir: str, optional
        :param recursive: True to use the pattern “**” to match any files and zero or more directories, defaults to False
        :type recursive: bool, optional
        """
        if root_dir:
            expr = os.path.join(root_dir, expr)
        else:
            expr = ""
        for file in glob(expr, recursive=recursive):
            self.files.append(self.FileItem(os.path.relpath(file, root_dir)))

    def add_sequence(self, expr):
        raise ValueError("TODO")

    def add_stream(self, id=None, codec=None, metadata=None, extradata=None):
        """append a stream specification to the list

        :param id: ID of the stream, defaults to None
        :type id: str, optional
        :param codec: Codec for the stream, defaults to None
        :type codec: str, optional
        :param metadata: Metadata for the stream, defaults to None
        :type metadata: dict, optional
        :param extradata: Extradata for the stream in hexadecimal, defaults to None
        :type extradata: str or bytes-like, optional
        """
        self.streams.append(self.StreamItem(id, codec, metadata, extradata))

    def add_chapter(self, id, start, end):
        """add a chapter

        :param id: chapter ID
        :type id: str
        :param start: start time
        :type start: numeric or str
        :param end: end time
        :type end: numeric or str
        """

        self.chapters[id] = (start, end)

    def parse(self, script, append=False):
        """parse ffconcat script

        :param script: ffconcat script
        :type script: str
        :param append: True to append to the existing listing, False to clear
                       existing and start new, defaults to False
        :type append: bool, optional
        """

        def new_file(args):
            self.files.append(self.FileItem(unescape(args)))

        def new_stream(_):
            self.streams.append(self.StreamItem())

        def set_file_attr(key, args):
            try:
                args = float(args)
            except:
                pass
            setattr(self.last_file, key, args)

        def set_file_meta(esc, args):
            k, v = args.split(esc, 1)
            self.last_file.metadata[k] = unescape(v)

        def set_option(args):
            key, value = args.split(" ", 1)
            self.last_file.options[key] = unescape(value)

        def set_stream_attr(key, args):
            setattr(self.last_stream, key, args)

        def set_stream_meta(args):
            k, v = args.split(" ", 1)
            self.last_stream.metadata[k] = unescape(v)

        def set_chapter(args):
            id, start, end = args.split(" ", 2)
            self.chapters[unescape(id)] = (start, end)

        arg_parsers = {
            "file": new_file,
            "duration": partial(set_file_attr, "duration"),
            "inpoint": partial(set_file_attr, "inpoint"),
            "outpoint": partial(set_file_attr, "outpoint"),
            "file_packet_metadata": partial(set_file_meta, "="),
            "file_packet_meta": partial(set_file_meta, " "),
            "option": set_option,
            "stream": new_stream,
            "exact_stream_id": partial(set_stream_attr, "id"),
            "stream_meta": set_stream_meta,
            "stream_codec": partial(set_stream_attr, "codec"),
            "stream_extradata": partial(set_stream_attr, "extradata"),
            "chapter": set_chapter,
        }

        if not append:
            self.files = []
            self.streams = []
            self.options = {}
            self.chapters = {}

        for match in re.finditer(r"\s*([^#]\S*)\s+(.*)?\n", script):
            dir = match[1]
            args = match[2]

            if dir == "ffconcat" and args == "version 1.0":
                continue

            try:
                arg_parsers[dir](args)
            except:
                raise ValueError(f"Unknown directive or invalid syntax: {dir} {args}")

    def compose(self, f=None):
        """compose ffconcat file

        :param f: writable file-like object, defaults to None, outputting to a
                  :py:class:`StringIO` object.
        :type f: File-like object, optional
        :return: passes through `f` or the created :py:class:`StringIO` object
        :rtype: File-like object
        """

        if f is None:
            f = io.StringIO()

        f.write("ffconcat version 1.0\n")

        for file in self.files:
            f.writelines(file.lines)

        for stream in self.streams:
            f.writelines(stream.lines)

        for id, start, end in sorted(
            ((key, *value) for key, value in self.chapters.items()),
            key=lambda el: el[1],
        ):
            f.write(f"chapter {escape(id)} {start} {end}\n")
        return f

    def __enter__(self):
        self._temp_file = self.compose(
            None
            if self.pipe_url
            else open(self.ffconcat_url, "wt")
            if self.ffconcat_url
            else NamedTemporaryFile("wt", delete=False)
        )
        self._temp_file.close()

        return self

    def update(self):
        """Update the prepared script for the context"""
        if self._temp_file:
            os.remove(self._temp_file.name)
            self._temp_file = self.compose(
                None
                if self.pipe_url
                else open(self.ffconcat_url, "wt")
                if self.ffconcat_url
                else NamedTemporaryFile("wt", delete=False)
            )
            self._temp_file.close()

    def __exit__(self, *exc):
        if self._temp_file and not self.ffconcat_url:
            os.remove(self._temp_file.name)
        self._temp_file = None

    @property
    def url(self):
        """:str: url to use as FFmpeg `-i` option"""
        try:
            return self.pipe_url or self._temp_file.name
        except:
            return "unset"

    @property
    def script(self):
        """:str: composed concat listing script"""
        return (self._temp_file or self.compose()).getvalue()

    @property
    def input(self):
        """:bytes: script as bytes"""
        return self.script.encode("utf-8")

    def __str__(self) -> str:
        return self.url

    def __repr__(self) -> str:
        script = "\n        ".join(self.script.splitlines())
        return f"""FFmpeg concat demuxer source generator
    url: {self.url}
    script:
        {script}"""

    def as_filter(self, v=1, a=0, file_offset=0):
        """convert to concat filter commands

        :param v: number of video streams in each file, default to 1
        :type v: int, optional
        :param a: number of audio streams in each file, default to 0
        :type a: int, optional
        :param file_offset: id of the first file used in the filtergraph input labels
        :type file_offset: int, optional
        :returns: inputs list and concat filtergraph string
        :rtype: tuple[list[tuple[str,dict]], str]
        """

        if len(self.streams) or len(self.chapters):
            logger.warning(
                "Demuxer specifying non-file directives. Only file directives are converted."
            )

        meta_warn = opt_warn = False

        inputs = []
        for file in self.files:
            url = file.path
            opts = {}
            if file.duration:
                opts["t"] = file.duration
            if file.inpoint:
                opts["ss"] = file.inpoint
            if file.outpoint:
                opts["to"] = file.outpoint
            if file.metadata and not meta_warn:
                logger.warning("File metadata directives are ignored.")
                meta_warn = True
            if file.options and not opt_warn:
                logger.warning("File option directives are ignored.")
                opt_warn = True
            inputs.append((url, opts))

        n = len(self.files)
        nst = v + a
        in_labels = "".join(
            (f"[{i+file_offset}:{j}]" for j in range(nst) for i in range(n))
        )

        fg = f"{in_labels}concat=n={n}:v={v}:a={a}"

        return inputs, fg
