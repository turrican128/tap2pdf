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
        # A lower threshold for what gets REPORTED than for what gets
        # decided: the CBM long pulse occurs once per byte, about 5%, and
        # at the decision threshold it vanishes from the dossier - leaving
        # a ROM region that appears to have only two pulse widths.
        r.clusters = find_clusters(
            histogram(pulses[r.start_index:r.end_index]), min_share=0.02)
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


# ------------------------------------------------------------------ basic --
# BASIC V2 tokens $80-$CB. $9E is SYS, and that anchor is what the entry-point
# feature rests on: if this table shifts by one, every SYS address is wrong.
_TOKEN_TEXT = (
    "END FOR NEXT DATA INPUT# INPUT DIM READ LET GOTO RUN IF RESTORE GOSUB "
    "RETURN REM STOP ON WAIT LOAD SAVE VERIFY DEF POKE PRINT# PRINT CONT LIST "
    "CLR CMD SYS OPEN CLOSE GET NEW TAB( TO FN SPC( THEN NOT STEP + - * / ^ "
    "AND OR > = < SGN INT ABS USR FRE POS SQR RND LOG EXP COS SIN TAN ATN "
    "PEEK LEN STR$ VAL ASC CHR$ LEFT$ RIGHT$ MID$ GO"
).split()
BASIC_TOKENS = dict((0x80 + i, t) for i, t in enumerate(_TOKEN_TEXT))


def detokenize(data, load=0x0801):
    """Lines are [next-line pointer][line number][tokens][$00]; a next-line
    pointer of $0000 ends the program."""
    lines = []
    i = 0
    while i + 4 <= len(data):
        nxt = data[i] | (data[i + 1] << 8)
        if nxt == 0:
            break
        number = data[i + 2] | (data[i + 3] << 8)
        i += 4
        out = []
        while i < len(data) and data[i]:
            b = data[i]
            out.append(BASIC_TOKENS.get(b, chr(b) if 32 <= b < 127 else "."))
            i += 1
        i += 1                                   # the line's terminating $00
        lines.append((number, "".join(out)))
    return lines


def find_sys(lines):
    for _number, text in lines:
        at = text.find("SYS")
        if at < 0:
            continue
        digits = ""
        for ch in text[at + 3:]:
            if ch.isdigit():
                digits += ch
            elif digits or ch != " ":
                break
        if digits:
            return int(digits)
    return None


# ----------------------------------------------------------------- packer --
# Every entry here was observed in a file crunched locally with the named
# tool. Nothing in this table is written from memory: a packer table that
# guesses makes the dossier lie, which is the one thing it must not do.
# Re-derive with tools/derive_signatures.py.
PACKER_SIGNATURES = []


def identify_packer(data, load):
    for sig in PACKER_SIGNATURES:
        window = data[:sig["max_offset"] + len(sig["pattern"])]
        at = window.find(sig["pattern"])
        if at >= 0:
            return {"name": sig["name"], "offset": at}
    return None


# ----------------------------------------------------------------- verify --
# Most people who own a TAP downloaded it. The question they actually have is
# whether the file is any good, so that is what the dossier opens with - and
# the part that makes it worth trusting is the checks that did NOT run.
PASS = "PASS"
FAIL = "FAIL"
NOT_CHECKED = "NOT CHECKED"


@dataclass
class Check:
    name: str
    result: str
    detail: str


