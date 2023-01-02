#!/usr/bin/env python3

"""Pretty-prints any TVM ObjectRef objects

Usage:

- Source this file in your `.gdbinit`
  `source ~/path/to/the/tvm_pretty_print.py`

TODO:

- Longer string result from gdb.parse_and_eval, some objects get
  truncated.

"""

import enum
from abc import abstractmethod
from typing import Optional

import gdb


class PrettyPrintLevel(enum.Flag):
    Disabled = 0
    DataType = enum.auto()
    ObjectRef = enum.auto()
    Default = DataType
    All = Default | ObjectRef


class PrettyPrinter:
    _printers = []

    def __init_subclass__(cls, /, pprint_level):
        cls.pprint_level = pprint_level
        PrettyPrinter._printers.append(cls)

    @classmethod
    def register(cls, pprint_level):
        for subclass in cls._printers:
            if pprint_level & subclass.pprint_level:
                gdb.pretty_printers.append(subclass.lookup)

    @classmethod
    @abstractmethod
    def lookup(cls, val) -> Optional["Self"]:
        """Return the printer that should print the value"""

    @abstractmethod
    def to_string(self) -> str:
        """Convert the value to a string"""


class TVM_ObjectRef(PrettyPrinter, pprint_level=PrettyPrintLevel.ObjectRef):
    @classmethod
    def lookup(cls, val):
        if val.type.code == gdb.TYPE_CODE_PTR:
            obj = val.referenced_value()
        elif val.type.code == gdb.TYPE_CODE_STRUCT:
            obj = val
        else:
            return

        try:
            object_ref_type = gdb.lookup_type("::tvm::runtime::ObjectRef")
        except Exception as e:
            # TVM not loaded, so don't use this printer
            return

        ptr_type = object_ref_type.const().pointer()
        try:
            as_objref_pointer = obj.address.dynamic_cast(ptr_type)
            return cls(as_objref_pointer)
        except gdb.error:
            # Not a subclass of ObjectRef, so don't use this printer
            return

    def __init__(self, pointer):
        self.pointer = pointer

    def to_string(self):
        command = "::tvm::PrettyPrint(*(::tvm::runtime::ObjectRef*){}).c_str()".format(
            int(self.pointer)
        )
        # TODO: Figure out better handling during segfaults, not safe
        # to make calls into tvm at that point.

        # TODO: No string length limit on parse_and_eval
        output = gdb.parse_and_eval(command)
        as_str = str(output)
        parsed = (
            as_str[as_str.find('"') : as_str.rfind('"') + 1]
            .encode("ascii")
            .decode("unicode_escape")
        )
        return parsed


class TVM_DataType(PrettyPrinter, pprint_level=PrettyPrintLevel.DataType):
    @classmethod
    def lookup(cls, val):
        try:
            datatype_type = gdb.lookup_type("::tvm::runtime::DataType")
        except Exception as e:
            # TVM not loaded, so don't use this printer
            return

        if val.type == datatype_type:
            return cls(val)

    def __init__(self, val):
        self.val = val

    def to_string(self):
        data = self.val[self.val.type.fields()[0]]
        data_fields = data.type.fields()
        values = {
            field.name: int(data[field].format_string(format="d"))
            for field in data.type.fields()
        }

        import tvm

        # Can't construct directly from the type_code/bits/lanes, but
        # can set them afterwards.
        dtype = tvm.DataType("int")
        dtype.type_code = values["code"]
        dtype.bits = values["bits"]
        dtype.lanes = values["lanes"]

        return repr(dtype)
