import importlib.util

if importlib.util.find_spec("httpx") is None:  # the wrapper's only extra dependency
    collect_ignore_glob = ["test_*.py"]