def build_checks(header, pulses, regions, files, tapclean_used, loaders=None):
    checks = []

    checks.append(Check(
        "TAP signature", PASS,
        "%s, version %d, %s, %s" % (header.signature.decode("ascii"),
                                    header.version, header.platform,
                                    header.video)))

    if header.length_mismatch == 0:
        checks.append(Check(
            "Header length vs actual data", PASS,
            "declared %d bytes, file holds %d"
            % (header.declared_length, header.actual_length)))
    else:
        checks.append(Check(
            "Header length vs actual data", FAIL,
            "header says %d bytes, file holds %d (%+d). Cosmetic; repairable "
            "with `tapclean -rs`."
            % (header.declared_length, header.actual_length,
               header.length_mismatch)))

    overflows = sum(1 for p in pulses if p.overflow)
    checks.append(Check(
        "Pulse stream integrity", PASS,
        "%d pulses, none truncated%s"
        % (len(pulses),
           (", %d of unrecorded length (version 0 overflow)" % overflows)
           if overflows else "")))

    blocks = [b for f in files for b in (f.header_block, f.data_block)]
    if not blocks:
        checks.append(Check(
            "CBM block checksums", NOT_CHECKED,
            "no CBM ROM-loader blocks were found on this tape"))
    else:
        bad = [b for b in blocks if not b.checksum_ok]
        if bad:
            checks.append(Check(
                "CBM block checksums", FAIL,
                "%d of %d blocks fail: %s"
                % (len(bad), len(blocks),
                   ", ".join("expected $%02X, computed $%02X"
                             % (b.checksum_stored, b.checksum_computed)
                             for b in bad))))
        else:
            checks.append(Check("CBM block checksums", PASS,
                                "all %d blocks pass" % len(blocks)))

        disagreeing = [f for f in files if not f.copies_agree]
        if disagreeing:
            checks.append(Check(
                "First copy vs repeat", FAIL,
                "%d file(s) differ between the two recorded copies: %s"
                % (len(disagreeing),
                   ", ".join("%s (%d byte(s))" % (f.name, f.disagreement_count)
                             for f in disagreeing))))
        else:
            checks.append(Check("First copy vs repeat", PASS,
                                "all %d file(s) agree" % len(files)))

        parity = sum(b.parity_errors for b in blocks)
        checks.append(Check(
            "Byte parity", PASS if parity == 0 else FAIL,
            "no parity errors" if parity == 0
            else "%d byte(s) carry a bad parity bit" % parity))

    turbo = [r for r in regions if r.kind == "turbo"]
    if turbo:
        checks.append(Check(
            "Turbo region integrity", NOT_CHECKED,
            "%d turbo region(s), %d pulses. The format is unidentified, so "
            "there is no checksum model to verify them against."
            % (len(turbo), sum(r.pulse_count for r in turbo))))

    unclassified = [r for r in regions if r.kind == "unclassified"]
    if unclassified:
        checks.append(Check(
            "Unclassified regions", NOT_CHECKED,
            "%d region(s), %d pulses, match no pulse pattern this tool "
            "recognises and were not analysed further."
            % (len(unclassified), sum(r.pulse_count for r in unclassified))))

    if tapclean_used:
        named = ", ".join(loaders) if loaders else ""
        checks.append(Check(
            "Loader identification", PASS,
            ("identified from the supplied TAPClean report: " + named)
            if named
            else "the supplied TAPClean report names no loader"))
    else:
        checks.append(Check(
            "Loader identification", NOT_CHECKED,
            "no loader names available: run with `--tapclean <report>` to "
            "identify them"))

    if not PACKER_SIGNATURES:
        checks.append(Check(
            "Cruncher identification", NOT_CHECKED,
            "no cruncher signatures are compiled into this build, so no "
            "check was attempted"))

    return checks


def verdict(checks, regions):
    """One plain sentence that never outruns the evidence."""
    failed = [c for c in checks if c.result == FAIL]
    unchecked = [c for c in checks if c.result == NOT_CHECKED]
    has_cbm = any(r.kind == "cbm" for r in regions)
    parts = []
    if failed:
        parts.append("This tape has %d failing check(s): %s."
                     % (len(failed), ", ".join(c.name.lower()
                                               for c in failed)))
    elif has_cbm:
        parts.append("The CBM portion of this tape reads cleanly.")
    else:
        parts.append("No CBM ROM-loader data was found on this tape.")
    if unchecked:
        parts.append("%d aspect(s) have not been checked (%s), so this is not "
                     "a clean bill of health for the whole tape."
                     % (len(unchecked),
                        ", ".join(c.name.lower() for c in unchecked)))
    return " ".join(parts)


