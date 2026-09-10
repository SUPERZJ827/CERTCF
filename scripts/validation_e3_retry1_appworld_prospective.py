#!/usr/bin/env python3
"""E3 retry1 launcher using the recovered frozen AppWorld environment."""
from __future__ import annotations

import runpy
from pathlib import Path

LAUNCHER = Path(__file__).resolve()
ROOT = LAUNCHER.parents[1]
source = ROOT / "scripts" / "validation_e3_appworld_prospective.py"
code = source.read_text()
code = code.replace('"validation_e3"', '"validation_e3_retry1"')
code = code.replace("phase_e3_", "phase_e3_retry1_")
globals_dict = {"__name__": "__main__", "__file__": str(LAUNCHER)}
exec(compile(code, str(LAUNCHER), "exec"), globals_dict)
