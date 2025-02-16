from __future__ import annotations

from time import time
import logging

logger = logging.getLogger("ffmpegio")

from .. import utils, configure, ffmpegprocess, plugins
from ..threading import LoggerThread, ReaderThread, WriterThread

from typing_extensions import Unpack
from collections.abc import Sequence
from ..typing import (
    Literal,
    Any,
    RawStreamDef,
    ProgressCallable,
    RawDataBlob,
    StreamSpecDict,
    FFmpegArgs
)

import contextlib
from io import BytesIO
from fractions import Fraction

from namedpipe import NPopen

from ..threading import WriterThread
from ..filtergraph.presets import merge_audio

from .. import ffmpegprocess, utils, configure, FFmpegError, plugins, filtergraph as fgb
from ..utils import avi, pop_global_options
from ..utils.log import extract_output_stream
from ..threading import WriterThread, ReaderThread
from ..probe import streams_basic
from ..configure import init_media_read, init_media_write

# fmt:off
__all__ = ["Trancoder"]
# fmt:on

class PipedRunner:
    def __init__(
        self,
        args: FFmpegArgs,
        input_info: list,
        output_info: dict,
        progress: ProgressCallable | None = None,
        show_log: bool | None = None,
        blocksize:int=None,
        sp_kwargs: dict | None = None,
        **options: Unpack[dict[str, Any]],
    ):
        ...

class PipedReader(PipedRunner):
    def __init__(
        self,
        *urls: * tuple[str | tuple[str, dict[str, Any] | None]],
        map: Sequence[str] | dict[str, dict[str, Any] | None] | None = None,
        progress: ProgressCallable | None = None,
        show_log: bool | None = None,
        blocksize:int=None,
        sp_kwargs: dict | None = None,
        **options: Unpack[dict[str, Any]],
    ):
        ffmpeg_args, self._output_info, self._resolve_outputs = configure.init_media_read(urls, map, options)

        # run FFmpeg

        self.dtype = None  # :str: output data type
        self.shape = (
            None  # :tuple of ints: dimension of each video frame or audio sample
        )
        self.samplesize = (
            None  #:int: number of bytes of each video frame or audio sample
        )
        self.blocksize = None  #:positive int: number of video frames or audio samples to read when used as an iterator
        self.sp_kwargs = sp_kwargs  #:dict[str,Any]: additional keyword arguments for subprocess.Popen

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

class PipedWriter: ...


class PipedFilter: ...


class Transcoder:
    """Class to merge multiple media streams in memory

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

    def __init__(
        self,
        *input_formats_or_opts: Sequence[str | dict | None],
        nb_inputs: int | None = None,
        output_url: str | None = None,
        blocksize: int | None = None,
        default_timeout: float | None = None,
        progress: Callable | None = None,
        show_log: bool | None = None,
        sp_kwargs: dict | None = None,
        np_kwargs: dict | None = None,
        **output_options: dict[str, Any],
    ) -> None:

        #:float: default filter operation timeout in seconds
        self.default_timeout = default_timeout or 10e-3

        # set this to false in _finalize() if guaranteed for the logger to have output stream info
        self._loggertimeout = True

        nin = len(input_formats_or_opts)
        if nb_inputs is None and not nin:
            raise ValueError(
                "At least one input format/options must be given OR specify nb_inputs."
            )
        if nb_inputs is not None and nin > 0 and nb_inputs != nin:
            raise ValueError(
                "Both nb_inputs and input format/options are given but nb_inputs does not agree with the number of inputs specified."
            )

        inopts = (
            [
                v if isinstance(v, dict) else {} if v is None else {"f": v}
                for v in input_formats_or_opts
            ]
            if len(input_formats_or_opts)
            else [{}] * nb_inputs
        )

        nb_inputs = len(inopts)
        self._input_pipes = inpipes = [
            NPopen("w", **(np_kwargs or {})) for _ in range(nb_inputs)
        ]

        self._output_pipe = None
        if output_url is None:
            self._output_pipe = outpipe = NPopen("r", **(np_kwargs or {}))
            output_url = outpipe.path

        # create input format list
        self._args = ffmpeg_args = configure.empty()
        ffmpeg_args["inputs"].extend([(p.path, o) for p, o in zip(inpipes, inopts)])
        configure.add_url(ffmpeg_args, "output", output_url, output_options)[1][1]

        self._proc = None

        # create the stdin writer without assigning the sink stream
        self._writers = [WriterThread(p, 0) for p in inpipes]

        # create the stdout reader without assigning the source stream
        self._reader = None
        if self._output_pipe is not None:
            self._reader = ReaderThread(self._output_pipe, blocksize, 0)

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

        # start FFmpeg
        self._proc = ffmpegprocess.Popen(**self._cfg)

        self._logger.stderr = self._proc.stderr
        self._logger.start()

        # start the writers
        for writer in self._writers:
            writer.start()

        self._reader.start()
        self._cfg = False

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

        for p in self._input_pipes:
            p.close()

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
            for writer in self._writers:
                writer.join()
        except:
            # possibly close before opening the writer thread
            pass

    @property
    def closed(self) -> bool:
        """:bool: True if the stream is closed."""
        return self._proc.poll() is not None

    @property
    def lasterror(self) -> Exception:
        """:FFmpegError: Last error FFmpeg posted"""
        if self._proc.poll():
            return self._logger.Exception()
        else:
            return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def readlog(self, n: int | None = None) -> str:
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

    def write(self, stream_id: int, stream_data: bytes, timeout: float | None = None):
        """Run filter operation

        :param data: input data block
        :param timeout: timeout for the operation in seconds, defaults to None

        The input `data` array is expected to have the datatype specified by
        Filter class' `dtype_in` property and the array shape to match Filter
        class' `shape_in` property or with an additional dimension prepended.

        """

        timeout = timeout or self.default_timeout

        timeout += time()

        writer = self._writers[stream_id]
        try:
            writer.write(stream_data, timeout - time())
        except BrokenPipeError as e:
            # TODO check log for error in FFmpeg
            raise e

    def read(self, n: int = -1, timeout: float | None = None) -> bytes:

        try:
            return self._reader.read(n, timeout)
        except AttributeError as e:
            if self._reader is None:
                raise RuntimeError(
                    "read() not supported. FFmpeg is outputting directly to a file"
                )
            raise

    def read_nowait(self, n: int = -1) -> bytes:

        try:
            return self._reader.read_nowait(n)
        except AttributeError as e:
            if self._reader is None:
                raise RuntimeError(
                    "read_nowait() not supported. FFmpeg is outputting directly to a file"
                )
            raise

    def flush(self, timeout: float | None = None):
        """Flush the write buffers of the stream if applicable.

        :param timeout: timeout duration in seconds, defaults to None
        :type timeout: float, optional
        :return: remaining output samples
        :rtype: numpy.ndarray
        """

        timeout = timeout or self.default_timeout

        # If no input, close stdin and read all remaining frames
        y = self._reader.read_all(timeout)
        for p in self._input_pipes:
            p.close()
        self._proc.wait()
        y += self._reader.read_all(None)
