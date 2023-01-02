import functools
import os

import gdb

from .tvm_pretty_print import PrettyPrinter, PrettyPrintLevel
from .frame_filter import FrameFilter, FilterLevel


def parse(cls, string):
    try:
        as_int = int(string)
    except ValueError:
        pass
    else:
        return cls(as_int)

    output = cls.Disabled
    for part in string.split("|"):
        part = cls[part.strip().capitalize()]
        output = output | part
    return output


def env_var_flag(cls, env_var):
    filter_level = os.environ.get(env_var)
    if filter_level:
        return parse(cls, filter_level)
    else:
        return cls.Default


def main():
    filter_level = env_var_flag(FilterLevel, "TVM_GDB_FILTER_LEVEL")
    pprint_level = env_var_flag(PrettyPrintLevel, "TVM_GDB_PRETTY_PRINT")

    FrameFilter.register(filter_level)
    PrettyPrinter.register(pprint_level)
