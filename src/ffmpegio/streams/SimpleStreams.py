from time import time
import logging

logger = logging.getLogger("ffmpegio")

from .. import utils, configure, ffmpegprocess, plugins
from ..threading import LoggerThread, ReaderThread, WriterThread

# fmt:off
__all__ = [ "SimpleVideoReader", "SimpleAudioReader", "SimpleVideoWriter",
    "SimpleAudioWriter", "SimpleVideoFilter", "SimpleAudioFilter"]
# fmt:on


class SimpleReaderBase:
    """base class for SISO media read stream classes"""

    def __init__(
        self,
        converter,
        viewer,
        url,
        show_log=None,
        progress=None,
        blocksize=None,
        sp_kwargs=None,
        **options,
    ) -> None:
        self._converter = converter  # :Callable: f(b,dtype,shape) -> data_object
        self._memoryviewer = viewer  #:Callable: f(data_object)->bytes-like object
        self.dtype = None  # :str: output data type
        self.shape = (
            None  # :tuple of ints: dimension of each video frame or audio sample
        )
        self.samplesize = (
            None  #:int: number of bytes of each video frame or audio sample
        )
        self.blocksize = None  #:positive int: number of video frames or audio samples to read when used as an iterator
        self.sp_kwargs = sp_kwargs  #:dict[str,Any]: additional keyword arguments for subprocess.Popen

        # get url/file stream
        input_options = utils.pop_extra_options(options, "_in")
        url, stdin, input = configure.check_url(
            url, False, format=input_options.get("f", None)
        )

        ffmpeg_args = configure.empty()
        configure.add_url(ffmpeg_args, "input", url, input_options)
        configure.add_url(ffmpeg_args, "output", "-", options)

        # abstract method to finalize the options => sets self.dtype and self.shape if known
        self._finalize(ffmpeg_args)

        # create logger without assigning the source stream
        self._logger = LoggerThread(None, show_log)

        kwargs = {**sp_kwargs} if sp_kwargs else {}
        kwargs.update({"stdin": stdin, "progress": progress, "capture_log": True})

        # start FFmpeg
        self._proc = ffmpegprocess.Popen(ffmpeg_args, **kwargs)

        # set the log source and start the logger
        self._logger.stderr = self._proc.stderr
        self._logger.start()

        # if byte data is given, feed it
        if input is not None:
            self._proc.stdin.write(input)

        # wait until output stream log is captured if output format is unknown
        try:
            if self.dtype is None or self.shape is None:
                logger.debug(
                    "[reader main] waiting for logger to provide output stream info"
                )
                info = self._logger.output_stream()
                logger.debug(f"[reader main] received {info}")
                self._finalize_array(info)
            else:
                self._logger.index("Output")
        except:
            if self._proc.poll() is None:
                raise self._logger.Exception
            else:
                raise ValueError("failed retrieve output data format")

        self.samplesize = utils.get_samplesize(self.shape, self.dtype)

        self.blocksize = blocksize or max(1024**2 // self.samplesize, 1)
        logger.debug("[reader main] completed init")

    def close(self):
        """Flush and close this stream. This method has no effect if the stream is already
            closed. Once the stream is closed, any read operation on the stream will raise
            a ValueError.

        As a convenience, it is allowed to call this method more than once; only the first call,
        however, will have an effect.

        """

        if self._proc is None:
            return

        self._proc.stdout.close()
        self._proc.stderr.close()

        if self._proc.poll() is None:
            try:
                self._proc.terminate()
                if self._proc.poll() is None:
                    self._proc.kill()
            except:
                print("failed to terminate")
                pass

        logger.debug(f"[reader main] FFmpeg closed? {self._proc.poll()}")

        try:
            self._proc.stdin.close()
        except:
            pass
        self._logger.join()

    @property
    def closed(self):
        """:bool: True if the stream is closed."""
        return self._proc.poll() is not None

    @property
    def lasterror(self):
        """:FFmpegError: Last error FFmpeg posted"""
        if self._proc.poll():
            return self._logger.Exception()
        else:
            return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        F = self.read(self.blocksize)
        if F is None:
            raise StopIteration
        return F

    def readlog(self, n=None):
        if n is not None:
            self._logger.index(n)
        with self._logger._newline_mutex:
            return "\n".join(self._logger.logs or self._logger.logs[:n])

    def read(self, n=-1):
        """Read and return numpy.ndarray with up to n frames/samples. If
        the argument is omitted, None, or negative, data is read and
        returned until EOF is reached. An empty bytes object is returned
        if the stream is already at EOF.

        If the argument is positive, and the underlying raw stream is not
        interactive, multiple raw reads may be issued to satisfy the byte
        count (unless EOF is reached first). But for interactive raw streams,
        at most one raw read will be issued, and a short result does not
        imply that EOF is imminent.

        A BlockingIOError is raised if the underlying raw stream is in non
        blocking-mode, and has no data available at the moment."""
        logger.debug(f"[reader main] reading {n} samples")
        b = self._proc.stdout.read(n * self.samplesize if n > 0 else n)
        logger.debug(f"[reader main] read {len(b)} bytes")
        if not len(b):
            self._proc.stdout.close()
            return None
        return self._converter(b=b, shape=self.shape, dtype=self.dtype, squeeze=False)

    def readinto(self, array):
        """Read bytes into a pre-allocated, writable bytes-like object array and
        return the number of bytes read. For example, b might be a bytearray.

        Like read(), multiple reads may be issued to the underlying raw stream,
        unless the latter is interactive.

        A BlockingIOError is raised if the underlying raw stream is in non
        blocking-mode, and has no data available at the moment."""

        return (
            self._proc.stdout.readinto(self._memoryviewer(obj=array)) // self.samplesize
        )


class SimpleVideoReader(SimpleReaderBase):
    readable = True
    writable = False
    multi_read = False
    multi_write = False

    def __init__(
        self, url, show_log=None, progress=None, blocksize=1, sp_kwargs=None, **options
    ):
        hook = plugins.get_hook()
        super().__init__(
            hook.bytes_to_video,
            hook.video_bytes,
            url,
            show_log,
            progress,
            blocksize,
            sp_kwargs,
            **options,
        )

    def _finalize(self, ffmpeg_args):
        # finalize FFmpeg arguments and output array

        inurl, inopts = ffmpeg_args.get("inputs", [])[0]
        outopts = ffmpeg_args.get("outputs", [])[0][1]
        has_fg = configure.has_filtergraph(ffmpeg_args, "video")

        (
            self.dtype,
            self.shape,
            self.rate,
        ) = configure.finalize_video_read_opts(ffmpeg_args, istream="v:0")

        pix_fmt = outopts.get("pix_fmt", None)
        pix_fmt_in = inopts.get("pix_fmt", None)

        if pix_fmt_in is None and pix_fmt is None:
            raise ValueError("pix_fmt must be specified.")

        # construct basic video filter if options specified
        configure.build_basic_vf(
            ffmpeg_args, utils.alpha_change(pix_fmt_in, pix_fmt, -1)
        )

    def _finalize_array(self, info):
        # finalize array setup from FFmpeg log

        self.rate = info["r"]
        self.dtype, self.shape = utils.get_video_format(info["pix_fmt"], info["s"])


class SimpleAudioReader(SimpleReaderBase):
    readable = True
    writable = False
    multi_read = False
    multi_write = False

    def __init__(
        self,
        url,
        show_log=None,
        progress=None,
        blocksize=None,
        sp_kwargs=None,
        **options,
    ):
        hook = plugins.get_hook()
        super().__init__(
            hook.bytes_to_audio,
            hook.audio_bytes,
            url,
            show_log,
            progress,
            blocksize,
            sp_kwargs,
            **options,
        )

    def _finalize(self, ffmpeg_args):
        # finalize FFmpeg arguments and output array

        (
            self.dtype,
            ac,
            self.rate,
        ) = configure.finalize_audio_read_opts(ffmpeg_args, istream="a:0")

        if ac is not None:
            self.shape = (ac,)

    def _finalize_array(self, info):
        # finalize array setup from FFmpeg log

        self.rate = info["ar"]
        self.dtype, self.shape = utils.get_audio_format(
            info["sample_fmt"], info.get("ac", 1)
        )

    @property
    def channels(self):
        return self.shape[-1]


###########################################################################


class SimpleWriterBase:
    def __init__(
        self,
        viewer,
        url,
        shape_in=None,
        dtype_in=None,
        show_log=None,
        progress=None,
        overwrite=None,
        extra_inputs=None,
        sp_kwargs=None,
        **options,
    ) -> None:
        self._proc = None
        self._viewer = viewer
        self.dtype_in = dtype_in
        self.shape_in = shape_in

        # get url/file stream
        url, stdout, _ = configure.check_url(url, True)

        input_options = utils.pop_extra_options(options, "_in")

        ffmpeg_args = configure.empty()
        configure.add_url(ffmpeg_args, "input", "-", input_options)
        configure.add_url(ffmpeg_args, "output", url, options)

        # add extra input arguments if given
        if extra_inputs is not None:
            configure.add_urls(ffmpeg_args, "input", extra_inputs)

        # abstract method to finalize the options only if self.dtype and self.shape are given
        ready = self._finalize(ffmpeg_args)

        # create logger without assigning the source stream
        self._logger = LoggerThread(None, show_log)

        # FFmpeg Popen arguments
        self._cfg = {**sp_kwargs} if sp_kwargs else {}
        self._cfg.update(
            {
                "ffmpeg_args": ffmpeg_args,
                "progress": progress,
                "capture_log": True,
                "overwrite": overwrite,
                "stdout": stdout,
            }
        )

        if ready:
            self._open()

    def _open(self, data=None):
        # if data array is given, finalize the FFmpeg configuration with it
        if data is not None:
            self._finalize_with_data(data)

        # start FFmpeg
        self._proc = ffmpegprocess.Popen(**self._cfg)
        self._cfg = False

        # set the log source and start the logger
        self._logger.stderr = self._proc.stderr
        self._logger.start()

    def close(self):
        """close the output stream"""
        if self._proc is None:
            return

        if self._proc.stdin and not self._proc.stdin.closed:
            try:
                self._proc.stdin.close()  # flushes the buffer first before closing
            except OSError as e:
                logger.error(e)
        self._proc.wait()
        if self._proc.stderr and not self._proc.stderr.closed:
            try:
                self._proc.stderr.close()
            except OSError as e:
                logger.error(e)

        self._logger.join()

    @property
    def closed(self):
        """:bool: True if stream is closed"""
        return self._proc.poll() is not None

    @property
    def lasterror(self):
        """:FFmpegError or None: Last caught FFmpeg error"""
        if self._proc.poll():
            return self._logger.Exception()
        else:
            return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def readlog(self, n=None):
        if n is not None:
            self._logger.index(n)
        with self._logger._newline_mutex:
            return "\n".join(self._logger.logs or self._logger.logs[:n])

    def write(self, data):
        """Write the given numpy.ndarray object, data, and return the number
        of bytes written (always equal to the number of data frames/samples,
        since if the write fails an OSError will be raised).

        When in non-blocking mode, a BlockingIOError is raised if the data
        needed to be written to the raw stream but it couldn’t accept all
        the data without blocking.

        The caller may release or mutate data after this method returns,
        so the implementation should only access data during the method call.

        """

        if self._cfg:
            # if FFmpeg not yet started, finalize the configuration with
            # the data and start
            self._open(data)

        logger.debug("[writer main] writing...")

        try:
            self._proc.stdin.write(self._viewer(obj=data))
        except (BrokenPipeError, OSError):
            self._logger.join_and_raise()

    def flush(self):
        self._proc.stdin.flush()


class SimpleVideoWriter(SimpleWriterBase):
    readable = False
    writable = True
    multi_read = False
    multi_write = False

    def __init__(
        self,
        url,
        rate_in,
        shape_in=None,
        dtype_in=None,
        show_log=None,
        progress=None,
        overwrite=None,
        extra_inputs=None,
        sp_kwargs=None,
        **options,
    ):
        options["r_in"] = rate_in
        if "r" not in options:
            options["r"] = rate_in

        super().__init__(
            plugins.get_hook().video_bytes,
            url,
            shape_in,
            dtype_in,
            show_log,
            progress,
            overwrite,
            extra_inputs,
            sp_kwargs,
            **options,
        )

    def _finalize(self, ffmpeg_args) -> None:
        inopts = ffmpeg_args["inputs"][0][1]
        inopts["f"] = "rawvideo"

        ready = "s" in inopts and "pix_fmt" in inopts

        if not (ready or (self.dtype_in is None or self.shape_in is None)):
            s, pix_fmt = utils.guess_video_format((self.shape_in, self.dtype_in))
            if "s" not in inopts:
                inopts["s"] = s
            if "pix_fmt" not in inopts:
                inopts["pix_fmt"] = pix_fmt
            ready = True

        if ready:
            # set basic video filter chain if related options are specified
            configure.build_basic_vf(
                ffmpeg_args, configure.check_alpha_change(ffmpeg_args, -1)
            )
        return ready

    def _finalize_with_data(self, data):
        ffmpeg_args = self._cfg["ffmpeg_args"]
        inopts = ffmpeg_args["inputs"][0][1]
        shape, dtype = plugins.get_hook().video_info(obj=data)
        s, pix_fmt = utils.guess_video_format(shape, dtype)

        configure.build_basic_vf(
            ffmpeg_args, configure.check_alpha_change(ffmpeg_args, -1)
        )

        if "s" not in inopts:
            inopts["s"] = s
        if "pix_fmt" not in inopts:
            inopts["pix_fmt"] = pix_fmt

        self.shape_in = shape
        self.dtype_in = dtype


class SimpleAudioWriter(SimpleWriterBase):
    readable = False
    writable = True
    multi_read = False
    multi_write = False

    def __init__(
        self,
        url,
        rate_in,
        shape_in=None,
        dtype_in=None,
        show_log=None,
        progress=None,
        overwrite=None,
        extra_inputs=None,
        sp_kwargs=None,
        **options,
    ):
        options["ar_in"] = rate_in
        if "ar" not in options:
            options["ar"] = rate_in

        super().__init__(
            plugins.get_hook().audio_bytes,
            url,
            shape_in,
            dtype_in,
            show_log,
            progress,
            overwrite,
            extra_inputs,
            sp_kwargs,
            **options,
        )

    def _finalize(self, ffmpeg_args):
        # ffmpeg_args must have sample format & sampling rate specified
        inopts = ffmpeg_args["inputs"][0][1]
        ready = "sample_fmt" in inopts and "ac" in inopts

        if not ready and (self.dtype_in is not None or self.shape_in is not None):
            inopts = ffmpeg_args["inputs"][0][1]
            inopts["sample_fmt"], inopts["ac"] = utils.guess_audio_format(
                self.dtype_in, self.shape_in
            )
            ready = True

        if ready and not ("c:a" in inopts or "acodec" in inopts):
            # fill audio codec and format options
            inopts["c:a"], inopts["f"] = utils.get_audio_codec(inopts["sample_fmt"])
            if "acodec" in inopts:
                del inopts["acodec"]

        return ready

    def _finalize_with_data(self, data):
        self.shape_in, self.dtype_in = plugins.get_hook().audio_info(obj=data)

        inopts = self._cfg["ffmpeg_args"]["inputs"][0][1]
        inopts["sample_fmt"], inopts["ac"] = utils.guess_audio_format(
            self.dtype_in, self.shape_in
        )
        inopts["c:a"], inopts["f"] = utils.get_audio_codec(inopts["sample_fmt"])


###############################################################################


class SimpleFilterBase:
    """base class for SISO media filter stream classes

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str, None
    :param rate_in: input sample rate
    :type rate_in: int, float, Fraction, str
    :param shape_in: input single-sample array shape, defaults to None
    :type shape_in: seq of ints, optional
    :param dtype_in: input data type string, defaults to None
    :type dtype_in: str, optional
    :param rate: output sample rate, defaults to None (auto-detect)
    :type rate: int, float, Fraction, str, optional
    :param shape: output single-sample array shape, defaults to None
    :type shape: seq of ints, optional
    :param dtype: output data type string, defaults to None
    :type dtype: str, optional
    :param blocksize: read buffer block size in samples, defaults to None
    :type blocksize: int, optional
    :param default_timeout: default filter timeout in seconds, defaults to None (10 ms)
    :type default_timeout: float, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                    defaults to None (no show/capture)

    :type show_log: bool, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional

    """

    # fmt:off
    def _set_options(self, options, shape, dtype, rate=None, expr=None): ...
    def _pre_open(self, ffmpeg_args): ...
    def _finalize_output(self, info): ...
    # fmt:on

    def __init__(
        # fmt:off
        self, converter, data_viewer, info_viewer, expr, rate_in, shape_in=None, dtype_in=None, 
        rate=None, shape=None, dtype=None, blocksize=None, default_timeout=None,
        progress=None, show_log=None,         sp_kwargs=None,
**options,
        # fmt:on
    ) -> None:
        if not rate_in:
            if rate:
                rate_in = rate
            else:
                raise ValueError("Either rate_in or rate must be defined.")

        # :Callable: create a new data block object
        self._converter = converter

        # :Callable: get bytes-like object of the data block obj
        self._memoryviewer = data_viewer

        # :Callable: get bytes-like object of the data block obj
        self._infoviewer = info_viewer

        #:float: default filter operation timeout in seconds
        self.default_timeout = default_timeout or 10e-3

        #:int|Fraction: input sample rate
        self.rate_in = rate_in
        #:int|Fraction: output sample rate
        self.rate = rate

        #:str: input array dtype
        self.dtype_in = None
        #:tuple(int): input array shape
        self.shape_in = None
        #:str: output array dtype
        self.dtype = None
        #:tuple(int): output array shape
        self.shape = None

        self.nin = 0  #:int: total number of input samples sent to FFmpeg
        self.nout = 0  #:int: total number of output sampless received from FFmpeg
        # :float: # of output samples per 1 input sample
        self._out2in = None

        # set this to false in _finalize() if guaranteed for the logger to have output stream info
        self._loggertimeout = True

        self._proc = None

        ffmpeg_args = configure.empty()
        inopts = configure.add_url(
            ffmpeg_args, "input", "-", utils.pop_extra_options(options, "_in")
        )[1][1]
        outopts = configure.add_url(ffmpeg_args, "output", "-", options)[1][1]

        # configuration process
        # 1. during __init__
        # 1.0. set filter
        # 1.1. if dtype_in or shape_in is given, deduce the input options
        # 1.2. if dtype or shape is given, deduce the output options
        # 1.3. if input options are incomplete, defer starting the FFmpeg until
        #      the first data block is given
        # 2. during _open
        # 2.1. if data is given (i.e., input was not completely defined)
        # 2.1.1. get dtype_in and shape_in from data
        # 2.1.2. deduce the input ffmpeg options
        # 2.2. start ffmpeg
        # 2.3. start reader if dtype & shape are already set

        self.shape_in, self.dtype_in = self._set_options(
            inopts, shape_in, dtype_in, rate_in
        )

        self.shape, self.dtype = self._set_options(outopts, shape, dtype, rate, expr)

        # create the stdin writer without assigning the sink stream
        self._writer = WriterThread(None, 0)

        # create the stdout reader without assigning the source stream
        self._reader = ReaderThread(None, blocksize, 0)
        self._reader_needs_info = True

        # create logger without assigning the source stream
        self._logger = LoggerThread(None, show_log)

        # FFmpeg Popen arguments
        self._cfg = {**sp_kwargs} if sp_kwargs else {}
        self._cfg.update(
            {
                "ffmpeg_args": ffmpeg_args,
                "progress": progress,
                "capture_log": True,
            }
        )

        # if input is fully configured, start FFmpeg now
        if self.shape_in is not None and self.dtype_in is not None:
            self._open()

    def _open(self, data=None):
        ffmpeg_args = self._cfg["ffmpeg_args"]

        # if data array is given, finalize the FFmpeg configuration with it
        if data is not None:
            self.shape_in, self.dtype_in = self._set_options(
                ffmpeg_args["inputs"][0][1], *self._infoviewer(obj=data)
            )

        # final argument tweak before opening the ffmpeg
        self._pre_open(ffmpeg_args)

        # start FFmpeg
        self._proc = ffmpegprocess.Popen(**self._cfg)

        # set the log source and start the logger
        self._logger.stderr = self._proc.stderr
        self._logger.start()

        # start the writer
        self._writer.stdin = self._proc.stdin
        self._writer.start()

        if self.rate is not None and self.dtype is not None and self.shape is not None:
            self._reader_needs_info = False
            self._start_reader()
        self._cfg = False

    def _get_output_info(self, timeout):
        # run after the first input block is sent to FFmpeg
        try:
            info = self._logger.output_stream(
                timeout=timeout if self._loggertimeout else None
            )
        except TimeoutError as e:
            raise e
        except Exception as e:
            if self._proc.poll() is None:
                raise self._logger.Exception
            else:
                raise ValueError("failed retrieve output data format")

        self._finalize_output(info)
        self._reader_needs_info = False

    def _start_reader(self):
        self._bps_out = utils.get_samplesize(self.shape, self.dtype)
        self._bps_in = utils.get_samplesize(self.shape_in, self.dtype_in)
        self._out2in = self.rate / self.rate_in

        # start the FFmpeg output reader
        self._reader.itemsize = self._bps_out
        self._reader.stdout = self._proc.stdout
        self._reader.start()

        self._reader_needs_info = False

    def close(self):
        """Close the stream.

        This method has no effect if the stream is already closed. Once the
        stream is closed, any read operation on the stream will raise a ThreadNotActive.

        As a convenience, it is allowed to call this method more than once; only the first call,
        however, will have an effect.
        """

        if self._proc is None:
            return

        self._proc.stdout.close()
        self._proc.stderr.close()

        # kill the process
        try:
            self._proc.terminate()
        except:
            pass

        self._proc.stdin.close()

        try:
            self._logger.join()
        except:
            # possibly close before opening the logger thread
            pass
        try:
            self._reader.join()
        except:
            # possibly close before opening the reader thread
            pass
        try:
            self._writer.join()
        except:
            # possibly close before opening the writer thread
            pass

    @property
    def closed(self):
        """:bool: True if the stream is closed."""
        return self._proc.poll() is not None

    @property
    def lasterror(self):
        """:FFmpegError: Last error FFmpeg posted"""
        if self._proc.poll():
            return self._logger.Exception()
        else:
            return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def readlog(self, n=None):
        """get FFmpeg log lines

        :param n: number of lines to return, defaults to None (every line logged)
        :type n: int, optional
        :return: string containing the requested logs
        :rtype: str

        """
        if n is not None:
            self._logger.index(n)
        with self._logger._newline_mutex:
            return "\n".join(self._logger.logs or self._logger.logs[:n])

    def filter(self, data, timeout=None):
        """Run filter operation

        :param data: input data block
        :type data: numpy.ndarray
        :param timeout: timeout for the operation in seconds, defaults to None
        :type timeout: float, optional
        :return: output data block
        :rtype: numpy.ndarray

        The input `data` array is expected to have the datatype specified by
        Filter class' `dtype_in` property and the array shape to match Filter
        class' `shape_in` property or with an additional dimension prepended.

        .. important::
          [audio only] For the first 2-seconds or 50000-samples, whichever
          is smaller, TimeoutError may be raised because the necessary output
          format information is not yet made available from FFmpeg. This
          exception, however, only indicate the lack of output data and
          the input data can be assumed properly enqueued to be sent to
          FFmpeg process

        .. important::
          Once the output format is resolved, this method always return
          numpy.ndarray object as output. However, the exact number of
          samples is unknown, and it could be a properly shaped empty
          array. Additional buffering may be required if the following
          process requires a fixed number of samples.

        .. important::
          Filtering operation is always timed because the buffering
          protocols used by various subsystems of FFmpeg are undeterminable
          from Python. The operation timeout is controlled by `timeout`
          argument if specified or else  by `default_timeout` property. The
          default timeout duration is 10 ms, but it could be optimized for
          each use case (`blocksize` property, I/O rate ratio, typical size of
          `data` argument, etc.).

        """

        timeout = timeout or self.default_timeout

        timeout += time()

        if self._cfg:
            # if FFmpeg not yet started, finalize the configuration with
            # the data and start
            self._open(data)

        inbytes = self._memoryviewer(obj=data)

        try:
            self._writer.write(inbytes, timeout - time())
        except BrokenPipeError as e:
            # TODO check log for error in FFmpeg
            raise e

        if self._reader_needs_info:
            # with the data written, FFmpeg should inform the output setup
            self._get_output_info(timeout - time())
            self._start_reader()

        self.nin += len(inbytes) // self._bps_in
        nread = (int(self.nin * self._out2in) - self.nout) * self._bps_out
        y = self._reader.read(-nread, timeout - time())
        self.nout += len(y) // self._bps_out
        return self._converter(b=y, dtype=self.dtype, shape=self.shape, squeeze=False)

    def flush(self, timeout=None):
        """Close the stream input and retrieve the remaining output samples

        :param timeout: timeout duration in seconds, defaults to None
        :type timeout: float, optional
        :return: remaining output samples
        :rtype: numpy.ndarray
        """

        timeout = timeout or self.default_timeout

        # If no input, close stdin and read all remaining frames
        y = self._reader.read_all(timeout)
        self._proc.stdin.close()
        self._proc.wait()
        y += self._reader.read_all(None)
        self.nout += len(y) // self._bps_out
        return self._converter(b=y, dtype=self.dtype, shape=self.shape, squeeze=False)


class SimpleVideoFilter(SimpleFilterBase):
    """SISO video filter stream class

    .. important::
        Number of output frames is not predetermined although it is
        generally close to the expected number of frames based on the
        number of input frames and the ratio of input and output frame
        rate

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str, None
    :param rate_in: input frame rate
    :type rate_in: int, float, Fraction, str
    :param shape_in: input single-frame array shape, defaults to None
    :type shape_in: seq of ints, optional
    :param dtype_in: input numpy data type, defaults to None
    :type dtype_in: str, optional
    :param rate: output frame rate, defaults to None (auto-detect)
    :type rate: int, float, Fraction, str, optional
    :param shape: output single-frame array shape, defaults to None
    :type shape: seq of ints, optional
    :param dtype: output numpy data type, defaults to None
    :type dtype: str, optional
    :param blocksize: read buffer block size in frames, defaults to None (=1)
    :type blocksize: int, optional
    :param default_timeout: default filter timeout in seconds, defaults to None (10 ms)
    :type default_timeout: float, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                    defaults to None (no show/capture)
    :type show_log: bool, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional

    """

    readable = True
    writable = True
    multi_read = False
    multi_write = False

    def __init__(
        # fmt:off
        self, expr, rate_in, shape_in=None, dtype_in=None, rate=None, shape=None, dtype=None,
        blocksize=None, default_timeout=None, progress=None, show_log=None,         sp_kwargs=None,
**options,
        # fmt:on
    ) -> None:
        hook = plugins.get_hook()
        # fmt:off
        super().__init__(
            hook.bytes_to_video, hook.video_bytes, hook.video_info,
            expr, rate_in, shape_in, dtype_in, rate, shape, dtype,
            blocksize, default_timeout, progress, show_log, sp_kwargs,**options,
        )
        # fmt:on
        self._loggertimeout = False

    def _pre_open(self, ffmpeg_args):
        # append basic video filter chain
        configure.build_basic_vf(
            ffmpeg_args, configure.check_alpha_change(ffmpeg_args, -1)
        )

    def _set_options(self, options, shape, dtype, rate=None, expr=None):
        if rate:
            options["r"] = rate
        if expr is not None:
            options["vf"] = expr

        options["f"] = "rawvideo"

        if shape is None or dtype is None:
            # deduce them from options
            if shape is not None or dtype is not None:
                logger.warn(
                    "[SimpleVideoFilter] both dtype and shape must be defined for the arguments to take effect."
                )

            try:
                dtype, shape = utils.get_video_format(options["pix_fmt"], options["s"])
            except:
                return None, None
        else:
            options["s"], options["pix_fmt"] = utils.guess_video_format(shape, dtype)

        return shape, dtype

    def _finalize_output(self, info):
        # finalize array setup from FFmpeg log
        self.rate = info["r"]
        self.dtype, self.shape = utils.get_video_format(info["pix_fmt"], info["s"])


class SimpleAudioFilter(SimpleFilterBase):
    """SISO audio filter stream class

    .. important::
        If the total duration of the stream is less than 2 seconds, use
        :py:func:`audio.filter` function instead. FFmpeg does not start
        the filtering process until about 2-seconds or about 50000-samples
        worth of data are first accumulated. No output data will be produced
        during this initial accumulation period.

    .. important::
        The exact number of output samples after each :py:meth:`filter`
        call is not known and can be zero.

    :param expr: SISO filter graph or None if implicit filtering via output options.
    :type expr: str, None
    :param rate_in: input sample rate
    :type rate_in: int, float, Fraction, str
    :param shape_in: input single-sample array shape, defaults to None
    :type shape_in: seq of ints, optional
    :param dtype_in: input numpy data type, defaults to None
    :type dtype_in: str, optional
    :param rate: output sample rate, defaults to None (auto-detect)
    :type rate: int, float, Fraction, str, optional
    :param shape: output single-sample array shape, defaults to None
    :type shape: seq of ints, optional
    :param dtype: output numpy data type, defaults to None
    :type dtype: str, optional
    :param blocksize: read buffer block size in samples, defaults to None (=>1024)
    :type blocksize: int, optional
    :param default_timeout: default filter timeout in seconds, defaults to None (100 ms)
    :type default_timeout: float, optional
    :param progress: progress callback function, defaults to None
    :type progress: callable object, optional
    :param show_log: True to show FFmpeg log messages on the console,
                    defaults to None (no show/capture)
    :type show_log: bool, optional
    :param \\**options: FFmpeg options, append '_in' for input option names (see :doc:`options`)
    :type \\**options: dict, optional

    ..note::
        Use of larger `blocksize` parameter could improve the processing speed

    """

    readable = True
    writable = True
    multi_read = False
    multi_write = False

    def __init__(
        self,
        expr,
        rate_in,
        shape_in=None,
        dtype_in=None,
        rate=None,
        shape=None,
        dtype=None,
        blocksize=None,
        default_timeout=None,
        progress=None,
        show_log=None,
        sp_kwargs=None,
        **options,
    ) -> None:
        hook = plugins.get_hook()
        # fmt: off
        super().__init__(hook.bytes_to_audio, hook.audio_bytes, hook.audio_info,
            expr, rate_in, shape_in, dtype_in, rate, shape, dtype, 
            blocksize, default_timeout, progress, show_log, sp_kwargs,**options)
        # fmt: on

    def _pre_open(self, ffmpeg_args):
        if self.dtype is None:
            inopts = ffmpeg_args["inputs"][0][1]
            outopts = ffmpeg_args["outputs"][0][1]
            sample_fmt = outopts["sample_fmt"] = inopts["sample_fmt"]
            outopts["c:a"], outopts["f"] = utils.get_audio_codec(sample_fmt)

    def _set_options(self, options, shape, dtype, rate=None, expr=None):
        if rate:
            options["ar"] = rate
        if expr is not None:
            options["af"] = expr

        if shape is None:
            try:
                shape = (options["ac"],)
            except:
                shape = None
        else:
            options["ac"] = shape[-1]

        if dtype is None:
            try:
                dtype, _ = utils.get_audio_format(options["sample_fmt"])
            except:
                dtype = None
        else:
            options["sample_fmt"], _ = utils.guess_audio_format(dtype)
            options["c:a"], options["f"] = utils.get_audio_codec(options["sample_fmt"])

        return shape, dtype

    def _finalize_output(self, info):
        # finalize array setup from FFmpeg log
        self.rate = info["ar"]
        self.dtype, self.shape = utils.get_audio_format(info["sample_fmt"], info["ac"])

    @property
    def channels(self):
        """:int: Number of output channels (None if not yet determined)"""
        return self.shape and self.shape[-1]

    @property
    def channels_in(self):
        """:int: Number of input channels (None if not yet determined)"""
        return self.shape_in and self.shape_in[-1]
