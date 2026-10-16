"""Runs every scenario script in tests/scenarios as its own test, at two simulated screen scales.

The scenarios use tests/fake_tk.py, a tiny stand-in for Tkinter, so no display (and no Tkinter install)
is needed. They check the registration rules, the screens' wiring and the button handlers - not how
the window looks. Do a quick visual check by hand before releasing.

    python -m unittest discover -s tests -v
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

SCENARIOS = Path(__file__).parent / "scenarios"


class Scenarios(unittest.TestCase):
    pass


def _make(path, dpi):
    def test(self):
        env = dict(os.environ, FPI=str(dpi))
        run = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, env=env, timeout=180)
        self.assertEqual(run.returncode, 0, (run.stdout[-1500:] + run.stderr[-1500:]))
    return test


for _path in sorted(SCENARIOS.glob("scenario_*.py")):
    for _dpi in (96, 144):
        setattr(Scenarios, f"test_{_path.stem[len('scenario_'):]}_{_dpi}dpi", _make(_path, _dpi))

if __name__ == "__main__":
    unittest.main()
