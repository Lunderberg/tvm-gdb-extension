#!/usr/bin/env python3

"""
Usage:

- Install debug symbols for python.  (e.g. `sudo apt install pythonX.Y-dbg`)
- Source this file in your `.gdbinit`
  `source ~/path/to/the/tvm_packedfunc_filter.py`

"""

import ctypes
import os
import re

import gdb
from gdb.FrameDecorator import FrameDecorator


class PackedFuncFilter:
    def __init__(self):
        self.name = "TVM_Ignore_PackedFunc"
        self.priority = 100
        self.enabled = True

        gdb.frame_filters[self.name] = self

    def filter(self, frame_iter):
        try:
            remove_elided = bool(int(os.environ["TVM_GDB_HIDE_ELIDED_FRAMES"]))
        except (KeyError, ValueError):
            remove_elided = False

        base_frame = None
        elided_frames = []

        for frame in frame_iter:
            to_elide = False

            pyframe = PythonFrameDecorator(frame)

            if pyframe.is_python_evalframe():
                frame = pyframe
                to_elide = any(
                    f"packages/{p}/" in pyframe.filename()
                    for p in ["_pytest", "pluggy"]
                )

            elif pyframe.is_python_frame():
                to_elide = True

            # Bottom-most frame should always be visible
            if base_frame is None:
                base_frame = frame
                continue

            # Skip any packed_func.h or std::function frame
            filenames_to_skip = ["packed_func.h", "std_function.h"]
            if frame.filename() and any(
                skip in frame.filename() for skip in filenames_to_skip
            ):
                to_elide = True

            # Packed function through the C API
            functions_to_skip = [
                "TVMFuncCall(TVMFunctionHandle, TVMValue*, int*, int, TVMValue*, int*)"
            ]
            if frame.function() in functions_to_skip:
                to_elide = True

            # Intermediate calls when traversing a graph.
            if isinstance(frame.function(), str) and "InitVTable" in frame.function():
                to_elide = True

            if to_elide:
                if not remove_elided:
                    # Append to the list of elided frames
                    elided_frames.append(frame)
            else:
                # Now we're ready to show something, with the base frames
                # and any packed_func after it.  Clear out the base_frame
                # to be reset next time.
                if elided_frames:
                    yield ElideDecorator(base_frame, elided_frames)
                else:
                    yield base_frame
                base_frame = frame
                elided_frames = []

        # Clear out the last item, if needed
        if base_frame:
            if elided_frames:
                yield ElideDecorator(base_frame, elided_frames)
            else:
                yield base_frame


class ElideDecorator(FrameDecorator):
    def __init__(self, frame, elided_frames):
        super().__init__(frame)
        self.frame = frame
        self.elided_frames = elided_frames

    def elided(self):
        return iter(self.elided_frames)


class PythonFrameDecorator(FrameDecorator):
    def __init__(self, frame):
        super().__init__(frame)
        self.frame = frame

        # Unwrap any other decorators that may have been applied
        self.gdbframe = frame
        while not isinstance(self.gdbframe, gdb.Frame):
            self.gdbframe = self.gdbframe.inferior_frame()

    def is_python_evalframe(self):
        """Check if this is a pythhon stack frame

        Returns True if the stack frame is a C++ frame that
        corresponds to a Python stack frame
        """

        # python3.8-gdb.py looks for "_PyEval_EvalFrameDefault", but
        # that has arguments optimized out on ubuntu 20.04.  Instead,
        # let's use PyEval_EvalFrameEx instead.  This is part of the
        # CPython API, so it should be more stable to find.
        return self.gdbframe.name() == "PyEval_EvalFrameEx"

    def is_python_frame(self):
        """Check if this stack frame is owned by CPython

        Returns True if the stack frame is part of the python
        executable or libpython.so.  May erroneously return False if
        the frame is a CPython frame included statically in another
        executable using libpython.a

        """

        # Find the file that contains the current instruction pointer.
        shared_lib_name = gdb.solib_name(self.gdbframe.pc())
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

    def filename(self):
        try:
            return self.pyop.filename()
        except AttributeError:
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
        args = gdb.FrameDecorator.FrameVars(self.gdbframe).fetch_frame_args()
        for wrap in args:
            sym = wrap.sym
            if str(sym.type) == "PyFrameObject *":
                break
        else:
            return self.pyop.f_lineno

        # Pull out the value of that argument.
        val = sym.value(self.gdbframe)
        pointer = int(val)

        # Call PyFrame_GetLineNumber in the inferior, using whichever
        # pointer was found as an argument.  The cast of the function
        # pointer is a workaround for incorrect debug symbols
        # (observed in python3.7-dbg in ubuntu 18.04,
        # PyFrame_GetLineNumber showed 4 arguments instead of 1).
        line_num = gdb.parse_and_eval(
            "((int (*)(PyFrameObject*))PyFrame_GetLineNumber)((PyFrameObject*){})".format(
                int(pointer)
            )
        )
        return int(line_num)

    def frame_args(self):
        # TODO: Extract python arguments to print here.
        # python3.8-dbg.py provides pyop.iter_locals(), though that
        # needs to be run in the _PyEval_EvalFrameDefault gdb frame.
        # It doesn't distinguish between arguments and local
        # variables, but that should be possible to determine, because
        # inspect.getargvalues is a thing that exists.
        return None

    def function(self):
        try:
            return self.pyop.co_name.proxyval(set())
        except AttributeError:
            return "Unknown python function"

    def address(self):
        return None


PackedFuncFilter()
