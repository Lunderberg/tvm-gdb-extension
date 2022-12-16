import enum
import os

import gdb

from . import tvm_pretty_print
from .frame_filter import FrameFilter, FilterLevel


def env_var_flag(name):
    var = os.environ.get(name, "").strip()
    if var.strip():
        return bool(int(var))
    else:
        return False


def main():
    # remove_elided = env_var_flag("TVM_GDB_HIDE_ELIDED_FRAMES")

    filter_level = os.environ.get("TVM_GDB_FILTER_LEVEL")
    if filter_level:
        try:
            filter_level = FilterLevel[filter_level.capitalize()]
        except KeyError:
            filter_level = FilterLevel(int(filter_level))
    else:
        filter_level = FilterLevel.Dispatch | FilterLevel.Interpreter

    FrameFilter.register(filter_level)
