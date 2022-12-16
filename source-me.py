def import_this_dir():
    import importlib.machinery
    import importlib.util
    import pathlib
    import sys

    filepath = pathlib.Path(__file__).expanduser().resolve().with_name("__init__.py")
    assert filepath.exists()
    name = "tvm_gdb_extensions"

    # Re-sourcing the file should reload the extensions, even if
    # changes have been made.  Therefore, remove python's cache.
    to_remove = [mod_name for mod_name in sys.modules if mod_name.startswith(name)]
    for mod_name in to_remove:
        del sys.modules[mod_name]

    loader = importlib.machinery.SourceFileLoader(name, str(filepath))
    spec = importlib.util.spec_from_loader(name, loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


import_this_dir().main()

# Anything defined here remains in the namespace used by gdb.
# Therefore, cleanup to avoid polluting that namespace.
del import_this_dir
