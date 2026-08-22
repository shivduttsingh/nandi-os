from __future__ import annotations

from pathlib import Path


def test_shiv_after_hours_modules_compile() -> None:
    for path in (Path("shiv_app.py"), Path("shiv_v1/after_hours.py")):
        compile(path.read_text(encoding="utf-8"), str(path), "exec")
