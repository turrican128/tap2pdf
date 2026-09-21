#!/usr/bin/env python3
"""tap2pdf - turn a C64 .tap into an honest dossier about what is on it.

Standard library only. The binary people download bundles nothing else, so
nothing else may be imported here.
"""
import argparse
import os
import sys

__version__ = "0.1.0-dev"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_INPUT = 2
EXIT_NOT_TAP = 3
EXIT_MALFORMED = 4
EXIT_OUTPUT = 5
EXIT_ENRICH = 6


class Refusal(Exception):
    """A refusal that maps to a documented exit code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# -------------------------------------------------------------------- cli --
class _Parser(argparse.ArgumentParser):
    """argparse exits 2 on a usage error; our documented contract says 2 means
    'input unreadable' and 1 means 'usage'. The published exit codes are the
    promise, so argparse conforms to them rather than the other way round.
    --help and --version still exit 0."""

    def exit(self, status=0, message=None):
        if message:
            self._print_message(message, sys.stderr)
        super().exit(EXIT_USAGE if status == 2 else status)


def build_parser():
    p = _Parser(
        prog="tap2pdf",
        description="Turn a C64 .tap into a self-contained HTML dossier.")
    p.add_argument("tape", help="the .tap file to examine")
    p.add_argument("-o", "--output", help="output HTML path")
    p.add_argument("--pdf", action="store_true", help="also render a PDF")
    p.add_argument("--pdf-only", action="store_true",
                   help="render the PDF and remove the intermediate HTML")
    p.add_argument("--nfo", action="store_true",
                   help="also write a plain-ASCII .nfo")
    p.add_argument("--tapclean", metavar="FILE",
                   help="a TAPClean report to ingest for loader names")
    p.add_argument("--vice", metavar="PATH",
                   help="x64sc, for the title screenshot")
    p.add_argument("--browser", metavar="PATH",
                   help="headless Edge/Chrome to use for --pdf")
    p.add_argument("--extract", metavar="DIR",
                   help="write the recovered PRGs here")
    p.add_argument("--pal", action="store_true", help="force PAL timing")
    p.add_argument("--ntsc", action="store_true", help="force NTSC timing")
    p.add_argument("--title", help="override the document title")
    p.add_argument("--quiet", action="store_true",
                   help="no progress on stderr")
    p.add_argument("--version", action="version",
                   version="tap2pdf " + __version__)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if not os.path.isfile(args.tape):
            raise Refusal(EXIT_INPUT, "cannot read: " + args.tape)
        return EXIT_OK
    except Refusal as r:
        sys.stderr.write("tap2pdf: " + r.message + "\n")
        return r.code


if __name__ == "__main__":
    sys.exit(main())
