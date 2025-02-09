try:
    from math import prod
except:
    from functools import reduce
    from operator import mul

    prod = lambda seq: reduce(mul, seq, 1)

from builtins import zip as builtin_zip


def zip(*args, strict=False):

    # backwards compatibility for pre-py3.10

    try:
        return builtin_zip(*args, strict=strict)
    except TypeError:
        if strict is False:
            return builtin_zip(*args)

    def strict_zip():
        # strict=True case, excerpted from PEP618: https://peps.python.org/pep-0618/
        iterators = tuple(iter(iterable) for iterable in args)
        try:
            while True:
                items = []
                for iterator in iterators:
                    items.append(next(iterator))
                yield tuple(items)
        except StopIteration:
            pass

        if items:
            i = len(items)
            plural = " " if i == 1 else "s 1-"
            msg = f"zip() argument {i+1} is shorter than argument{plural}{i}"
            raise ValueError(msg)
        sentinel = object()
        for i, iterator in enumerate(iterators[1:], 1):
            if next(iterator, sentinel) is not sentinel:
                plural = " " if i == 1 else "s 1-"
                msg = f"zip() argument {i+1} is longer than argument{plural}{i}"
                raise ValueError(msg)

    return strict_zip()


def dtype_itemsize(dtype):
    return int(dtype[-1])


def get_samplesize(shape, dtype):
    return prod(shape) * dtype_itemsize(dtype)


def deprecate_core():
    from importlib import metadata
    import warnings

    try:
        metadata.version("ffmpegio-core")
    except metadata.PackageNotFoundError:
        return

    warnings.warn(
        message="!!PACKAGE CONFLICT!! ffmpegio-core distribution package has been deprecated. Please read the following link for the instructions: https://github.com/python-ffmpegio/python-ffmpegio/wiki/Instructions-to-upgrade-to-v0.11.0."
    )