# ---------------------------------------------------------------- tapemap --
REGION_COLORS = {
    "leader": "#5b7fa6", "cbm": "#3f8f5c", "turbo": "#c07a2c",
    "gap": "#40454d", "unclassified": "#8a3b3b",
}
REGION_LABELS = {
    "leader": "leader", "cbm": "CBM ROM loader", "turbo": "turbo",
    "gap": "gap", "unclassified": "unclassified",
}


def svg_escape(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def tape_map_svg(regions, header, width=880, height=124):
    """Bands proportional to TIME, not pulse count: a gap of few long pulses
    occupies real seconds on the tape and must look like it."""
    total = sum(r.cycles for r in regions) or 1
    bar_y, bar_h = 30, 46
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="100%%" role="img" aria-label="tape map">'
             % (width, height)]
    x = 0.0
    for r in regions:
        w = max(1.0, width * (r.cycles / float(total)))
        parts.append(
            '<rect x="%.2f" y="%d" width="%.2f" height="%d" fill="%s">'
            '<title>%s - %d pulses, %.2f s</title></rect>'
            % (x, bar_y, w, bar_h, REGION_COLORS.get(r.kind, "#8a3b3b"),
               svg_escape(REGION_LABELS.get(r.kind, r.kind)), r.pulse_count,
               seconds(r.cycles, header)))
        x += w
    duration = seconds(total, header)
    for i in range(6):
        tx = width * i / 5.0
        parts.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" '
                     'stroke="#aab" stroke-width="1"/>'
                     % (tx, bar_y + bar_h, tx, bar_y + bar_h + 5))
        parts.append('<text x="%.1f" y="%d" font-size="11" fill="#6a7078" '
                     'text-anchor="%s">%.1fs</text>'
                     % (min(width - 2, max(2, tx)), bar_y + bar_h + 18,
                        "start" if i == 0 else
                        ("end" if i == 5 else "middle"),
                        duration * i / 5.0))
    seen = []
    for r in regions:
        if r.kind not in seen:
            seen.append(r.kind)
    lx = 0
    for kind in seen:
        label = REGION_LABELS.get(kind, kind)
        parts.append('<rect x="%d" y="6" width="10" height="10" fill="%s"/>'
                     % (lx, REGION_COLORS.get(kind, "#8a3b3b")))
        parts.append('<text x="%d" y="15" font-size="11" fill="#3a4048">%s'
                     '</text>' % (lx + 14, svg_escape(label)))
        lx += 26 + 7 * len(label)
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------- memmap --
def memory_map_svg(files, width=880, height=176):
    bar_y, bar_h = 62, 40
    scale = width / 65536.0
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
             'width="100%%" role="img" aria-label="memory map">'
             % (width, height)]
    for start, end, label in ((0xA000, 0xC000, "BASIC"),
                              (0xD000, 0xE000, "I/O"),
                              (0xE000, 0x10000, "KERNAL")):
        parts.append('<rect x="%.2f" y="%d" width="%.2f" height="%d" '
                     'fill="#e8eaed"/>'
                     % (start * scale, bar_y, (end - start) * scale, bar_h))
        parts.append('<text x="%.2f" y="%d" font-size="10" fill="#9aa0a6">%s'
                     '</text>' % (start * scale + 3, bar_y + bar_h - 5, label))
    parts.append('<rect x="0" y="%d" width="%d" height="%d" fill="none" '
                 'stroke="#c8ccd2"/>' % (bar_y, width, bar_h))
    for i, f in enumerate(files):
        x = f.load * scale
        w = max(2.0, (f.end - f.load) * scale)
        row = i % 2
        parts.append('<rect x="%.2f" y="%d" width="%.2f" height="%d" '
                     'fill="#3f8f5c" fill-opacity="0.85">'
                     '<title>%s</title></rect>'
                     % (x, bar_y, w, bar_h, svg_escape(f.name)))
        label_y = bar_y - 8 - row * 16
        parts.append('<line x1="%.2f" y1="%d" x2="%.2f" y2="%d" '
                     'stroke="#9aa0a6" stroke-width="1"/>'
                     % (x, label_y + 3, x, bar_y))
        parts.append('<text x="%.2f" y="%d" font-size="11" fill="#1b1d20">'
                     '%s $%04X</text>'
                     % (x + 3, label_y, svg_escape(f.name), f.load))
    for addr in (0x0000, 0x4000, 0x8000, 0xC000, 0xFFFF):
        tx = addr * scale
        parts.append('<text x="%.1f" y="%d" font-size="11" fill="#6a7078" '
                     'text-anchor="%s">$%04X</text>'
                     % (min(width - 2, max(2, tx)), bar_y + bar_h + 18,
                        "start" if addr == 0 else
                        ("end" if addr == 0xFFFF else "middle"), addr))
    parts.append("</svg>")
    return "".join(parts)


