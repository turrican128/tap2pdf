#!/usr/bin/env python3
"""tap2pdf - turn a C64 .tap into an honest dossier about what is on it.

Standard library only. The binary people download bundles nothing else, so
nothing else may be imported here.
"""
import argparse
import os
import struct
import sys
from dataclasses import dataclass, field

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


# ---------------------------------------------------------------- tapfile --
SIGNATURES = {b"C64-TAPE-RAW": "C64", b"C16-TAPE-RAW": "C16"}
PLATFORMS = {0: "C64", 1: "VIC20", 2: "C16"}
VIDEO = {0: "PAL", 1: "NTSC"}

CLOCKS = {
    ("C64", "PAL"): 985248, ("C64", "NTSC"): 1022727,
    ("C16", "PAL"): 886724, ("C16", "NTSC"): 894886,
    ("VIC20", "PAL"): 1108405, ("VIC20", "NTSC"): 1022727,
}

HEADER_SIZE = 20


@dataclass
class TapHeader:
    signature: bytes
    version: int
    platform: str
    video: str
    declared_length: int
    actual_length: int
    length_mismatch: int


def parse_header(data):
    if len(data) < HEADER_SIZE:
        raise Refusal(EXIT_NOT_TAP,
                      "too short to be a TAP file (%d bytes)" % len(data))
    sig = data[:12]
    if sig not in SIGNATURES:
        raise Refusal(EXIT_NOT_TAP, "not a TAP file: signature is %r" % sig)
    version = data[12]
    if version not in (0, 1):
        raise Refusal(EXIT_MALFORMED, "unknown TAP version %d" % version)
    platform = PLATFORMS.get(data[13], SIGNATURES[sig])
    video = VIDEO.get(data[14], "PAL")
    declared = struct.unpack("<I", data[16:20])[0]
    actual = len(data) - HEADER_SIZE
    return TapHeader(sig, version, platform, video, declared, actual,
                     actual - declared)


OVERFLOW_CYCLES = 255 * 8


@dataclass
class Pulse:
    cycles: int
    overflow: bool
    offset: int


def decode_pulses(data, header):
    """Pulses, in clock cycles.

    A non-zero byte B is B*8 cycles. A $00 byte means different things in the
    two versions, and this is the easiest thing in the format to get wrong:

      v0: 'longer than 255*8 cycles, length not recorded'. Carried as
          overflow=True with OVERFLOW_CYCLES as a floor, never as zero.
      v1: followed by three bytes, little-endian, giving the length in clock
          cycles DIRECTLY - not multiplied by 8.
    """
    body = data[HEADER_SIZE:]
    if not body:
        raise Refusal(EXIT_MALFORMED, "the TAP holds no pulse data at all")
    pulses = []
    i = 0
    n = len(body)
    while i < n:
        b = body[i]
        if b:
            pulses.append(Pulse(b * 8, False, i))
            i += 1
        elif header.version == 0:
            pulses.append(Pulse(OVERFLOW_CYCLES, True, i))
            i += 1
        else:
            if i + 3 >= n:
                raise Refusal(
                    EXIT_MALFORMED,
                    "truncated mid-pulse at offset %d: a $00 pulse needs three "
                    "more bytes and only %d remain" % (i, n - i - 1))
            pulses.append(Pulse(
                body[i + 1] | (body[i + 2] << 8) | (body[i + 3] << 16),
                False, i))
            i += 4
    if not pulses:
        raise Refusal(EXIT_MALFORMED, "the TAP holds no pulses")
    return pulses


def total_cycles(pulses):
    return sum(p.cycles for p in pulses)


def seconds(cycles, header):
    clock = CLOCKS.get((header.platform, header.video), 985248)
    return cycles / float(clock)


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
