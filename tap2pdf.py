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


# --------------------------------------------------------------- classify --
CBM_SHORT, CBM_MEDIUM, CBM_LONG = 0x30, 0x42, 0x56
CBM_TOLERANCE = 6
WINDOW = 256


@dataclass
class Cluster:
    center: int
    count: int
    share: float


@dataclass
class Region:
    kind: str
    start_index: int
    end_index: int
    pulse_count: int
    cycles: int
    clusters: list = field(default_factory=list)


def histogram(pulses):
    counts = {}
    for p in pulses:
        key = min(255, p.cycles // 8)
        counts[key] = counts.get(key, 0) + 1
    return counts


def find_clusters(hist, min_share=0.02):
    """Group neighbouring pulse widths. Tape speed wobbles, so one logical
    width shows up spread across a few adjacent byte values."""
    total = sum(hist.values()) or 1
    groups = []
    for value in sorted(hist):
        if groups and value - groups[-1][-1] <= 2:
            groups[-1].append(value)
        else:
            groups.append([value])
    out = []
    for group in groups:
        count = sum(hist[v] for v in group)
        share = count / float(total)
        if share < min_share:
            continue
        peak = max(group, key=lambda v: hist[v])
        out.append(Cluster(peak, count, share))
    return sorted(out, key=lambda c: c.center)


def looks_like_cbm(clusters):
    """All three ROM widths present. Used to judge a whole tape."""
    centers = [c.center for c in clusters]
    return all(any(abs(c - want) <= CBM_TOLERANCE for c in centers)
               for want in (CBM_SHORT, CBM_MEDIUM, CBM_LONG))


def _near_cbm(center):
    for want in (CBM_SHORT, CBM_MEDIUM, CBM_LONG):
        if abs(center - want) <= CBM_TOLERANCE:
            return want
    return None


def _window_is_cbm(clusters):
    """Judge one window, where the long pulse may be too rare to survive.

    A long pulse occurs once per byte - about 5% of pulses - so in a short
    window it often falls below the noise threshold and only the short and
    medium widths remain. Requiring all three here made a plain ROM tape
    segment into alternating cbm/turbo bands, which invents turbo regions
    that do not exist. So: every cluster must be a CBM width, and short and
    medium must both be present. The long one is welcome but not required.
    """
    matched = [_near_cbm(c.center) for c in clusters]
    if any(m is None for m in matched):
        return False
    return CBM_SHORT in matched and CBM_MEDIUM in matched


def _classify_window(window):
    clusters = find_clusters(histogram(window), min_share=0.05)
    if not clusters:
        return "unclassified", clusters
    if len(clusters) == 1:
        top = clusters[0]
        if top.share > 0.90:
            return ("gap" if top.center >= 255 else "leader"), clusters
        return "unclassified", clusters
    if _window_is_cbm(clusters):
        return "cbm", clusters
    if len(clusters) == 2:
        return "turbo", clusters
    return "unclassified", clusters


def segment(pulses):
    regions = []
    for start in range(0, len(pulses), WINDOW):
        window = pulses[start:start + WINDOW]
        kind, _clusters = _classify_window(window)
        end = start + len(window)
        cycles = sum(p.cycles for p in window)
        if regions and regions[-1].kind == kind:
            r = regions[-1]
            r.end_index = end
            r.pulse_count += len(window)
            r.cycles += cycles
        else:
            regions.append(Region(kind, start, end, len(window), cycles))
    for r in regions:
        r.clusters = find_clusters(
            histogram(pulses[r.start_index:r.end_index]), min_share=0.05)
    return regions


# -------------------------------------------------------------------- cbm --
FILE_TYPES = {1: "relocatable PRG", 2: "SEQ data", 3: "non-relocatable PRG",
              4: "SEQ header", 5: "end of tape"}
HEADER_PAYLOAD = 192


@dataclass
class CbmBlock:
    countdown: int
    payload: bytes
    checksum_stored: int
    checksum_computed: int
    parity_errors: int
    start_index: int
    end_index: int

    @property
    def checksum_ok(self):
        return self.checksum_stored == self.checksum_computed


@dataclass
class CbmFile:
    name: str
    ftype: int
    load: int
    end: int
    data: bytes
    header_block: CbmBlock
    data_block: CbmBlock
    copies_agree: bool
    disagreement_count: int

    @property
    def size(self):
        return len(self.data)

    @property
    def type_name(self):
        return FILE_TYPES.get(self.ftype, "unknown type %d" % self.ftype)


def _symbol(pulse):
    v = pulse.cycles // 8
    for sym, want in (("S", CBM_SHORT), ("M", CBM_MEDIUM), ("L", CBM_LONG)):
        if abs(v - want) <= CBM_TOLERANCE:
            return sym
    return None


def petscii_to_ascii(raw):
    out = []
    for b in raw:
        out.append(chr(b) if 32 <= b < 127 else ".")
    return "".join(out).rstrip(". ").rstrip()


def _read_byte(syms, i):
    """(value, parity_ok, next_i), or (None, None, i) if no byte starts here.

    New-data marker L+M, then 8 bits LSB first, then an odd parity bit.
    A bit is S+M for 0 and M+S for 1.
    """
    if i + 1 >= len(syms) or syms[i] != "L" or syms[i + 1] != "M":
        return None, None, i
    i += 2
    value = 0
    ones = 0
    for bit_index in range(9):                   # 8 data bits, then parity
        if i + 1 >= len(syms):
            return None, None, i
        a, b = syms[i], syms[i + 1]
        if a == "S" and b == "M":
            bit = 0
        elif a == "M" and b == "S":
            bit = 1
        else:
            return None, None, i
        i += 2
        if bit_index < 8:
            value |= bit << bit_index
        ones += bit
    return value, (ones % 2 == 1), i


def decode_cbm_blocks(pulses):
    syms = [_symbol(p) for p in pulses]
    blocks = []
    i = 0
    n = len(syms)
    while i < n:
        value, _parity_ok, nxt = _read_byte(syms, i)
        if value is None:
            i += 1
            continue
        if value not in (0x89, 0x09):            # only a countdown starts one
            i = nxt
            continue
        start = i
        countdown = value
        i = nxt
        expect = value - 1
        floor = 0x81 if countdown == 0x89 else 0x01
        while expect >= floor:
            v, _ok, nxt = _read_byte(syms, i)
            if v != expect:
                break
            i = nxt
            expect -= 1
        payload = bytearray()
        parity_errors = 0
        while True:
            v, ok, nxt = _read_byte(syms, i)
            if v is None:
                break
            payload.append(v)
            if not ok:
                # A damaged byte is kept, not dropped: a cracker wants to
                # see it.
                parity_errors += 1
            i = nxt
            if i + 1 < n and syms[i] == "L" and syms[i + 1] == "S":
                i += 2                           # end-of-data marker
                break
        if not payload:
            continue
        stored = payload[-1]
        body = bytes(payload[:-1])
        computed = 0
        for b in body:
            computed ^= b
        blocks.append(CbmBlock(countdown, body, stored, computed,
                               parity_errors, start, i))
    return blocks


def pair_blocks(blocks):
    """Pair each $89 block with the $09 repeat that follows it."""
    pairs = []
    i = 0
    while i < len(blocks):
        first = blocks[i]
        repeat = None
        if i + 1 < len(blocks) and blocks[i + 1].countdown == 0x09:
            repeat = blocks[i + 1]
            i += 2
        else:
            i += 1
        pairs.append((first, repeat))
    return pairs


def _disagreements(a, b):
    if b is None:
        return 0
    n = max(len(a.payload), len(b.payload))
    pa = a.payload.ljust(n, b"\x00")
    pb = b.payload.ljust(n, b"\x00")
    return sum(1 for x, y in zip(pa, pb) if x != y)


def build_files(pairs):
    files = []
    i = 0
    while i + 1 < len(pairs):
        hdr_first, hdr_repeat = pairs[i]
        if len(hdr_first.payload) < HEADER_PAYLOAD:
            i += 1
            continue
        p = hdr_first.payload
        ftype = p[0]
        load = p[1] | (p[2] << 8)
        end = p[3] | (p[4] << 8)
        name = petscii_to_ascii(p[5:21])
        data_first, data_repeat = pairs[i + 1]
        disagree = (_disagreements(hdr_first, hdr_repeat)
                    + _disagreements(data_first, data_repeat))
        files.append(CbmFile(name, ftype, load, end, data_first.payload,
                             hdr_first, data_first, disagree == 0, disagree))
        i += 2
    return files


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
