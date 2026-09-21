#!/usr/bin/env python3
"""Rebuild the shipped example dossier.

The example tape is synthetic, like every tape in this repo. It carries two
files so the memory map has something to show, and a BASIC stub so the entry
point is real rather than absent.

CI re-runs this and fails if the committed dossier has drifted: a committed
artifact goes stale silently otherwise, and the file people look at first
stops matching the code.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import make_fixtures as mf  # noqa: E402
import tap2pdf  # noqa: E402

EXAMPLES = ROOT / "examples"


def build_tape():
    """A loader at $0801 that SYSes into a payload at $C000."""
    loader = mf.basic_sys(49152)
    payload = bytes(range(0x00, 0x80)) * 2
    return mf.make_tap(
        mf.cbm_file("DEMO LOADER", 0x0801, loader, ftype=1)
        + mf.cbm_file("DEMO DATA", 0xC000, payload))


def main():
    EXAMPLES.mkdir(parents=True, exist_ok=True)
    tape = EXAMPLES / "example.tap"
    tape.write_bytes(build_tape())

    args = tap2pdf.build_parser().parse_args([str(tape)])
    dossier = tap2pdf.analyse(tape.read_bytes(), args)

    # open(), not Path.write_text(newline=...): that keyword only exists from
    # Python 3.10, and 3.9 is the floor this project promises. Local Python
    # was newer, so only the CI matrix caught it.
    html = EXAMPLES / "example-dossier.html"
    with open(str(html), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tap2pdf.render_html(dossier))
    nfo = EXAMPLES / "example-dossier.nfo"
    with open(str(nfo), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(tap2pdf.render_nfo(dossier))

    for p in (tape, html, nfo):
        print("%-28s %7d bytes" % (p.name, p.stat().st_size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
