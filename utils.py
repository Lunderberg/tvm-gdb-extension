import functools
import traceback

import gdb


def _unwrap_frame(frame):
    while not isinstance(frame, gdb.Frame):
        frame = frame.inferior_frame()
    return frame


class StacktraceOnRaise:
    """Print a stack trace when leaving a scope by a raised exception

    gdb doesn't print the stacktrace when an exception is raised from
    a plugin.  If necessary, will need to print it ourselves.
    """

    def __call__(self, func):
        @functools.wraps(func)
        def inner(*args, **kwargs):
            with self:
                return func(*args, **kwargs)

        return inner

    def __enter__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_traceback):
        if exc_val is not None:
            traceback.print_exc()