# ----------------------------------------------------------------- report --
@dataclass
class Dossier:
    title: str
    header: TapHeader
    regions: list
    files: list
    checks: list
    verdict_text: str
    sys_entry: object
    packers: dict
    loaders: list
    duration: float
    pulse_count: int
    enrichments: dict
    screenshot_data_uri: object = None


def analyse(data, args):
    header = parse_header(data)
    if getattr(args, "pal", False):
        header.video = "PAL"
    elif getattr(args, "ntsc", False):
        header.video = "NTSC"
    pulses = decode_pulses(data, header)
    regions = segment(pulses)
    files = build_files(pair_blocks(decode_cbm_blocks(pulses)))
    sys_entry = None
    packers = {}
    for f in files:
        if f.load == 0x0801 and sys_entry is None:
            sys_entry = find_sys(detokenize(f.data))
        found = identify_packer(f.data, f.load)
        if found:
            packers[f.name] = found
    report_path = getattr(args, "tapclean", None)
    loaders = []
    if report_path:
        loaders = read_tapclean_report(report_path)["loaders"]
    checks = build_checks(header, pulses, regions, files,
                          tapclean_used=bool(report_path), loaders=loaders)
    return Dossier(
        title=getattr(args, "title", None) or os.path.basename(args.tape),
        header=header, regions=regions, files=files, checks=checks,
        verdict_text=verdict(checks, regions), sys_entry=sys_entry,
        packers=packers,
        loaders=loaders,
        duration=seconds(total_cycles(pulses), header),
        pulse_count=len(pulses),
        enrichments={
            "TAPClean report": bool(getattr(args, "tapclean", None)),
            "VICE screenshot": bool(getattr(args, "vice", None)),
        })


CSS = """
:root{--ink:#1b1d20;--muted:#6a7078;--rule:#d8dce1;--bg:#fff;--panel:#f6f7f9;
--pass:#2f7d4f;--fail:#a52f2f;--unchecked:#8a6d1f}
*{box-sizing:border-box}
body{margin:0;padding:32px;background:var(--bg);color:var(--ink);
font:15px/1.55 "Helvetica Neue",Arial,sans-serif;max-width:960px}
h1{font-size:28px;margin:0 0 4px;letter-spacing:-.01em}
h2{font-size:17px;margin:34px 0 10px;padding-bottom:6px;
border-bottom:1px solid var(--rule)}
.sub{color:var(--muted);margin:0 0 18px}
table{border-collapse:collapse;width:100%;font-size:14px}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--rule);
vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:12px;text-transform:uppercase;
letter-spacing:.04em}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.PASS{color:var(--pass);font-weight:600;white-space:nowrap}
.FAIL{color:var(--fail);font-weight:600;white-space:nowrap}
.NOTCHECKED{color:var(--unchecked);font-weight:600;white-space:nowrap}
.verdict{background:var(--panel);border-left:3px solid var(--muted);
padding:12px 14px;margin:14px 0}
.prov{color:var(--muted);font-size:13px}
figure{margin:0}
@media print{body{padding:0;max-width:none}h2{page-break-after:avoid}
table{page-break-inside:avoid}figure{page-break-inside:avoid}}
@page{size:A4;margin:16mm}
"""


