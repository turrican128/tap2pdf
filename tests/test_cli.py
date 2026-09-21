import subprocess
import sys

from conftest import ROOT

TOOL = ROOT / "tap2pdf.py"


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True)


def test_version_prints_and_exits_zero():
    r = run("--version")
    assert r.returncode == 0
    assert r.stdout.strip().startswith("tap2pdf ")


def test_no_arguments_is_a_usage_error():
    r = run()
    assert r.returncode == 1


def test_missing_input_file_exits_2():
    r = run("no-such-tape.tap")
    assert r.returncode == 2
    assert "no-such-tape.tap" in (r.stderr + r.stdout)
