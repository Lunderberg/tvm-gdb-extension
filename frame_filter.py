#!/usr/bin/env python3

"""
Usage:

- Install debug symbols for python.  (e.g. `sudo apt install pythonX.Y-dbg`)
- Source this file in your `.gdbinit`
  `source ~/path/to/the/tvm_packedfunc_filter.py`

"""

import ctypes
import enum
import itertools
import os
import re

from abc import abstractmethod
from typing import Union

from .utils import _unwrap_frame, StacktraceOnRaise

import gdb
from gdb.FrameDecorator import FrameDecorator


class FilterLevel(enum.Flag):
    Disabled = 0
    Interpreter = enum.auto()
    Pytest = enum.auto()
    Dispatch = enum.auto()
    Default = Interpreter | Pytest | Dispatch
    CommonBaseClass = enum.auto()
    All = Default | CommonBaseClass


class FrameFilter:
    @classmethod
    def register(cls, filter_level):
        for subclass in cls._filters:
            filter = subclass()
            filter.enabled = filter_level & subclass.filter_level
            gdb.frame_filters[filter.name] = filter

    _filters = []

    def __init_subclass__(cls, /, filter_level, priority=100, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.filter_level = filter_level
        cls.priority = priority
        FrameFilter._filters.append(cls)

    def __init__(self):
        self.name = "TVM_" + type(self).__name__
        self.priority = type(self).priority
        self.enabled = True


class PythonFrameFilter(FrameFilter, filter_level=FilterLevel.Interpreter):
    def __init__(self):
        self.name = "TVM_Python_Filter"
        self.priority = 100
        self.enabled = True

    def filter(self, frame_iter):
        with StacktraceOnRaise():
            python_frames = []
            prev_evalframe = None

            for frame in frame_iter:
                is_python = self.is_python_frame(frame)
                is_evalframe = is_python and self.is_python_evalframe(frame)

                if is_python and is_evalframe and prev_evalframe is None:
                    prev_evalframe = frame
                    python_frames.append(frame)
                elif is_python and is_evalframe and prev_evalframe is not None:
                    yield PythonFrameDecorator(prev_evalframe, python_frames)
                    python_frames = [frame]
                    prev_evalframe = frame
                elif is_python and not is_evalframe:
                    python_frames.append(frame)
                elif not is_python and python_frames and prev_evalframe is not None:
                    yield PythonFrameDecorator(prev_evalframe, python_frames)
                    python_frames = []
                    prev_evalframe = None
                    yield frame
                elif not is_python and python_frames and prev_evalframe is None:
                    yield from python_frames
                    python_frames = []
                    yield frame
                elif not is_python and not python_frames:
                    yield frame

    @staticmethod
    def is_python_frame(frame):
        """Check if this stack frame is owned by CPython

        Returns True if the stack frame is part of the python
        executable or libpython.so.  May erroneously return False if
        the frame is a CPython frame included statically in another
        executable using libpython.a
        """

        frame = _unwrap_frame(frame)

        # Find the file that contains the current instruction pointer.

        shared_lib_name = gdb.solib_name(frame.pc())
        prog_name = gdb.current_progspace().filename
        if shared_lib_name:
            obj_filepath = shared_lib_name
        else:
            obj_filepath = prog_name

        obj_filename = os.path.basename(obj_filepath)

        # Check for pythonX.Y, and debug versions pythonX.Yd
        is_python_exe = bool(re.match(r"^python\d+\.\d+d?$", obj_filename))
        # Check for libpythonX.Y.so, libpythonX.Yd.so, with optional versioning
        is_libpython = bool(re.match(r"^libpython\d+\.\d+d?\.so(\.\d)*$", obj_filename))
        # Check for cpython compiled modules (e.g. _ctypes.cpython-38-x86_64-linux-gnu.so)
        is_cpython_module = "cpython" in obj_filename
        # Check for libffi.so, with optional versioning
        is_ffi = bool(re.match(r"^libffi.so(\.\d)*$", obj_filename))

        is_python = is_python_exe or is_libpython or is_cpython_module or is_ffi

        return is_python

    @staticmethod
    def is_python_evalframe(frame):
        """Check if this is a python stack frame

        Returns True if the stack frame is a C++ frame that
        corresponds to a Python stack frame
        """
        # python3.8-gdb.py looks for "_PyEval_EvalFrameDefault", but
        # that has arguments optimized out on ubuntu 20.04.  Instead,
        # let's use PyEval_EvalFrameEx instead.  This is part of the
        # CPython API, so it should be more stable to find.
        return _unwrap_frame(frame).name() == "_PyEval_EvalFrameDefault"


class PythonFrameDecorator(FrameDecorator):
    def __init__(self, evalframe, frames):
        super().__init__(evalframe)
        self.frame = evalframe
        self.gdbframe = _unwrap_frame(evalframe)
        self._elided = frames

    @StacktraceOnRaise()
    def elided(self):
        return self._elided

    @property
    def pyop(self):
        if hasattr(self, "_pyop"):
            return self._pyop

        # All gdb python extensions run in the same __main__
        # namespace.  This Frame object is defined by python*-gdb.py,
        # and gives some utilities for interacting with python stack
        # frames.  Repeat the checks for it, in case the
        # python*-gdb.py helpers were loaded since the last time.
        PyFrame = globals().get("Frame", None)

        if PyFrame is None:
            return

        pyframe = PyFrame(self.gdbframe)
        self._pyop = pyframe.get_pyop()
        return self._pyop

    @StacktraceOnRaise()
    def get_pyframe_argument(self):
        frames_to_check = [self.gdbframe, self.gdbframe.older()]

        def symbols(frame):
            frame_vars = gdb.FrameDecorator.FrameVars(frame)
            for wrapper in frame_vars.fetch_frame_args():
                yield wrapper.sym
            for wrapper in frame_vars.fetch_frame_locals():
                yield wrapper.sym

        for frame in frames_to_check:
            for sym in symbols(frame):
                if str(sym.type) == "PyFrameObject *":
                    val = sym.value(frame)
                    if not val.is_optimized_out:
                        pointer = int(val)
                        return pointer

        return None

    @StacktraceOnRaise()
    def filename(self):
        pyframe = self.get_pyframe_argument()
        if pyframe is not None:
            result = gdb.parse_and_eval(
                f"PyUnicode_AsUTF8(((PyFrameObject*){pyframe})->f_code->co_filename)"
            )
            return result.string()

        if self.pyop is not None:
            return self.pyop.filename()

        return "Unknown python file"

    def line(self):
        # This is what py-bt uses, which can return line numbers
        # outside of the length of the file.
        # return self.pyop.current_line_num()

        # This gives the line number of the start of the function.
        # Closer, but still not there.
        # return self.pyop.f_lineno

        # Instead, evil dirty hackery.

        # Look for the an argument passed in that has type
        # PyFrameObject*.  If it can't be found, fall back to a
        # slightly incorrect line number.
        pyframe = self.get_pyframe_argument()
        if pyframe is not None:
            # Call PyFrame_GetLineNumber in the inferior, using whichever
            # pointer was found as an argument.  The cast of the function
            # pointer is a workaround for incorrect debug symbols
            # (observed in python3.7-dbg in ubuntu 18.04,
            # PyFrame_GetLineNumber showed 4 arguments instead of 1).
            line_num = gdb.parse_and_eval(
                f"((int (*)(PyFrameObject*))PyFrame_GetLineNumber)((PyFrameObject*){pyframe})"
            )
            return int(line_num)

        if self.pyop is not None:
            return self.pyop.f_lineno

        return None

    @StacktraceOnRaise()
    def frame_args(self):
        # TODO: Extract python arguments to print here.
        # python3.8-dbg.py provides pyop.iter_locals(), though that
        # needs to be run in the _PyEval_EvalFrameDefault gdb frame.
        # It doesn't distinguish between arguments and local
        # variables, but that should be possible to determine, because
        # inspect.getargvalues is a thing that exists.
        return None

    @StacktraceOnRaise()
    def function(self):
        pyframe = self.get_pyframe_argument()
        if pyframe is not None:
            result = gdb.parse_and_eval(
                f"PyUnicode_AsUTF8(((PyFrameObject*){pyframe})->f_code->co_name)"
            )
            return result.string()

        if self.pyop is not None:
            return self.pyop.co_name.proxyval(set())

        return "Unknown python function"

    @StacktraceOnRaise()
    def address(self):
        return None


class ElideFilter(FrameFilter, filter_level=FilterLevel.Disabled):
    @abstractmethod
    def _elide_frame(self, frame: Union[gdb.Frame, FrameDecorator]) -> bool:
        """Whether the frame should be elided.

        Return true if the frame should be elided as part of the previous,
        false otherwise.
        """

    class ElidedFrameDecorator(FrameDecorator):
        def __init__(self, frame, elided):
            super().__init__(frame)
            self._elided = elided

        def elided(self):
            return self._elided

    def filter(self, frame_iter):
        prev_nonelided_frame = None
        elided_frames = []

        def yield_elided():
            nonlocal prev_nonelided_frame
            nonlocal elided_frames

            if elided_frames and prev_nonelided_frame is None:
                yield from elided_frames
                elided_frames = []
            elif elided_frames and prev_nonelided_frame is not None:
                yield self.ElidedFrameDecorator(prev_nonelided_frame, elided_frames)
                prev_nonelided_frame = None
                elided_frames = []
            elif not elided_frames and prev_nonelided_frame is not None:
                yield prev_nonelided_frame
                prev_nonelided_frame = None

        for frame in frame_iter:
            is_elided = self._elide_frame(frame)
            if is_elided:
                elided_frames.append(frame)
            else:
                yield from yield_elided()
                prev_nonelided_frame = frame

        yield from yield_elided()


class PytestFrameFilter(ElideFilter, filter_level=FilterLevel.Pytest, priority=90):
    def _elide_frame(self, frame):
        filename = frame.filename()
        for package in ["_pytest", "pluggy"]:
            if f"packages/{package}/" in filename:
                return True
        else:
            return False


class PackedFuncFilter(ElideFilter, filter_level=FilterLevel.Dispatch):
    def _elide_frame(self, frame):
        packed_func_c_api = (
            "TVMFuncCall(TVMFunctionHandle, TVMValue*, int*, int, TVMValue*, int*)"
        )
        return (
            frame.function() == packed_func_c_api or "packed_func.h" in frame.filename()
        )


class FunctorDispatchFilter(ElideFilter, filter_level=FilterLevel.Dispatch):
    def _elide_frame(self, frame):
        regices = [
            r"tvm::.*Functor<.*>::operator\(\)",
            r"tvm::.*Functor<.*>::Visit",
            r"tvm::.*Functor<.*>::InitVTable",
            r"tvm::tir::StmtExprVisitor::VisitExpr",
            r"tvm::tir::StmtExprMutator::VisitExpr",
        ]

        function = frame.function()
        return any(re.search(regex, function) for regex in regices)


class TransformationBaseClassFilter(
    ElideFilter, filter_level=FilterLevel.CommonBaseClass
):
    def _elide_frame(self, frame):
        return frame.function() in [
            "tvm::tir::transform::PrimFuncPassNode::operator()(tvm::IRModule, tvm::transform::PassContext const&) const",
            "tvm::transform::Pass::operator()(tvm::IRModule, tvm::transform::PassContext const&) const",
            "tvm::transform::SequentialNode::operator()(tvm::IRModule, tvm::transform::PassContext const&) const",
            "tvm::transform::Pass::operator()(tvm::IRModule, tvm::transform::PassContext const&) const",
            "tvm::transform::Pass::operator()(tvm::IRModule) const",
        ]


class StmtExprVisitorFilter(ElideFilter, filter_level=FilterLevel.CommonBaseClass):
    def _elide_frame(self, frame):
        regex = r"(((Expr|Stmt)(Visitor|Mutator))|(IR(Visitor|Mutator)WithAnalyzer))::Visit(Stmt|Expr)_"

        return re.search(regex, frame.function())
