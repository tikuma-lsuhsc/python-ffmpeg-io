"""SimpleStreams Module: FFmpeg"""

from __future__ import annotations

from time import time
import logging

logger = logging.getLogger("ffmpegio")

from typing import Literal
from fractions import Fraction
from .._typing import RawDataBlob
from ..filtergraph.abc import FilterGraphObject
from ..errors import FFmpegioError

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
        kwargs.update(
            {"stdin": stdin, "progress": progress, "capture_log": True, "bufsize": 0}
        )

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

        inopts = ffmpeg_args.get("inputs", [])[0][1]
        outopts = ffmpeg_args.get("outputs", [])[0][1]

        outopts["map"] = "0:v:0"
        (
            self.dtype,
            self.shape,
            self.rate,
        ) = configure.finalize_video_read_opts(
            ffmpeg_args,
            input_info=[
                {
                    "src_type": (
                        "filtergraph" if outopts.get("f", None) == "lavfi" else "url"
                    )
                }
            ],
        )

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

        outopts = ffmpeg_args["outputs"][0][1]
        outopts["map"] = "0:a:0"
        (
            self.dtype,
            self.shape,
            self.rate,
        ) = configure.finalize_audio_read_opts(
            ffmpeg_args,
            input_info=[
                {
                    "src_type": (
                        "filtergraph" if outopts.get("f", None) == "lavfi" else "url"
                    )
                }
            ],
        )

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
                "bufsize": 0,
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

    stream_type: Literal["a", "v"]

    # fmt:off
    def _set_options(self, options, shape, dtype, rate=None, expr=None): ...
    def _pre_open(self, ffmpeg_args): ...
    def _finalize_output(self, info): ...
    # fmt:on

    def __init__(
        self,
        converter,
        data_viewer,
        info_viewer,
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
        self.dtype_in = dtype_in
        #:tuple(int): input array shape
        self.shape_in = shape_in
        #:str: output array dtype
        self.dtype = dtype
        #:tuple(int): output array shape
        self.shape = shape

        self.nin = 0  #:int: total number of input samples sent to FFmpeg
        self.nout = 0  #:int: total number of output sampless received from FFmpeg
        # :float: # of output samples per 1 input sample
        self._out2in = None

        # set this to false in _finalize() if guaranteed for the logger to have output stream info
        self._loggertimeout = True

        self._proc = None

        inopts = utils.pop_extra_options(options, "_in")
        glopts = utils.pop_global_options(options)

        try:
            not_ready, self.shape_in, self.dtype_in = self._set_options(
                inopts, shape_in, dtype_in, rate_in
            )
        except FFmpegioError as exc:
            raise FFmpegioError(
                exc.args[0].replace("dtype", "dtype_in").replace("shape", "shape_in")
            ) from exc

        self._set_options(options, shape, dtype, rate, expr)
        self._output_opts = options

        ffmpeg_args = configure.empty(glopts)
        self._input_info = configure.process_raw_inputs(ffmpeg_args, [inopts], {})
        configure.assign_input_url(ffmpeg_args, 0, "pipe:0")

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
                "bufsize": 0,
            }
        )

        # if input is fully configured, start FFmpeg now
        if not not_ready:
            self._open()

    def _open(self, data=None):
        ffmpeg_args = self._cfg["ffmpeg_args"]
        in_opts = ffmpeg_args["inputs"][0][1]

        # if data array is given, finalize input options (updates the initial options)
        if data is not None:
            _, self.shape_in, self.dtype_in = self._set_options(
                in_opts, *self._infoviewer(obj=data)
            )

        # add the output pipe
        self.dtype, self.shape, self.rate = configure.process_raw_outputs(
            ffmpeg_args,
            self._input_info,
            [f"0:{self.stream_type}:0"],
            self._output_opts,
        )[0]["media_info"]
        configure.assign_output_url(ffmpeg_args, 0, "pipe:1")

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

        try:
            # write the sentinel to the writer thread to terminate immediately
            self._writer.join()
        except:
            # possibly close before opening the writer thread
            pass

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

    def flush(self, timeout: float = None) -> RawDataBlob:
        """Close the stream input and retrieve the remaining output samples

        :param timeout: timeout duration in seconds, defaults to None
        :return: remaining output samples
        """

        timeout = timeout or self.default_timeout

        # If no input, close stdin and read all remaining frames
        self._writer.write(None)  # sentinel message
        self._writer.join()  # wait until all written data reaches FFmpeg
        self._proc.stdin.close()  # close stdin -> triggers ffmpeg to shutdown
        self._proc.wait()
        y = self._reader.read_all(timeout) # read whatever is left in the read queue
        nframes = len(y) // self._bps_out
        self.nout += nframes
        return self._converter(
            b=y[: nframes * self._bps_out],
            dtype=self.dtype,
            shape=self.shape,
            squeeze=False,
        )


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
    stream_type = "v"

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

    def _set_options(
        self,
        options: dict,
        shape: tuple[int] | None,
        dtype: str | None,
        rate: Fraction | int | None = None,
        expr: FilterGraphObject | None = None,
    ) -> tuple[bool, tuple[int, ...], str]:

        pix_fmt = options.get("pix_fmt", None)
        s = options.get("s", None)

        if (
            dtype is None
            and pix_fmt is not None
            and (s is not None or shape is not None)
        ):
            if shape is not None and s is None:
                s = shape[2::-1]
            if pix_fmt is not None and s is not None:
                dtype_alt, shape_alt = utils.get_video_format(pix_fmt, s)
                if dtype is not None and dtype != dtype_alt:
                    raise FFmpegioError(
                        f"Specifid {dtype=} and {pix_fmt=} are not compatible."
                    )
                if shape is not None and shape != shape_alt:
                    raise FFmpegioError(
                        f"Specifid {shape=}, {s=}, and {pix_fmt=} are not compatible."
                    )
        elif (pix_fmt is None or s is None) and dtype is not None and shape:
            s_alt, pix_fmt_alt = utils.guess_video_format(shape, dtype)
            if s is None:
                s = s_alt
            elif s != s_alt:
                raise FFmpegioError(
                    f"Specifid {dtype=}, {shape=}, and {s=} are not compatible."
                )
            if pix_fmt is None:
                pix_fmt = pix_fmt_alt
            elif pix_fmt != pix_fmt_alt:
                raise FFmpegioError(
                    f"Specifid {dtype=}, {shape=}, and {pix_fmt=} are not compatible."
                )

        options["f"] = "rawvideo"
        if rate is not None:
            options["r"] = rate
        if expr is not None:
            options["vf"] = expr
        if s is not None:
            options["s"] = s
        if pix_fmt is not None:
            options["pix_fmt"] = pix_fmt

        return pix_fmt is None or s is None, shape, dtype

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
    stream_type = "a"

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

    def _set_options(
        self,
        options: dict,
        shape: tuple[int] | None,
        dtype: str | None,
        rate: Fraction | int | None = None,
        expr: FilterGraphObject | None = None,
    ) -> tuple[bool, tuple[int], str]:

        ac = options.get("ac", None)

        if shape is None:
            if ac is not None:
                shape = (ac,)
        elif ac is None:
            ac = shape[-1]
        elif shape[-1] != ac:
            raise FFmpegioError(f"{shape=} and {ac=} does not match")

        sample_fmt = options.get("sample_fmt", None)
        if dtype is None and sample_fmt is not None:
            if sample_fmt is not None:
                dtype_alt, _ = utils.get_audio_format(options["sample_fmt"])
                if dtype is None:
                    dtype = dtype_alt
                elif dtype != dtype_alt:
                    raise FFmpegioError(
                        f"Specifid {dtype=} and {pix_fmt=} are not compatible."
                    )
        elif (sample_fmt is None) and (dtype is not None):
            sample_fmt_alt, _ = utils.guess_audio_format(dtype)
            if sample_fmt is None:
                sample_fmt = sample_fmt_alt
            elif sample_fmt != sample_fmt_alt:
                raise FFmpegioError(
                    f"Specifid {dtype=} and {sample_fmt=} are not compatible."
                )

        options["f"] = "rawvideo"
        if rate:
            options["ar"] = rate
        if expr is not None:
            options["af"] = expr
        if sample_fmt is not None:
            options["sample_fmt"] = sample_fmt
            options["c:a"], options["f"] = utils.get_audio_codec(sample_fmt)
        if ac is not None:
            options["ac"] = ac

        return sample_fmt is None or ac is None, shape, dtype

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
