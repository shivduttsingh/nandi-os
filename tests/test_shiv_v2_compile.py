import py_compile
from pathlib import Path


def test_shiv_v2_entrypoint_and_modules_compile():
    for path in (
        Path("shiv_app.py"),
        Path("shiv_v2/strategy.py"),
        Path("shiv_v2/replay.py"),
        Path("shiv_v2/history.py"),
        Path("shiv_v2/ui.py"),
    ):
        py_compile.compile(str(path), doraise=True)