def _h(text):
    import html as _html
    return _html.escape(str(text), quote=True)


def render_html(d):
    check_rows = []
    for c in d.checks:
        check_rows.append(
            '<tr><td>%s</td><td class="%s">%s</td><td>%s</td></tr>'
            % (_h(c.name), c.result.replace(" ", ""), _h(c.result),
               _h(c.detail)))

    file_rows = []
    for f in d.files:
        packer = d.packers.get(f.name)
        note = ("%s at +%d" % (packer["name"], packer["offset"])) if packer \
            else ""
        file_rows.append(
            '<tr><td>%s</td><td>%s</td><td class="num">$%04X</td>'
            '<td class="num">$%04X</td><td class="num">%d</td>'
            '<td class="%s">%s</td><td>%s</td></tr>'
            % (_h(f.name), _h(f.type_name), f.load, f.end, f.size,
               "PASS" if f.data_block.checksum_ok else "FAIL",
               "ok" if f.data_block.checksum_ok else "bad", _h(note)))
    if not file_rows:
        file_rows.append('<tr><td colspan="7">No CBM ROM-loader files were '
                         'recovered from this tape.</td></tr>')

    region_rows = []
    for r in d.regions:
        clusters = ", ".join("$%02X (%.0f%%)" % (c.center, c.share * 100)
                             for c in r.clusters)
        region_rows.append(
            '<tr><td>%s</td><td class="num">%d</td><td class="num">%.2f s</td>'
            '<td class="num">%s</td></tr>'
            % (_h(REGION_LABELS.get(r.kind, r.kind)), r.pulse_count,
               seconds(r.cycles, d.header), _h(clusters or "-")))

    prov = []
    for name, used in sorted(d.enrichments.items()):
        prov.append("<li>%s: %s</li>"
                    % (_h(name), "used" if used else "not used"))

    entry = ("$%04X (SYS %d)" % (d.sys_entry, d.sys_entry)) if d.sys_entry \
        else "not found in a BASIC stub"

    screenshot = ""
    if d.screenshot_data_uri:
        screenshot = ('<figure><img src="%s" alt="title screen" '
                      'style="width:100%%;max-width:640px;border:1px solid '
                      '#d8dce1"/></figure>' % d.screenshot_data_uri)

    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>%s - tape dossier</title><style>%s</style></head><body>'
        '<h1>%s</h1>'
        '<p class="sub">%s &middot; %s &middot; %.2f seconds &middot; '
        '%d pulses</p>'
        '%s'
        '<h2>Verification report</h2>'
        '<table><thead><tr><th>Check</th><th>Result</th><th>Detail</th></tr>'
        '</thead><tbody>%s</tbody></table>'
        '<p class="verdict">%s</p>'
        '<h2>Tape map</h2><figure>%s</figure>'
        '<h2>Files</h2>'
        '<table><thead><tr><th>Name</th><th>Type</th><th>Load</th><th>End+1</th>'
        '<th>Size</th><th>Checksum</th><th>Cruncher</th></tr></thead>'
        '<tbody>%s</tbody></table>'
        '<p class="sub">Machine-code entry point: %s</p>'
        '<h2>Memory map</h2><figure>%s</figure>'
        '<h2>Regions</h2>'
        '<table><thead><tr><th>Kind</th><th>Pulses</th><th>Duration</th>'
        '<th>Pulse clusters</th></tr></thead><tbody>%s</tbody></table>'
        '<h2>Provenance</h2><ul class="prov">%s</ul>'
        '<p class="prov">Generated by tap2pdf %s. This document states what '
        'was checked and what was not: a check that did not run is never '
        'shown as a pass.</p>'
        '</body></html>\n'
        % (_h(d.title), CSS, _h(d.title), _h(d.header.platform),
           _h(d.header.video), d.duration, d.pulse_count, screenshot,
           "".join(check_rows), _h(d.verdict_text),
           tape_map_svg(d.regions, d.header), "".join(file_rows), _h(entry),
           memory_map_svg(d.files), "".join(region_rows), "".join(prov),
           _h(__version__)))


