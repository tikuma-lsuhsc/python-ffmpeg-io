"""collection of thread classes for handling FFmpeg streams
"""

from __future__ import annotations

from typing import BinaryIO

from copy import deepcopy
import re, os
from threading import Thread, Condition, Lock, Event
from io import TextIOBase, TextIOWrapper
from time import sleep, time
from tempfile import TemporaryDirectory
from queue import Empty, Full, Queue
from math import ceil
from shutil import copyfileobj
import logging

from namedpipe import NPopen

logger = logging.getLogger("ffmpegio")

from .utils.avi import AviReader
from .utils.log import extract_output_stream as _extract_output_stream
from .errors import FFmpegError

# fmt:off
__all__ = ['AviReader', 'FFmpegError', 'ThreadNotActive', 'ProgressMonitorThread',
 'LoggerThread', 'ReaderThread', 'WriterThread', 'AviReaderThread', 'Empty', 'Full']
# fmt:on


class ThreadNotActive(RuntimeError):
    pass


class ProgressMonitorThread(Thread):
    """FFmpeg progress monitor class

    :param callback: [description]
    :type callback: function
    :param cancel_fun: [description], defaults to None
    :type cancel_fun: [type], optional
    :param url: [description], defaults to None
    :type url: [type], optional
    :param timeout: [description], defaults to 10e-3
    :type timeout: [type], optional
    """

    def __init__(self, callback, cancelfun=None, url=None, timeout=10e-3):
        if callback is None:
            self.url = self.cancelfun = self._thread = None
        else:
            tempdir = None if url else TemporaryDirectory()
            self.url = url or os.path.join(tempdir.name, "progress.txt")
            self.cancelfun = cancelfun
            super().__init__(args=(callback, tempdir, timeout))
            self._stop_monitor = Event()

    def start(self):
        if self.url:
            super().start()

    def join(self, timeout=None):
        if self.url:
            self._stop_monitor.set()
            super().join(timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.join()

    def run(self):
        callback, tempdir, timeout = self._args
        url = self.url

        pattern = re.compile(r"(.+)?=(.+)")
        logger.debug(f'[progress_monitor] monitoring "{url}"')

        while not (self._stop_monitor.is_set() or os.path.isfile(url)):
            sleep(timeout)

        logger.debug("[progress_monitor] file found")

        if not self._stop_monitor.is_set():
            with open(url, "rt") as f:
                last_mtime = None

                def update(sleep=True):
                    d = {}
                    mtime = os.fstat(f.fileno()).st_mtime
                    new_data = mtime != last_mtime
                    if new_data:
                        lines = f.readlines()
                        for line in lines:
                            m = pattern.match(line)
                            if not m:
                                continue
                            if m[1] != "progress":
                                val = m[2].lstrip()
                                try:
                                    val = int(val)
                                except:
                                    try:
                                        val = float(val)
                                    except:
                                        pass

                                d[m[1]] = val
                            else:
                                done = m[2] == "end"
                                try:
                                    if callback(d, done) and self.cancelfun:
                                        logger.debug(
                                            "[progress_monitor] operation canceled by user agent"
                                        )
                                        self.cancelfun()
                                        self.cancelfun = None
                                except Exception as e:
                                    logger.critical(
                                        f"[progress_monitor] user callback error:\n\n{e}"
                                    )
                    elif sleep:
                        sleep(timeout)

                while not self._stop_monitor.is_set():
                    last_mtime = update()

                # one final update just in case FFmpeg termianted during sleep
                update(False)

        if tempdir is not None:
            try:
                tempdir.cleanup()
            except:
                pass

        logger.debug("[progress_monitor] terminated")


class LoggerThread(Thread):
    def __init__(self, stderr, echo=False) -> None:
        self.stderr = stderr
        self.logs = []
        self._newline_mutex = Lock()
        self.newline = Condition(self._newline_mutex)
        self.echo = echo
        super().__init__()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stderr.close()
        self.join()  # will wait until stderr is closed
        return self

    def run(self):
        logger.debug("[logger] starting")
        stderr = self.stderr
        if not stderr or stderr.closed:
            logger.debug("[logger] exiting (stderr pipe not open)")
            return

        if not isinstance(stderr, TextIOBase):
            stderr = self.stderr = TextIOWrapper(stderr, "utf-8")
        while True:
            try:
                log = stderr.readline()
            except:
                # stderr stream closed/FFmpeg terminated, end the thread as well
                break
            if not log and stderr.closed:
                break

            log = log[:-1]  # remove the newline

            if not log:
                sleep(0.001)
                continue

            if self.echo:
                print(log)

            logger.debug(log)

            with self.newline:
                self.logs.append(log)
                self.newline.notify_all()

        with self.newline:
            self.stderr = None
            self.newline.notify_all()
        logger.debug("[logger] exiting")

    def index(self, prefix, start=None, block=True, timeout=None):
        start = int(start or 0)
        with self.newline:
            logs = self.logs[start:] if start else self.logs
            try:
                # check existing lines
                return (
                    next((i for i, log in enumerate(logs) if log.startswith(prefix)))
                    + start
                )
            except:
                if not self.is_alive():
                    raise ThreadNotActive("LoggerThread is not running")

                # no wait mode
                if not block:
                    raise ValueError("Specified line not found")

                # wait till matching line is read by the thread
                if timeout is not None:
                    timeout = time() + timeout
                start = len(self.logs)
                while True:
                    tout = timeout and timeout - time()
                    # wait till the next log update
                    if (tout is not None and tout < 0) or not self.newline.wait(tout):
                        raise TimeoutError("Specified line not found")

                    # FFmpeg could have been terminated without match
                    if self.stderr is None:
                        raise ValueError("Specified line not found")

                    # check the new lines
                    try:
                        return (
                            next(
                                (
                                    i
                                    for i, log in enumerate(self.logs[start:])
                                    if log.startswith(prefix)
                                )
                            )
                            + start
                        )
                    except:
                        # still no match, update the starting position
                        start = len(self.logs)

    def output_stream(self, file_id=0, stream_id=0, block=True, timeout=None):
        try:
            i = self.index(f"Output #{file_id}", block=block, timeout=timeout)
            self.index(f"  Stream #{file_id}:{stream_id}", i, block, timeout)
        except ThreadNotActive as e:
            raise e
        except TimeoutError:
            raise TimeoutError("Specified output stream not found")
        except Exception as e:
            raise ValueError("Specified output stream not found")

        with self._newline_mutex:
            return _extract_output_stream(self.logs, hint=i)

    def join_and_raise(self, timeout: float | None = None):
        """wait till thread terminates and raise exception based on the log

        :param timeout: specifying a timeout for the operation in seconds if present, defaults to None
        :type timeout: float | None, optional
        :raises e: FFmpegError only if log is present

        Note: This method throws the exception regardless of the thread's status if log is available.
        """        
        
        self.join(timeout)
        e = self.Exception
        if e is not None:
            raise e

    @property
    def Exception(self) -> FFmpegError | None:
        """Exception gathered from the current log or None if there is no log"""
        return FFmpegError(self.logs) if len(self.logs) else None


class ReaderThread(Thread):
    def __init__(self, stdout, nmin=None, queuesize=None):
        super().__init__()
        self.stdout = stdout  #:readable stream: data source
        self.nmin = nmin  #:positive int: expected minimum number of read()'s n arg (not enforced)
        self.itemsize = None  #:int: number of bytes per time sample
        self._queue = Queue(queuesize or 0)  # inter-thread data I/O
        self._carryover = None  # extra data that was not previously read by user
        self._collect = True

    def start(self):
        if self.itemsize is None:
            raise ValueError(
                "Thread object's must have its itemsize property set with the expected sample/frame size in bytes"
            )

        super().start()

    def cool_down(self):
        # stop enqueue read samples
        self._collect = False
        try:
            self._queue.get_nowait()
        except:
            pass

    def join(self, timeout=None):
        if self._queue.full():
            if timeout:
                self._queue.not_full.wait(timeout)
                if self._queue.full():
                    return
            else:
                with self._queue.mutex:
                    self._queue.queue.clear()

        # if queue is full,
        super().join(timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.stdout.close()
        self.join()  # will wait until stdout is closed
        return self

    def run(self):
        blocksize = (
            self.nmin if self.nmin is not None else 1 if self.itemsize > 1024 else 1024
        ) * self.itemsize
        while True:
            try:
                data = self.stdout.read(blocksize)
            except:
                # stdout stream closed/FFmpeg terminated, end the thread as well
                break
            # print(f"reader thread: read {len(data)} bytes")
            if not data:
                if self.stdout.closed:  # just in case
                    break
                else:
                    break

            if self._collect:  # True until self.cooloff
                self._queue.put(data)
                # print(f"reader thread: queued samples")

    def read(self, n=-1, timeout=None):
        """read n samples

        :param n: number of samples/frames to read, if non-positive, read all, defaults to -1
        :type n: int, optional
        :param timeout: timeout in seconds, defaults to None
        :type timeout: float, optional
        :return: n*itemsize bytes
        :rtype: bytes
        """

        # wait till matching line is read by the thread
        block = self.is_alive() and n != 0
        if timeout is not None:
            timeout = time() + timeout

        arrays = []
        n_new = max(n, -n)

        # grab any leftover data from previous read
        if self._carryover:
            arrays = [self._carryover]
            if n_new != 0:
                n_new -= len(self._carryover) // self.itemsize
            self._carryover = None

        # loop till enough data are collected
        nreads = 1 if n <= 0 else max(n_new, 0)
        nr = 0
        while True:
            tout = timeout and timeout - time()
            if tout <= 0:
                break
            try:
                b = self._queue.get(block, tout)
                self._queue.task_done()
                arrays.append(b)
            except Empty:
                break

            nr += len(b) // self.itemsize
            if nr >= nreads:  # enough read
                if n < 0:
                    block = False  # keep reading until queue is empty
                else:
                    break

        # combine all the data and return requested amount
        if not len(arrays):
            return b""

        all_data = b"".join(arrays)
        if n <= 0:
            return all_data
        nbytes = self.itemsize * n
        if len(all_data) > nbytes:
            self._carryover = all_data[nbytes:]
        return all_data[:nbytes]

    def read_all(self, timeout=None):
        # wait till matching line is read by the thread
        if timeout is not None:
            timeout = time() + timeout

        arrays = arrays = [self._carryover] if self._carryover else []
        self._carryover = None

        # loop till enough data are collected
        while not self.is_alive() or timeout and timeout > time():
            try:
                data = self._queue.get(self.is_alive(), timeout and timeout - time())
                self._queue.task_done()
                arrays.append(data)
            except Empty:
                break

        # combine all the data and return requested amount
        if not len(arrays):
            return b""

        return b"".join(arrays)


class WriterThread(Thread):
    """a thread to write byte data to a writable stream

    :param stdin: stream to write data to
    :param queuesize: depth of a queue for inter-thread data transfer, defaults to None
    :param bufsize: maximum number of bytes to write at once, defaults to None (1048576 bytes)
    """

    def __init__(self, stdin: BinaryIO | NPopen, queuesize: int | None = None):
        super().__init__()
        self.stdin = stdin  #:writable stream: data sink
        self._queue = Queue(queuesize or 0)  # inter-thread data I/O

    def join(self, timeout: float | None = None):
        # close the stream if not already closed
        self.stdin.close()

        # if empty, queue a dummy item to wake up the thread
        if self._queue.empty():
            self._queue.put(None)

        # if queue is full,
        super().join(timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.join()  # will wait until stdout is closed
        return self

    def run(self):

        is_namedpipe = isinstance(self.stdin, NPopen)
        stream = self.stdin.wait() if is_namedpipe else self.stdin

        while True:
            # get next data block
            data = self._queue.get()
            self._queue.task_done()
            if data is None:
                logger.info(f"writer thread: received a sentinel to stop the writer")
                break
            else:
                logger.info(f"writer thread: received {len(data)} bytes to write")

            try:
                nwritten = 0
                nwritten = stream.write(data)
                logger.info(f"writer thread: written {nwritten} written")
            except Exception as e:
                # stdout stream closed/FFmpeg terminated, end the thread as well
                logger.info(f"writer thread exception: {e}")
                break
            if not nwritten and stream.closed:  # just in case
                logger.info(f"writer thread: somethin' else happened")
                break

        if is_namedpipe:
            self.stdin.close()

        logger.info(f"writer thread exiting")

    def write(self, data, timeout=None):
        if not self.is_alive():
            raise ThreadNotActive("WriterThread is not running")

        data = self._queue.put(data, timeout)


class AviReaderThread(Thread):
    class InvalidAviStream(FFmpegError): ...

    def __init__(self, queuesize=None):
        super().__init__()
        self.reader = AviReader()  #:utils.avi.AviReader: AVI demuxer
        self.streamsready = Event()  #:Event: Set when received stream header info
        self.rates = None  # :dict(int:int|Fraction)
        self._queue = Queue(queuesize or 0)  # inter-thread data I/O
        self._ids = None  #:dict(int:int): stream indices
        self._nread = None  #:dict(int:int): number of samples read/stream
        self._carryover = (
            None  #:dict(int:ndarray) extra data that was not previously read by user
        )

    @property
    def streams(self):
        return self.reader.streams if self.streamsready else None

    def start(self, stdout, use_ya=None):
        self._args = (stdout, use_ya)
        super().start()

    def join(self, timeout=None):
        # if queue is full,
        super().join(timeout)

    def __bool__(self):
        """True if FFmpeg stdout stream is still open or there are more frames in the buffer"""
        return self.is_alive() or not self._queue.empty()

    # def __enter__(self):
    #     self.start()
    #     return self

    # def __exit__(self, *_):
    #     self.join()  # will wait until stdout is closed
    #     return self

    def run(self):
        reader = self.reader

        try:
            # start the AVI reader to process stdout byte stream
            reader.start(*self._args)

            # initialize the stream properties
            self._ids = ids = [i for i in reader.streams]
            self._nread = {k: 0 for k in ids}
            self.rates = {
                k: v["frame_rate"] if v["type"] == "v" else v["sample_rate"]
                for k, v in reader.streams.items()
            }
        except Exception as e:
            logger.critical(e)
            return
        finally:
            self.streamsready.set()

        reader = self.reader
        for id, data in reader:
            self._queue.put((id, data))
        self._queue.put(None)  # end of stream

    def wait(self, timeout: float | None = None) -> bool:
        """wait till stream is ready to be read

        :param timeout: timeout in seconds, defaults to None (waits indefinitely)
        :type timeout: float, optional
        :raises InvalidAviStream: if thread has been terminated before stream header info was read
        :return: tuple of stream specifier and data array
        :rtype: (str, object)
        """

        flag = self.streamsready.wait(timeout)
        if not (flag or self.is_alive()):
            raise self.InvalidAviStream(
                "No stream header info was found in FFmpeg's AVI stream."
            )
        return flag

    def readchunk(self, timeout=None) -> tuple[str, object]:
        """read the next avi chunk

        :param timeout: timeout in seconds, defaults to None (waits indefinitely)
        :type timeout: float, optional
        :raises TimeoutError: if terminated due to timeout
        :return: tuple of stream specifier and data array
        """

        # wait till matching line is read by the thread
        tend = timeout and time() + timeout

        # if stream header not received in time, raise error
        if not self.wait(timeout):
            raise TimeoutError("timed out waiting for the stream headers")

        block = self.is_alive()

        # if any leftover data available, return the first one
        if self._carryover is not None:
            (id, data) = next(
                ((k, v) for k, v in self._carryover.items() if v is not None)
            )
            self._carryover[id] = None
            if all((k for k, v in self._carryover.items() if v is None)):
                self._carryover = None
            return self.reader.streams[id]["spec"], self.reader.from_bytes(id, data)

        # get next chunk
        try:
            if timeout is not None:
                timeout = tend - time()
                assert timeout > 0
            chunk = self._queue.get(block, timeout)
            if chunk is None:
                raise ThreadNotActive("reached end-of-stream")
            id, data = chunk
        except Empty:
            raise TimeoutError("timed out waiting for next chunk")
        self._queue.task_done()

        return self.reader.streams[id]["spec"], self.reader.from_bytes(id, data)

    def find_id(self, ref_stream: str) -> object:
        self.wait()
        try:
            return next(
                (k for k, v in self.reader.streams.items() if v["spec"] == ref_stream)
            )
        except:
            ValueError(f"{ref_stream} is not a valid stream specifier")

    def read(
        self, n: int = -1, ref_stream: str | None = None, timeout: float | None = None
    ) -> dict[str, bytes]:
        """read data from all streams

        :param n: number of samples, negate to non-blocking, defaults to -1
        :param ref_stream: stream specifier to count the samples,
                           defaults to None (first stream)
        :param timeout: timeout in seconds, defaults to None (waits indefinitely)
        :raises TimeoutError: if terminated due to timeout
        :return: dict of data object keyed by stream specifier string, each data object is
                 created by `bytes_to_video` or `bytes_to_image` plugin hook. If all frames
                 have been read, dict items would be all empty
        """

        # wait till matching line is read by the thread
        block = self.is_alive() and n != 0
        tend = timeout and (time() + timeout)

        # if stream header not received in time, raise error
        if not self.wait(timeout):
            raise TimeoutError("timed out waiting for the stream headers")

        # get the reference stream id
        if ref_stream is None:
            ref_stream = self._ids[0]
        else:
            ref_stream = self.find_id(ref_stream)

        # identify how many samples are needed for each stream
        nref = max(n, -n)
        tref = (self._nread[ref_stream] + nref) / self.rates[ref_stream]
        n_need = {
            k: ceil(tref * self.rates[k]) - self._nread[k] if k != ref_stream else nref
            for k in self._ids
        }
        nremain = deepcopy(n_need)

        # initialize output arrays
        arrays = {k: [] for k in self._ids}

        itemsizes = self.reader.itemsizes

        # grab any leftover data from previous read
        if self._carryover is not None:
            for k, v in self._carryover.items():
                if v is not None:
                    arrays[k] = [v]
                    nremain[k] -= len(v) // itemsizes[k]
            self._carryover = None

        # loop till enough data are collected
        while any((v > 0 for k, v in nremain.items() if n_need[k] > 0)):
            try:
                if timeout:
                    timeout = tend - time()
                    if timeout <= 0:
                        break
                chunk = self._queue.get(block, timeout)
                if chunk is None:
                    break
                k, data = chunk
                self._queue.task_done()
                arrays[k].append(data)
                nremain[k] -= len(data) // itemsizes[k]

            except Empty:
                break

        def combine(id, array, n, nr):
            # combine all the data and return requested amount
            if not len(array):
                return (id, None, None)
            all_data = b"".join(array)
            nbytes = n * itemsizes[id]
            return (
                (id, all_data, None)
                if nr >= 0
                else (id, all_data[:nbytes], all_data[nbytes:])
            )

        ids, data, excess = zip(
            *(
                combine(id, array, n_need[id], nremain[id])
                for id, array in arrays.items()
            )
        )

        # any excess samples, store as a _carryover dict
        if any((sdata is not None for sdata in excess)):
            self._carryover = {id: sdata for id, sdata in zip(ids, excess)}

        # final formatting of data
        out = {}
        for id, sdata in zip(ids, data):
            info = self.reader.streams[id]
            spec = info["spec"]
            if sdata is None:
                out[spec] = self.reader.from_bytes(id, b"")
            else:
                self._nread[id] += len(sdata) // itemsizes[id]
                out[spec] = self.reader.from_bytes(id, sdata)

        return out

    def readall(self, timeout: float | None = None) -> dict[str, bytes]:
        # wait till matching line is read by the thread
        if timeout is not None:
            timeout = time() + timeout

        # if stream header not received in time, raise error
        if not self.wait(timeout):
            raise TimeoutError("timed out waiting for the stream headers")

        # initialize output arrays
        arrays = {k: [] for k in self._ids}

        itemsizes = self.reader.itemsizes

        # grab any leftover data from previous read
        if self._carryover is not None:
            for k, v in self._carryover.items():
                if v is not None:
                    arrays[k] = [v]
                    self._nread[k] += len(v) // itemsizes[k]
            self._carryover = None

        # loop till enough data are collected
        while True:
            try:
                chunk = self._queue.get(self.is_alive(), timeout and timeout - time())
                if chunk is None:
                    break  # end of stream
                k, data = chunk
                self._queue.task_done()
                arrays[k].append(data)
                self._nread[k] += len(data) // itemsizes[k]
            except Empty:
                break

        # final formatting of data
        out = {}
        for id, sdata in arrays.items():
            info = self.reader.streams[id]
            spec = info["spec"]
            out[spec] = self.reader.from_bytes(
                id, b"" if sdata is None else b"".join(sdata)
            )

        return out


class CopyFileObjThread(Thread):
    """run shutil.copyfileobj in the thread

    :param fsrc: source file object
    :param fout: destination file object
    :param length: The integer length, if given, is the buffer size. In particular, a negative length
                    value means to copy the data without looping over the source data in chunks;
                    defaults to 0; the data is read in chunks to avoid uncontrolled memory consumption.

    Thread terminates when the copy operation is completed.

    Note that if the current file position of the fsrc object is not 0,
    only the contents from the current file position to the end of the file will be copied.
    """

    def __init__(
        self, fsrc: BinaryIO | NPopen, fdst: BinaryIO | NPopen, length: int = 0
    ):

        super().__init__()
        self._fsrc = fsrc
        self._fdst = fdst
        self.length = length

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *_):
        self.join()
        return self

    def run(self):
        src_is_namedpipe = isinstance(self._fsrc, NPopen)
        src = self._fsrc.wait() if src_is_namedpipe else self._fsrc
        dst_is_namedpipe = isinstance(self._fdst, NPopen)
        dst = self._fdst.wait() if dst_is_namedpipe else self._fdst
        copyfileobj(src, dst, self.length)
