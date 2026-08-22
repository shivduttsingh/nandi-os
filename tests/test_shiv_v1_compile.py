import py_compile
from pathlib import Path


def test_shiv_entrypoint_and_modules_compile():
    for path in (
        Path("shiv_app.py"),
        Path("shiv_v1/engine.py"),
        Path("shiv_v1/history.py"),
        Path("shiv_v1/ui.py"),
        Path("shiv_v1/after_hours.py"),
    ):
        py_compile.compile(str(path), doraise=True)
