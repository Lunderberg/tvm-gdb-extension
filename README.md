## Goal

[TVM](https://github.com/apache/tvm) makes frequent use of packed
functions, which may be implemented in any language supported by the
FFI.  This can cause stack traces to be difficult to read, as there
may be several stack frames devoted to the FFI or to CPython calls
that do not represent the issue being debugged.  This gdb extension
exists to hide those stack frames, allowing the actual flow of the
program to be more easily traced.

## Usage

- Install debug symbols for python.  (e.g. `sudo apt install pythonX.Y-dbg`)
- Source this file in your `.gdbinit`
  `source ~/path/to/the/tvm_packedfunc_filter.py`

## Known Configuration Issues

- Why are Python/PackedFunc frames still displayed, but indented?

  gdb indents elided frames by default, but still displays them.  With
  gdb 9.1 or higher, elided frames can be hidden using `bt -hide`.  For
  earlier versions of gdb, or to hide the elided frames by default,
  set the environment variable `TVM_GDB_HIDE_ELIDED_FRAMES` to a
  non-zero integer.

- Why are python stack frames not being displayed?

  Python must have debug symbols available in order to identify python
  calls, either embedded in the file or as a separate debug file.

  - To check if debug symbols are available in the python executable,
    run `objdump --syms $(which python3) | grep .debug_info` If no
    results are printed, debug symbols are missing from the
    executable.

  - To check if debug symbols are available in a separate file, first
    find the name of the debug file using
    `readelf --string-dump .gnu_debuglink $(which python3)`.
    Then, find the gdb debug dir using `gdb --config`.  Finally, check
    if the file exists in the gdb debug-dir.  These steps can be done
    with the following bash command.

    ```bash
    find "$(gdb --config | grep with-separate-debug-dir | \
            sed 's/\s\+--with-separate-debug-dir=//g; s/\s\+(relocatable)//g;')" \
        -name $(readelf --string-dump .gnu_debuglink $(which python3) | \
                grep -o "[0-9a-f]\+.debug")
    ```

    If no results are printed, the separate debug file is not automatically
    loaded.

  - If your version of gdb is not the system version of gdb, you may
    need to recompile with `./configure --with-separate-debug-dir=/usr/lib/debug`


- Why are python stack frames displayed as
  `Unknown python function () at Unknown python file`?

  The gdb extensions provided by python (e.g. python3.8-gdb.py) must
  be locatable by gdb.  These are typically stored in
  `/usr/share/gdb/auto-load`.

  If your version of gdb is not the system version of gdb, you may
  need to recompile with `./configure --with-auto-load=/usr/share/gdb/auto-load`
