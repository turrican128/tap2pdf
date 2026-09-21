import subprocess
import sys

from conftest import ROOT

EXAMPLES = ROOT / "examples"


def test_the_shipped_example_exists():
    for name in ("example.tap", "example-dossier.html",
                 "example-dossier.nfo"):
        assert (EXAMPLES / name).is_file(), "run tools/make_examples.py"


def test_the_shipped_example_is_current():
    # A committed artifact goes stale silently. If someone changes the
    # renderer and forgets to rebuild, the file people look at first stops
    # matching the code.
    before = {}
    for p in EXAMPLES.iterdir():
        before[p.name] = p.read_bytes()
    subprocess.run([sys.executable, str(ROOT / "tools" / "make_examples.py")],
                   check=True, capture_output=True)
    for name, data in before.items():
        assert (EXAMPLES / name).read_bytes() == data, \
            "examples/ is stale - run tools/make_examples.py and commit it"


def test_the_example_dossier_shows_an_entry_point_and_two_files():
    nfo = (EXAMPLES / "example-dossier.nfo").read_text(encoding="utf-8")
    assert "DEMO LOADER" in nfo
    assert "DEMO DATA" in nfo
    assert "SYS 49152" in nfo


def test_the_example_dossier_carries_the_not_checked_rows():
    nfo = (EXAMPLES / "example-dossier.nfo").read_text(encoding="utf-8")
    assert "NOT CHECKED" in nfo
    # The NFO hard-wraps at 78 columns, so the verdict phrase spans a line
    # break. Collapse whitespace before looking for it.
    flat = " ".join(nfo.split())
    assert "not a clean bill of health" in flat