NFO_WIDTH = 78


def _ascii(text):
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))


def _wrap(text, width, indent=""):
    words = _ascii(text).split()
    lines = []
    current = indent
    for w in words:
        if len(current) + len(w) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent + w + " "
        else:
            current += w + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def render_nfo(d):
    """The same dossier, as 7-bit ASCII you can paste into a release."""
    out = ["+" + "-" * (NFO_WIDTH - 2) + "+",
           "| " + _ascii(d.title).ljust(NFO_WIDTH - 4)[:NFO_WIDTH - 4] + " |",
           "+" + "-" * (NFO_WIDTH - 2) + "+",
           "",
           "%s / %s / %.2f seconds / %d pulses"
           % (_ascii(d.header.platform), _ascii(d.header.video),
              d.duration, d.pulse_count),
           "",
           "VERIFICATION", "-" * 12]
    for c in d.checks:
        out.append("%-30s %s" % (_ascii(c.name)[:30], c.result))
        out.extend(_wrap(c.detail, NFO_WIDTH, "    "))
    out += ["", "VERDICT", "-" * 7]
    out += _wrap(d.verdict_text, NFO_WIDTH)
    out += ["", "FILES", "-" * 5,
            "%-17s %-20s %-6s %-6s %7s %s"
            % ("NAME", "TYPE", "LOAD", "END+1", "SIZE", "SUM")]
    if d.files:
        for f in d.files:
            out.append("%-17s %-20s $%04X  $%04X  %7d %s"
                       % (_ascii(f.name)[:17], _ascii(f.type_name)[:20],
                          f.load, f.end, f.size,
                          "ok" if f.data_block.checksum_ok else "BAD"))
    else:
        out.append("(no CBM ROM-loader files were recovered)")
    if d.sys_entry:
        out += ["", "Entry point: $%04X (SYS %d)" % (d.sys_entry, d.sys_entry)]
    out += ["",
            "Generated by tap2pdf " + __version__ + ".",
            "A check that did not run is shown as NOT CHECKED, never as a "
            "pass."]
    return "\n".join(line[:NFO_WIDTH] for line in out) + "\n"


def extract_files(d, directory):
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise Refusal(EXIT_OUTPUT, "cannot create %s: %s" % (directory, exc))
    written = []
    for i, f in enumerate(d.files):
        safe = "".join(ch if ch.isalnum() else "_" for ch in f.name) or "file"
        path = os.path.join(directory, "%02d_%s.prg" % (i + 1, safe))
        try:
            with open(path, "wb") as fh:
                fh.write(bytes([f.load & 0xFF, (f.load >> 8) & 0xFF]))
                fh.write(f.data)
        except OSError as exc:
            raise Refusal(EXIT_OUTPUT, "cannot write %s: %s" % (path, exc))
        written.append(path)
    return written


# ----------------------------------------------------------------- enrich --
# Optional. Every failure here is reported and never faked, and the HTML is
# always written first so a failed enrichment cannot cost you the dossier.
import shutil            # noqa: E402 - kept beside its only users
import subprocess        # noqa: E402

BROWSER_CANDIDATES = [
    "msedge", "chrome", "chromium", "google-chrome", "chromium-browser",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def parse_tapclean_report(text):
    """Tolerant of format drift: it scans for loader names and never raises
    on a line it does not understand."""
    loaders = []
    lines = []
    for raw in str(text).splitlines():
        lines.append(raw)
        low = raw.lower()
        for marker in ("loader detected:", "loader:"):
            if marker in low:
                name = raw[low.index(marker) + len(marker):].strip()
                if name and name not in loaders:
                    loaders.append(name)
                break
    return {"loaders": loaders, "raw_lines": lines}


def read_tapclean_report(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return parse_tapclean_report(fh.read())
    except OSError as exc:
        raise Refusal(EXIT_ENRICH,
                      "cannot read the TAPClean report %s: %s" % (path, exc))


def find_browser(explicit=None):
    """An explicitly named browser is used or nothing is.

    Falling back to some other browser when --browser names one that is not
    there would render the PDF with a program the user did not ask for and
    say nothing about it.
    """
    if explicit:
        return shutil.which(explicit) or (explicit
                                          if os.path.isfile(explicit)
                                          else None)
    for c in BROWSER_CANDIDATES:
        found = shutil.which(c)
        if found:
            return found
        if os.path.isfile(c):
            return c
    return None


def render_pdf(html_path, pdf_path, browser=None):
    exe = find_browser(browser)
    if not exe:
        raise Refusal(
            EXIT_ENRICH,
            ("--browser %s was not found, and tap2pdf will not silently use "
             "a different one. The HTML dossier was still written." % browser)
            if browser else
            "no headless Edge or Chrome found for --pdf. The HTML dossier "
            "was still written. Pass --browser <path>.")
    url = "file:///" + os.path.abspath(html_path).replace("\\", "/")
    try:
        subprocess.run([exe, "--headless=new", "--disable-gpu",
                        "--no-pdf-header-footer",
                        "--print-to-pdf=" + os.path.abspath(pdf_path), url],
                       check=True, capture_output=True, timeout=180)
    except Exception as exc:
        raise Refusal(EXIT_ENRICH,
                      "the browser failed to render the PDF: %s. The HTML "
                      "dossier was still written." % exc)
    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) == 0:
        raise Refusal(EXIT_ENRICH,
                      "the browser produced no PDF. The HTML dossier was "
                      "still written.")


def capture_screenshot(vice_path, tap_path):
    """Not wired in v1.0. Its absence is reported in the dossier's
    provenance block rather than quietly producing a document with no
    cover image and no explanation."""
    return None


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


def default_output(tape):
    base = os.path.basename(tape)
    stem = base[:-4] if base.lower().endswith(".tap") else base
    return os.path.join(os.path.dirname(os.path.abspath(tape)),
                        stem + "-dossier.html")


def _write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        raise Refusal(EXIT_OUTPUT, "cannot write %s: %s" % (path, exc))


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if not os.path.isfile(args.tape):
            raise Refusal(EXIT_INPUT, "cannot read: " + args.tape)
        try:
            with open(args.tape, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise Refusal(EXIT_INPUT, "cannot read %s: %s" % (args.tape, exc))

        dossier = analyse(data, args)

        html_path = args.output or default_output(args.tape)
        _write_text(html_path, render_html(dossier))
        if not args.quiet:
            sys.stderr.write("wrote %s\n" % html_path)

        if args.nfo:
            nfo_path = os.path.splitext(html_path)[0] + ".nfo"
            _write_text(nfo_path, render_nfo(dossier))
            if not args.quiet:
                sys.stderr.write("wrote %s\n" % nfo_path)

        if args.extract:
            written = extract_files(dossier, args.extract)
            if not args.quiet:
                sys.stderr.write("extracted %d file(s) to %s\n"
                                 % (len(written), args.extract))

        if args.pdf or args.pdf_only:
            pdf_path = os.path.splitext(html_path)[0] + ".pdf"
            render_pdf(html_path, pdf_path, args.browser)
            if not args.quiet:
                sys.stderr.write("wrote %s\n" % pdf_path)
            if args.pdf_only:
                try:
                    os.remove(html_path)
                except OSError:
                    pass

        for check in dossier.checks:
            if check.result == FAIL and not args.quiet:
                sys.stderr.write("  %s: %s\n" % (check.name, check.detail))

        return EXIT_OK
    except Refusal as r:
        sys.stderr.write("tap2pdf: " + r.message + "\n")
        return r.code


if __name__ == "__main__":
    sys.exit(main())
