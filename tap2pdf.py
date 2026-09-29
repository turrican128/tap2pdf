#!/usr/bin/env python3
"""tap2pdf - turn a C64 .tap into an honest dossier about what is on it.

Standard library only. The binary people download bundles nothing else, so
nothing else may be imported here.
"""
import argparse
import html as _html
import os
import shutil
import struct
import subprocess
import sys
from dataclasses import dataclass, field

__version__ = "1.0.4"

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

# Decoding amplifies a TAP substantially in memory: one Pulse object per
# pulse, plus a symbol list alongside it. Measured at roughly 130x the file
# size on a real tape. A genuine C64 tape side is a few megabytes at most -
# the largest of 49 commercial tapes checked was 2.5 MB - so this default
# covers any real tape by a wide margin while refusing input that would take
# the process down instead of producing a message. --max-size raises it.
DEFAULT_MAX_INPUT_MB = 16


def refuse_if_too_large(size_bytes, limit_mb, what):
    limit = int(limit_mb * 1024 * 1024)
    if size_bytes > limit:
        raise Refusal(
            EXIT_INPUT,
            "%s is %.2f MB, above the %g MB limit. Decoding needs roughly "
            "130x the file size in memory, so this would likely exhaust it. "
            "Raise the limit with --max-size <MB> if you know the machine "
            "can take it." % (what, size_bytes / (1024.0 * 1024.0), limit_mb))


@dataclass
class TapHeader:
    signature: bytes
    version: int
    platform: str
    video: str
    declared_length: int
    actual_length: int
    length_mismatch: int
    # The raw bytes, and whether they named anything documented. An
    # undocumented value must not be rendered as a confident "C64 / PAL":
    # the timing shown would be an assumption the file never made.
    platform_byte: int = 0
    video_byte: int = 0
    platform_known: bool = True
    video_known: bool = True
    # Set when --pal/--ntsc chose the clock. Kept apart from video_known,
    # which is only ever about what the FILE says: an override reported as
    # "as stated by the header" puts the user's words in the file's mouth.
    video_forced_by: object = None

    @property
    def timing_is_assumed(self):
        return not self.video_known and not self.video_forced_by


def parse_header(data):
    if len(data) < HEADER_SIZE:
        raise Refusal(EXIT_NOT_TAP,
                      "too short to be a TAP file (%d bytes)" % len(data))
    sig = data[:12]
    if sig not in SIGNATURES:
        raise Refusal(EXIT_NOT_TAP, "not a TAP file: signature is %r" % sig)
    version = data[12]
    if version == 2:
        raise Refusal(EXIT_MALFORMED,
                      "TAP version 2 (C16/Plus4 half-wave) is not supported "
                      "by this version of tap2pdf")
    if version not in (0, 1):
        raise Refusal(EXIT_MALFORMED, "unknown TAP version %d" % version)
    platform_byte = data[13]
    video_byte = data[14]
    platform = PLATFORMS.get(platform_byte, SIGNATURES[sig])
    video = VIDEO.get(video_byte, "PAL")
    declared = struct.unpack("<I", data[16:20])[0]
    actual = len(data) - HEADER_SIZE
    # A C16 signature with a C64 platform byte (or the reverse) is two
    # statements that cannot both be true; neither may be shown as a pass.
    # VIC-20 tapes carry the C64 signature, so that pairing is consistent.
    platform_known = (platform_byte in PLATFORMS
                      and (SIGNATURES[sig] == "C16") == (platform == "C16"))
    return TapHeader(sig, version, platform, video, declared, actual,
                     actual - declared, platform_byte, video_byte,
                     platform_known, video_byte in VIDEO)


OVERFLOW_CYCLES = 255 * 8


@dataclass
class Pulse:
    # There is one of these per pulse, and a real tape has millions. Measured
    # on Night Breed (2.47M pulses): 136 bytes each without __slots__, 96
    # with. dataclass(slots=True) would be tidier but needs Python 3.10, and
    # 3.9 is the floor this project promises.
    __slots__ = ("cycles", "overflow", "offset")
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


def _classify_hist(hist):
    """Classify from a pulse histogram, so spans can be judged on pooled
    evidence rather than one 256-pulse window at a time."""
    clusters = find_clusters(hist, min_share=0.05)
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


# A genuine region spans many windows: a leader runs for hundreds of them, a
# block for thousands of pulses. A run of one or two windows is not a real
# change in what is on the tape.
SMOOTH_MIN_RUN = 3

# ...but "too short to be real" is relative to the tape, not an absolute
# number of windows. A 3-window region is 5% of a small tape and 0.03% of a
# 9,600-window one. Raising the absolute floor to fix a long tape destroyed
# the structure of short ones: at a floor of 8, a two-file tape collapsed
# from its genuine 8 regions to 1. Scaling to the tape's own length fixed
# Night Breed (4,112 regions -> 48) while leaving every fixture's real
# structure and every normal tape exactly as they were. Measured across 5
# real tapes and 5 fixtures; tighter ratios began eroding genuine regions.
SMOOTH_SCALE = 400


def _min_run_for(window_count):
    return max(SMOOTH_MIN_RUN, window_count // SMOOTH_SCALE)


def _add_hist(into, other):
    for value, count in other.items():
        into[value] = into.get(value, 0) + count
    return into


def _pooled_hist(hists, a, b):
    out = {}
    for h in hists[a:b]:
        _add_hist(out, h)
    return out


def _coalesce(spans):
    out = []
    for s in spans:
        if out and out[-1][2] == s[2]:
            out[-1][1] = s[1]
        else:
            out.append(list(s))
    return out


def _segment_spans(hists):
    """Spans of [first_window, last_window+1, kind].

    Each window is classified by thresholds: a cluster within CBM_TOLERANCE
    of a CBM width, a single cluster above 90%, a third cluster above the 5%
    noise floor. Real tape speed wobbles by a couple of pulse units, which
    crosses all three edges repeatedly, so one unchanging stretch of tape
    came out labelled cbm, turbo, leader and unclassified in consecutive
    windows. Night Breed produced 4,112 regions for a tape with a handful,
    55% of them a single window, and the resulting table made a dossier
    headless Edge could not lay out. Those regions were not real, so the
    table was also telling the reader something untrue.

    Smoothing the labels does not fix it: with a strict cbm/turbo
    alternation every run is one window, so each takes its neighbour's label
    and they simply swap, forever. The answer is to merge the short run with
    a neighbour and RE-CLASSIFY the merged span from the pooled histogram -
    judging the stretch on all its evidence at once instead of voting on
    labels decided 256 pulses at a time.
    """
    min_run = _min_run_for(len(hists))
    spans = _coalesce([[i, i + 1, _classify_hist(h)[0]]
                       for i, h in enumerate(hists)])
    while len(spans) > 1:
        if all(s[1] - s[0] >= min_run for s in spans):
            break
        merged = []
        i = 0
        while i < len(spans):
            s = spans[i]
            if s[1] - s[0] >= min_run:
                merged.append(list(s))
                i += 1
                continue
            if i + 1 < len(spans):
                a, b = s[0], spans[i + 1][1]
                i += 2
            elif merged:
                a, b = merged.pop()[0], s[1]
                i += 1
            else:
                merged.append(list(s))
                i += 1
                continue
            merged.append([a, b, _classify_hist(_pooled_hist(hists, a, b))[0]])
        merged = _coalesce(merged)
        if len(merged) >= len(spans):
            break                      # no progress; stop rather than spin
        spans = merged
    return spans


def segment(pulses, stats=None):
    """Regions, and optionally how much of the tape never classified.

    Merging fixes the table but must not quietly raise the tool's
    confidence. On one real tape 11.8% of the pulses matched no pattern at
    256-pulse resolution; pooling relabelled every one of them and the
    "unclassified" row vanished from the verification report. The share is
    measured from the raw per-window verdicts and reported whatever the
    merged table ends up saying.
    """
    windows = [pulses[s:s + WINDOW] for s in range(0, len(pulses), WINDOW)]
    if not windows:
        return []
    hists = [histogram(w) for w in windows]

    if stats is not None:
        raw = 0
        for w, h in zip(windows, hists):
            if _classify_hist(h)[0] == "unclassified":
                raw += len(w)
        stats["unclassified_pulses"] = raw
        stats["total_pulses"] = len(pulses)

    regions = []
    for a, b, kind in _segment_spans(hists):
        start = a * WINDOW
        end = min(len(pulses), b * WINDOW)
        regions.append(Region(kind, start, end, end - start,
                              sum(p.cycles for p in pulses[start:end])))
    for r in regions:
        # A lower threshold for what gets REPORTED than for what gets
        # decided: the CBM long pulse occurs once per byte, about 5%, and at
        # the decision threshold it vanishes from the dossier - leaving a
        # ROM region that appears to have only two pulse widths.
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
    # The repeat copies, or None when the tape did not carry them. Held so
    # that every block belonging to a file can be verified: keeping only the
    # first copies meant half the blocks on a normal tape were never
    # checksum-checked at all.
    header_repeat: object
    data_repeat: object
    missing_repeats: int
    disagreement_count: int
    # A SEQ file carries one or more further data block pairs after the
    # first. Empty for a PRG.
    more_data: list = field(default_factory=list)
    # True when `data` came from a repeat copy because the first copy failed
    # its checksum and the repeat passed. The good bytes were on the tape,
    # so they are the ones handed over - and the dossier says so.
    data_from_repeat: bool = False
    # Whether every data block `data` was taken from passes its checksum.
    data_ok: bool = True

    @property
    def all_blocks(self):
        extra = [b for pair in self.more_data for b in pair if b is not None]
        return [b for b in (self.header_block, self.data_block,
                            self.header_repeat, self.data_repeat)
                if b is not None] + extra

    @property
    def is_seq(self):
        return self.ftype == 4

    @property
    def span(self):
        """Bytes the header's range covers. An end address of $0000 is a
        file that runs to the top of memory: the end is exclusive and 16
        bits wide, so $10000 is written as $0000."""
        return ((self.end or 0x10000) - self.load) & 0x1FFFF

    @property
    def sum_label(self):
        if not self.data_ok:
            return "bad"
        return "ok (repeat copy)" if self.data_from_repeat else "ok"

    @property
    def copies_agree(self):
        """Only true when there was something to compare AND it matched.

        An absent repeat is not agreement. Returning 'they agree' for a
        comparison that never happened is the tool claiming more than it
        established.
        """
        return self.missing_repeats == 0 and self.disagreement_count == 0

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


def _compare_copies(a, b):
    """(disagreeing_bytes, missing) for one block and its repeat.

    `missing` is 1 when there is no repeat to compare against. It is counted
    separately and never folded into the disagreement count, because a
    comparison that could not happen is not a comparison that passed.
    """
    if b is None:
        return 0, 1
    n = max(len(a.payload), len(b.payload))
    pa = a.payload.ljust(n, b"\x00")
    pb = b.payload.ljust(n, b"\x00")
    return sum(1 for x, y in zip(pa, pb) if x != y), 0


HEADER_TYPES = (1, 3, 4)          # PRG (relocatable, fixed) and SEQ headers
SEQ_DATA_TYPE = 2
END_OF_TAPE_TYPE = 5


def _best_copy(first, repeat):
    """(block, from_repeat): the first copy, unless it fails its checksum and
    the repeat passes."""
    if (not first.checksum_ok and repeat is not None
            and repeat.checksum_ok):
        return repeat, True
    return first, False


# The ROM writes 192-byte headers, but real tapes carry 191 (Activision's
# no-1541 releases) and 193 (Buggy Boy), so one byte either way is allowed.
HEADER_LENGTHS = (HEADER_PAYLOAD - 1, HEADER_PAYLOAD, HEADER_PAYLOAD + 1)
# Further from 192 (Tau Ceti's is 187), a block is only a header when the
# tape confirms it: its declared range matches the next block exactly.
CONFIRMABLE_LENGTHS = range(HEADER_PAYLOAD - 32, HEADER_PAYLOAD + 33)


def _range_of(payload):
    load = payload[1] | (payload[2] << 8)
    end = payload[3] | (payload[4] << 8)
    return ((end or 0x10000) - load) if (end == 0 or end > load) else None


def _is_header(block, repeat=None, following=None):
    """Whether a block pair is a ROM-loader header.

    Length alone is not enough: a SEQ data block is also 192 bytes, and 1.0.3
    read one as a header and swallowed the next file as its data. The type
    byte alone is too strict: custom loaders use the ROM format with their own
    header types (Krystals of Zong uses 7). So a header is header-sized and
    either carries a documented header type, or declares a range that the
    block after it matches exactly - the tape itself confirming it.
    """
    b, _ = _best_copy(block, repeat)
    p = b.payload
    if len(p) not in CONFIRMABLE_LENGTHS or p[0] == SEQ_DATA_TYPE:
        return False
    if (len(p) in HEADER_LENGTHS
            and p[0] in HEADER_TYPES + (END_OF_TAPE_TYPE,)):
        return True
    if following is None:
        return False
    data, _ = _best_copy(*following)
    span = _range_of(p)
    return span is not None and span == len(data.payload)


def _is_seq_data(block, repeat=None):
    b, _ = _best_copy(block, repeat)
    return len(b.payload) in HEADER_LENGTHS and b.payload[0] == SEQ_DATA_TYPE


def build_files(pairs):
    files = []
    i = 0
    while i < len(pairs):
        hdr_first, hdr_repeat = pairs[i]
        following = pairs[i + 1] if i + 1 < len(pairs) else None
        if not _is_header(hdr_first, hdr_repeat, following):
            i += 1                           # a loose block: counted, not a file
            continue
        hdr, _ = _best_copy(hdr_first, hdr_repeat)
        p = hdr.payload
        ftype = p[0]
        if ftype == END_OF_TAPE_TYPE:
            i += 1
            continue
        load = p[1] | (p[2] << 8)
        end = p[3] | (p[4] << 8)
        name = petscii_to_ascii(p[5:21])
        hdr_diff, hdr_missing = _compare_copies(hdr_first, hdr_repeat)

        if ftype == 4:
            # SEQ: every following type-2 block pair is data, first byte
            # excluded. The header's addresses are the cassette buffer.
            data_pairs = []
            j = i + 1
            while j < len(pairs) and _is_seq_data(*pairs[j]):
                data_pairs.append(pairs[j])
                j += 1
            if not data_pairs:
                i += 1
                continue
            chosen = [_best_copy(f, r) for f, r in data_pairs]
            data = b"".join(b.payload[1:] for b, _ in chosen)
            diffs = [_compare_copies(f, r) for f, r in data_pairs]
            (data_first, data_repeat) = data_pairs[0]
            files.append(CbmFile(
                name, ftype, load, end, data, hdr_first, data_first,
                hdr_repeat, data_repeat,
                hdr_missing + sum(m for _, m in diffs),
                hdr_diff + sum(d for d, _ in diffs),
                more_data=data_pairs[1:],
                data_from_repeat=any(r for _, r in chosen),
                data_ok=all(b.checksum_ok for b, _ in chosen)))
            i = j
            continue

        # PRG: the next block pair is its data - unless it is itself a
        # header, in which case this header lost its data and stays loose.
        # A header-shaped block whose length is exactly this header's range
        # is its data, whatever its first byte happens to be.
        if following is None:
            i += 1
            continue
        next_best, _ = _best_copy(*following)
        if (_is_header(following[0], following[1],
                       pairs[i + 2] if i + 2 < len(pairs) else None)
                and _range_of(p) != len(next_best.payload)):
            i += 1
            continue
        data_first, data_repeat = pairs[i + 1]
        chosen, from_repeat = _best_copy(data_first, data_repeat)
        data_diff, data_missing = _compare_copies(data_first, data_repeat)
        files.append(CbmFile(name, ftype, load, end, chosen.payload,
                             hdr_first, data_first,
                             hdr_repeat, data_repeat,
                             hdr_missing + data_missing,
                             hdr_diff + data_diff,
                             data_from_repeat=from_repeat,
                             data_ok=chosen.checksum_ok))
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


TOKEN_REM = 0x8F
TOKEN_SYS = 0x9E


class BasicLine(tuple):
    """(line number, text), carrying the raw tokenised bytes alongside.

    The entry point is read from the raw bytes, never from the text: in the
    text, a SYS token and the letters S-Y-S typed after a REM look the same.
    """

    def __new__(cls, number, text, raw=b""):
        line = super().__new__(cls, (number, text))
        line.raw = raw
        return line


def detokenize(data, load=0x0801):
    """Lines are [next-line pointer][line number][tokens][$00]; a next-line
    pointer of $0000 ends the program.

    Inside a string or after REM, bytes are literal, not tokens - the BASIC
    interpreter never expands them there. Anything unprintable in those
    stretches is shown as {XX} rather than as a keyword it is not.
    """
    lines = []
    i = 0
    while i + 4 <= len(data):
        nxt = data[i] | (data[i + 1] << 8)
        if nxt == 0:
            break
        number = data[i + 2] | (data[i + 3] << 8)
        i += 4
        start = i
        out = []
        literal = False                          # inside quotes
        rem = False
        while i < len(data) and data[i]:
            b = data[i]
            if literal or rem:
                out.append(chr(b) if 32 <= b < 127 else "{%02X}" % b)
            else:
                out.append(BASIC_TOKENS.get(
                    b, chr(b) if 32 <= b < 127 else "."))
                rem = b == TOKEN_REM
            if b == 0x22 and not rem:
                literal = not literal
            i += 1
        lines.append(BasicLine(number, "".join(out), bytes(data[start:i])))
        i += 1                                   # the line's terminating $00
    return lines


def _sys_constant(raw, j):
    """The address after a SYS token at raw[j], or None unless it is a plain
    constant ending the statement. SYS 12*4096 or SYS PEEK(43) is an
    expression this tool does not evaluate, and taking the leading digits
    of one states a wrong address as fact."""
    n = len(raw)

    def skip(k):
        while k < n and raw[k] == 0x20:
            k += 1
        return k

    j = skip(j)
    paren = j < n and raw[j] == 0x28
    if paren:
        j = skip(j + 1)
    digits = ""
    while j < n and (0x30 <= raw[j] <= 0x39 or (digits and raw[j] == 0x20)):
        if raw[j] != 0x20:                      # BASIC ignores spaces in numbers
            digits += chr(raw[j])
        j += 1
    if not digits:
        return None
    j = skip(j)
    if paren:
        if j >= n or raw[j] != 0x29:
            return None
        j = skip(j + 1)
    if j < n and raw[j] != 0x3A:                # anything but end or ':'
        return None
    value = int(digits)
    return value if value <= 0xFFFF else None


def find_sys(lines):
    """The address of the first SYS the program reaches, when it is a plain
    constant. Only the first: if that one is an expression, a later SYS is
    not the entry point either, so the answer is None rather than a guess."""
    for line in lines:
        raw = getattr(line, "raw", b"")
        literal = False
        for j, b in enumerate(raw):
            if b == 0x22:
                literal = not literal
            elif literal:
                continue
            elif b == TOKEN_REM:
                break
            elif b == TOKEN_SYS:
                return _sys_constant(raw, j + 1)
    return None


# ----------------------------------------------------------------- packer --
# Every entry here was observed in a file crunched locally with the named
# tool. Nothing in this table is written from memory: a packer table that
# guesses makes the dossier lie, which is the one thing it must not do.
# Re-derive with tools/derive_signatures.py.
#
# A signature must also be STABLE. The first derivation of the Exomizer
# entry below looked fine and was worthless: taken from a single sample, it
# included the address operands `$0962` and `$0914`, which move with the
# crunched file's size. It would have matched that one file and nothing
# else - the tool would have appeared to support Exomizer while silently
# finding none. The bytes kept here are the ones that stayed identical
# across four genuinely different crunched files.
PACKER_SIGNATURES = [
    {"name": "Exomizer 3.x (sfx sys)",
     # The SFX BASIC stub (10 SYS 2061) plus the first two bytes of the
     # decruncher. Byte 14 onward is an address operand and varies.
     "pattern": bytes.fromhex("0b0837019e32303631000000babd"),
     "max_offset": 4,
     "source": "exomizer 3.x sfx sys, win32 build; bytes identical across 4 "
               "crunched samples of differing size, derived with "
               "tools/derive_signatures.py"},
]


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


def build_checks(header, pulses, regions, files, tapclean_used, loaders=None,
                 blocks=None, seg_stats=None):
    """`blocks` is every block the decoder recovered, not only those that
    were assembled into files.

    Verifying only the blocks belonging to files meant a block that decoded
    but never paired into one vanished from the report: its failing checksum
    was never mentioned, and the dossier stated that no blocks were found at
    all. Both were false, and on real tapes both happened at once."""
    checks = []

    checks.append(Check(
        "TAP signature", PASS,
        "%s, version %d" % (header.signature.decode("ascii"),
                            header.version)))

    forced = ""
    if header.video_forced_by:
        forced = (". Durations use %s timing, set by %s on the command line"
                  % (header.video, header.video_forced_by))
    if header.platform_known and header.video_known:
        checks.append(Check(
            "Header platform and timing", PASS,
            "%s, %s, as stated by the header%s"
            % (header.platform, VIDEO[header.video_byte], forced)))
    else:
        unknown = []
        if header.platform_byte not in PLATFORMS:
            unknown.append("platform byte $%02X names no known machine"
                           % header.platform_byte)
        elif not header.platform_known:
            unknown.append("the %s signature contradicts platform byte $%02X "
                           "(%s)" % (header.signature.decode("ascii"),
                                     header.platform_byte, header.platform))
        if not header.video_known:
            unknown.append("video byte $%02X names neither PAL nor NTSC"
                           % header.video_byte)
        if header.video_forced_by:
            tail = forced[2:] + "."
        else:
            tail = ("Every duration in this document is therefore computed "
                    "from an assumed %s %s clock, not from anything the file "
                    "states. Override with --pal or --ntsc if you know better."
                    % (header.platform, header.video))
        checks.append(Check(
            "Header platform and timing", NOT_CHECKED,
            "%s. %s" % ("; ".join(unknown), tail)))

    if header.length_mismatch == 0:
        checks.append(Check(
            "Header length vs actual data", PASS,
            "declared %d bytes, file holds %d"
            % (header.declared_length, header.actual_length)))
    elif header.length_mismatch > 0:
        checks.append(Check(
            "Header length vs actual data", FAIL,
            "header says %d bytes, file holds %d (%+d). Cosmetic; repairable "
            "with `tapclean -rs`."
            % (header.declared_length, header.actual_length,
               header.length_mismatch)))
    else:
        # Fewer bytes than declared is not cosmetic: either the end of the
        # recording is missing, or the header is wrong, and nothing in the
        # file says which.
        checks.append(Check(
            "Header length vs actual data", FAIL,
            "header says %d bytes, file holds %d (%+d). Data may be missing "
            "from the end of the file, or the header is wrong; the file "
            "alone cannot say which."
            % (header.declared_length, header.actual_length,
               header.length_mismatch)))

    overflows = sum(1 for p in pulses if p.overflow)
    checks.append(Check(
        "Pulse stream integrity", PASS,
        "%d pulses, none truncated%s"
        % (len(pulses),
           (", %d of unrecorded length (version 0 overflow)" % overflows)
           if overflows else "")))

    in_files = [b for f in files for b in f.all_blocks]
    all_blocks = list(blocks) if blocks is not None else in_files
    if not all_blocks:
        checks.append(Check(
            "CBM block checksums", NOT_CHECKED,
            "no CBM ROM-loader blocks were found on this tape"))
    else:
        bad = [b for b in all_blocks if not b.checksum_ok]
        if bad:
            checks.append(Check(
                "CBM block checksums", FAIL,
                "%d of %d decoded blocks fail: %s"
                % (len(bad), len(all_blocks),
                   ", ".join("expected $%02X, computed $%02X"
                             % (b.checksum_stored, b.checksum_computed)
                             for b in bad))))
        else:
            checks.append(Check("CBM block checksums", PASS,
                                "all %d decoded blocks pass" % len(all_blocks)))

        loose = len(all_blocks) - len(in_files)
        if loose > 0:
            checks.append(Check(
                "Block structure", NOT_CHECKED,
                "%d of %d decoded block(s) do not form a complete file (a "
                "header paired with its data), so they are not listed under "
                "Files. Their checksums are included in the result above. "
                "This is normal on a tape whose CBM section is only a "
                "bootstrap for a turbo loader."
                % (loose, len(all_blocks))))
        else:
            checks.append(Check(
                "Block structure", PASS,
                "all %d decoded block(s) belong to a complete file"
                % len(all_blocks)))

        differing = [f for f in files if f.disagreement_count]
        missing = [f for f in files if f.missing_repeats]
        if not files:
            # "all 0 file(s) agree" is a pass for a comparison that had no
            # subject. Blocks decoded but none were assembled into a file.
            checks.append(Check(
                "First copy vs repeat", NOT_CHECKED,
                "no complete file was assembled from the %d decoded block(s), "
                "so no first/repeat comparison was possible"
                % len(all_blocks)))
        elif differing:
            note = ("%d file(s) differ between the two recorded copies: %s"
                    % (len(differing),
                       ", ".join("%s (%d byte(s))" % (f.name,
                                                      f.disagreement_count)
                                 for f in differing)))
            if missing:
                note += (". A further %d file(s) carry no repeat at all and "
                         "could not be compared." % len(missing))
            checks.append(Check("First copy vs repeat", FAIL, note))
        elif missing:
            # Not a pass. There was nothing to compare against.
            checks.append(Check(
                "First copy vs repeat", NOT_CHECKED,
                "%d of %d file(s) carry no repeat copy on this tape, so the "
                "two copies could not be compared: %s"
                % (len(missing), len(files),
                   ", ".join(f.name for f in missing))))
        else:
            checks.append(Check("First copy vs repeat", PASS,
                                "all %d file(s) agree" % len(files)))

        if files:
            # The header states a load and an end address; the data block
            # carries the bytes. Printing both without checking they agree
            # puts two numbers in the dossier that cannot both be true.
            # SEQ headers carry the cassette buffer's addresses, not a range
            # the data occupies, so there is nothing of theirs to compare.
            prgs = [f for f in files if not f.is_seq]
            wrong = []
            for f in prgs:
                label = f.name or "(unnamed)"
                if f.end != 0 and f.end < f.load:
                    wrong.append("%s: header end $%04X is below its load "
                                 "address $%04X" % (label, f.end, f.load))
                elif f.span != f.size:
                    wrong.append("%s: header claims %d bytes ($%04X-$%04X), "
                                 "%d recovered"
                                 % (label, f.span, f.load, f.end, f.size))
            seq_note = ("; %d SEQ file(s) carry no address range to check"
                        % (len(files) - len(prgs))) if len(prgs) < len(files) \
                else ""
            if wrong:
                checks.append(Check("File length vs header range", FAIL,
                                    "; ".join(wrong) + seq_note))
            elif prgs:
                checks.append(Check(
                    "File length vs header range", PASS,
                    "all %d PRG file(s) carry exactly the bytes their header "
                    "declares%s" % (len(prgs), seq_note)))
            else:
                checks.append(Check(
                    "File length vs header range", NOT_CHECKED,
                    seq_note[2:]))

        parity = sum(b.parity_errors for b in all_blocks)
        checks.append(Check(
            "Byte parity", PASS if parity == 0 else FAIL,
            "no parity errors" if parity == 0
            else "%d byte(s) carry a bad parity bit" % parity))

    turbo = [r for r in regions if r.kind == "turbo"]
    if turbo:
        if loaders:
            why = ("TAPClean names the loader (%s), but tap2pdf has no "
                   "checksum model for it, so this data was not verified "
                   "here. TAPClean's own report carries its checksum test."
                   % ", ".join(loaders))
        else:
            why = ("The format is unidentified, so there is no checksum "
                   "model to verify them against.")
        checks.append(Check(
            "Turbo region integrity", NOT_CHECKED,
            "%d turbo region(s), %d pulses. %s"
            % (len(turbo), sum(r.pulse_count for r in turbo), why)))

    # Measured from the raw per-window verdicts, not from the merged table.
    # Merging is what makes the Regions table readable, but it must not be
    # allowed to quietly erase how much of the tape was never understood.
    raw_unclassified = (seg_stats or {}).get("unclassified_pulses", 0)
    total_pulses = (seg_stats or {}).get("total_pulses", 0) or len(pulses)
    if raw_unclassified:
        checks.append(Check(
            "Unclassified stretches", NOT_CHECKED,
            "%d pulses (%.1f%% of the tape) match no pulse pattern this tool "
            "recognises. They are folded into neighbouring regions in the "
            "table above so it stays readable, but nothing here analysed "
            "them." % (raw_unclassified,
                       100.0 * raw_unclassified / max(1, total_pulses))))

    if tapclean_used and loaders:
        checks.append(Check(
            "Loader identification", PASS,
            "identified from the supplied TAPClean report: "
            + ", ".join(loaders)))
    elif tapclean_used and not turbo:
        # Two independent readings agree: TAPClean found no turbo loader,
        # and no turbo region was found here either.
        checks.append(Check(
            "Loader identification", PASS,
            "the supplied TAPClean report names no turbo loader, and none "
            "of this tape was classified as turbo: the CBM ROM loader only"))
    elif tapclean_used:
        checks.append(Check(
            "Loader identification", NOT_CHECKED,
            "the supplied TAPClean report names no loader, but %d turbo "
            "region(s) were found here, so the loader remains unidentified"
            % len(turbo)))
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
    elif not files:
        checks.append(Check(
            "Cruncher identification", NOT_CHECKED,
            "no files were recovered, so there was nothing to check against "
            "the %d compiled signature(s)" % len(PACKER_SIGNATURES)))
    else:
        matched = []
        for f in files:
            found = identify_packer(f.data, f.load)
            if found:
                matched.append("%s: %s" % (f.name, found["name"]))
        if matched:
            checks.append(Check("Cruncher identification", PASS,
                                "; ".join(matched)))
        else:
            # NOT "unpacked". Only that nothing we can recognise was found.
            checks.append(Check(
                "Cruncher identification", NOT_CHECKED,
                "no match among the %d compiled signature(s). This does not "
                "mean the files are uncrunched - only that no cruncher this "
                "build knows about was recognised."
                % len(PACKER_SIGNATURES)))

    return checks


def verdict(checks, regions, files=None):
    """One plain sentence that never outruns the evidence.

    The `files` argument is not decoration. Without it this said "the CBM
    portion of this tape reads cleanly" whenever any region merely LOOKED
    CBM-shaped - even when not one complete block had been decoded from it,
    and the checksum row in the same table said NOT CHECKED. Four real tapes
    out of forty-nine produced that contradiction. Pulses that look like a
    ROM loader are not the same as data that was read.
    """
    failed = [c for c in checks if c.result == FAIL]
    unchecked = [c for c in checks if c.result == NOT_CHECKED]
    has_cbm = any(r.kind == "cbm" for r in regions)
    decoded = len(files) if files is not None else None
    parts = []
    if failed:
        parts.append("This tape has %d failing check(s): %s."
                     % (len(failed), ", ".join(c.name.lower()
                                               for c in failed)))
    elif has_cbm and decoded:
        parts.append("The CBM portion of this tape reads cleanly.")
    elif any(c.name == "CBM block checksums" and c.result != NOT_CHECKED
             for c in checks):
        # Blocks WERE decoded - saying none could be would be false. They
        # just never formed a header followed by its data.
        parts.append("CBM ROM-loader blocks were decoded, but none of them "
                     "forms a complete file (a header followed by its data), "
                     "so no file has been read.")
    elif has_cbm:
        parts.append("Pulses shaped like the CBM ROM loader are present, but "
                     "no complete block could be decoded from them, so "
                     "nothing here has been read.")
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
    for i, f in enumerate(fl for fl in files if not fl.is_seq):
        x = f.load * scale
        w = max(2.0, min(f.span, 0x10000 - f.load) * scale)
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
        header.video_forced_by = "--pal"
    elif getattr(args, "ntsc", False):
        header.video = "NTSC"
        header.video_forced_by = "--ntsc"
    pulses = decode_pulses(data, header)
    seg_stats = {}
    regions = segment(pulses, seg_stats)
    blocks = decode_cbm_blocks(pulses)
    files = build_files(pair_blocks(blocks))
    sys_entry = None
    # Keyed by position in `files`, not by name: two files on one tape can
    # share a name, and a cruncher found in one is not in the other.
    packers = {}
    for i, f in enumerate(files):
        if f.load == 0x0801 and f.ftype in (1, 3) and sys_entry is None:
            sys_entry = find_sys(detokenize(f.data))
        found = identify_packer(f.data, f.load)
        if found:
            packers[i] = found
    report_path = getattr(args, "tapclean", None)
    loaders = []
    if report_path:
        loaders = read_tapclean_report(report_path, len(data))["loaders"]
    checks = build_checks(header, pulses, regions, files,
                          tapclean_used=bool(report_path), loaders=loaders,
                          blocks=blocks, seg_stats=seg_stats)
    return Dossier(
        title=getattr(args, "title", None) or os.path.basename(args.tape),
        header=header, regions=regions, files=files, checks=checks,
        verdict_text=verdict(checks, regions, files), sys_entry=sys_entry,
        packers=packers,
        loaders=loaders,
        duration=seconds(total_cycles(pulses), header),
        pulse_count=len(pulses),
        enrichments={
            "TAPClean report": bool(getattr(args, "tapclean", None)),
            # Never true until a screenshot is actually taken. Asking for one
            # with --vice is not the same as having one.
            "VICE screenshot": False,
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
    return _html.escape(str(text), quote=True)


def timing_label(header):
    """"C64, PAL" - marked as assumed when the file did not say.

    Every duration in the document is computed from this clock. Printing it
    plainly in the headline while only the check table admits it was guessed
    states an assumption as a fact in the most prominent line on the page.
    """
    if header.video_forced_by:
        return "%s, %s (%s)" % (header.platform, header.video,
                                header.video_forced_by)
    if header.timing_is_assumed:
        return "%s, %s (assumed)" % (header.platform, header.video)
    return "%s, %s" % (header.platform, header.video)


def render_html(d):
    check_rows = []
    for c in d.checks:
        check_rows.append(
            '<tr><td>%s</td><td class="%s">%s</td><td>%s</td></tr>'
            % (_h(c.name), c.result.replace(" ", ""), _h(c.result),
               _h(c.detail)))

    file_rows = []
    for i, f in enumerate(d.files):
        packer = d.packers.get(i)
        note = ("%s at +%d" % (packer["name"], packer["offset"])) if packer \
            else ""
        file_rows.append(
            '<tr><td>%s</td><td>%s</td><td class="num">$%04X</td>'
            '<td class="num">$%04X</td><td class="num">%d</td>'
            '<td class="%s">%s</td><td>%s</td></tr>'
            % (_h(f.name), _h(f.type_name), f.load, f.end, f.size,
               "PASS" if f.data_ok else "FAIL", _h(f.sum_label), _h(note)))
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

    entry = ("$%04X (SYS %d)" % (d.sys_entry, d.sys_entry)) \
        if d.sys_entry is not None \
        else "no plain SYS constant found in a BASIC stub"

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
        '<p class="sub">%s &middot; %.2f seconds &middot; '
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
        % (_h(d.title), CSS, _h(d.title), _h(timing_label(d.header)),
           d.duration, d.pulse_count, screenshot,
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
           "%s / %.2f seconds / %d pulses"
           % (_ascii(timing_label(d.header)), d.duration, d.pulse_count),
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
                          f.load, f.end, f.size, _ascii(f.sum_label)))
    else:
        out.append("(no CBM ROM-loader files were recovered)")
    if d.sys_entry is not None:
        out += ["", "Entry point: $%04X (SYS %d)" % (d.sys_entry, d.sys_entry)]
    out += ["",
            "Generated by tap2pdf " + __version__ + ".",
            "A check that did not run is shown as NOT CHECKED, never as a "
            "pass."]
    return "\n".join(line[:NFO_WIDTH] for line in out) + "\n"


def extract_files(d, directory, tape_path=None):
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError as exc:
        raise Refusal(EXIT_OUTPUT, "cannot create %s: %s" % (directory, exc))
    # Work out every target first and check them all before writing any, so a
    # refusal on the second file does not leave the first one on disk.
    targets = []
    for i, f in enumerate(d.files):
        safe = "".join(ch if ch.isalnum() else "_" for ch in f.name) or "file"
        targets.append((f, os.path.join(
            directory, "%02d_%s.%s" % (i + 1, safe,
                                       "seq" if f.is_seq else "prg"))))
    if tape_path is not None:
        for _f, path in targets:
            refuse_if_clobbers_input(path, tape_path, "extracted file")

    written = []
    for f, path in targets:
        try:
            with open(path, "wb") as fh:
                if not f.is_seq:             # a SEQ file has no load address
                    fh.write(bytes([f.load & 0xFF, (f.load >> 8) & 0xFF]))
                fh.write(f.data)
        except OSError as exc:
            raise Refusal(EXIT_OUTPUT, "cannot write %s: %s" % (path, exc))
        written.append(path)
    return written


# ----------------------------------------------------------------- enrich --
# Optional. Every failure here is reported and never faked, and the HTML is
# always written first so a failed enrichment cannot cost you the dossier.
BROWSER_CANDIDATES = [
    "msedge", "chrome", "chromium", "google-chrome", "chromium-browser",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


TAPCLEAN_NO_LOADER = ("n/a", "none", "unknown", "-")


def _field(line, name):
    """The value of a 'Name   : value' line, or None. Matches TAPClean's
    report (tcreport.txt, 'Loader ID   : Visiload T2') and its console
    output ('  Loader ID: Visiload T2.')."""
    key, sep, value = line.partition(":")
    if not sep or " ".join(key.split()).lower() != name:
        return None
    return value.strip().rstrip(".").strip()


def parse_tapclean_report(text):
    """What a TAPClean report says, taken from the fields TAPClean really
    writes.

    1.0.3 looked for 'Loader:' and 'Loader detected:', which TAPClean never
    writes. A real report named no loader to it, and the dossier said so as
    a PASS - a false statement on every tape it was ever given.
    """
    loaders = []
    lines = []
    said_none = False
    tap_size = None
    is_tapclean = False
    for raw in str(text).splitlines():
        lines.append(raw)
        if "tapclean" in raw.lower():
            is_tapclean = True
        value = _field(raw, "loader id")
        if value is not None:
            if value.lower() in TAPCLEAN_NO_LOADER or not value:
                said_none = True
            elif value not in loaders:
                loaders.append(value)
        size = _field(raw, "tap size")
        if size is not None and size.split() and size.split()[0].isdigit():
            tap_size = int(size.split()[0])
    return {"loaders": loaders, "says_no_loader": said_none and not loaders,
            "tap_size": tap_size, "is_tapclean": is_tapclean,
            "raw_lines": lines}


def read_tapclean_report(path, tape_size=None):
    """A report that is not recognisably TAPClean's, or that describes a
    tape of a different size, is refused: ingesting it would put another
    file's findings - or none - into this tape's dossier."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            report = parse_tapclean_report(fh.read())
    except OSError as exc:
        raise Refusal(EXIT_ENRICH,
                      "cannot read the TAPClean report %s: %s" % (path, exc))
    if not report["is_tapclean"] or not (report["loaders"]
                                         or report["says_no_loader"]):
        raise Refusal(EXIT_ENRICH,
                      "%s does not look like a TAPClean report: it has no "
                      "'Loader ID' line. Generate one with `tapclean -t "
                      "<tape>` and pass the tcreport.txt it writes."
                      % path)
    if (tape_size is not None and report["tap_size"] is not None
            and report["tap_size"] != tape_size):
        raise Refusal(EXIT_ENRICH,
                      "the TAPClean report %s is for a different tape: it "
                      "describes %d bytes, this tape is %d"
                      % (path, report["tap_size"], tape_size))
    return report


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


# The VICE title screenshot is not wired up. There is deliberately no stub
# function for it: an empty one that returns None reads like an
# implementation and invites someone to call it. The dossier's provenance
# block reports the screenshot as "not used", which is the honest state.


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


def _positive_mb(text):
    """--max-size: a finite number above zero. 'inf' and 'nan' parse as
    floats and then crashed the size check with a traceback."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError("not a number: %r" % text)
    if not (0 < value < float("inf")):
        raise argparse.ArgumentTypeError(
            "must be a size in MB above zero, got %r" % text)
    return value


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
    p.add_argument("--max-size", type=_positive_mb,
                   default=DEFAULT_MAX_INPUT_MB, metavar="MB",
                   help="refuse an input larger than this (default %d MB)"
                        % DEFAULT_MAX_INPUT_MB)
    clock = p.add_mutually_exclusive_group()
    clock.add_argument("--pal", action="store_true", help="force PAL timing")
    clock.add_argument("--ntsc", action="store_true",
                       help="force NTSC timing")
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


def _same_path(a, b):
    """True when two paths name the same file.

    samefile() is the reliable answer but needs both to exist, and the output
    usually does not yet. The normalised-absolute comparison is the fallback,
    and normcase matters on Windows where TAPE.TAP and tape.tap are one file.
    """
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return (os.path.normcase(os.path.abspath(a))
            == os.path.normcase(os.path.abspath(b)))


def refuse_if_clobbers_input(out_path, tape_path, what):
    """The one thing this tool must never do.

    Reported by an external reviewer: passing the tape's own path to -o
    replaced it with HTML and exited 0, destroying the file the tool was
    asked to examine - while the README promises the tape is never written.
    Checked before every write, not only the one that was reported.
    """
    if _same_path(out_path, tape_path):
        raise Refusal(
            EXIT_OUTPUT,
            "refusing to write the %s over the input tape (%s). tap2pdf "
            "never writes to the tape it is reading. Choose a different "
            "output path." % (what, os.path.basename(tape_path)))


def _write_text(path, text):
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except OSError as exc:
        raise Refusal(EXIT_OUTPUT, "cannot write %s: %s" % (path, exc))


def plan_outputs(args):
    """(html, nfo or None, pdf or None), every one checked before anything
    is written.

    The NFO and PDF sit beside the HTML under its stem. In 1.0.3 that meant
    `-o x.nfo --nfo` wrote the HTML and then the NFO over it, and
    `--pdf-only -o x.pdf` rendered the PDF over its own source and then
    deleted it - exit 0, nothing on disk. Two outputs on one path is now a
    usage error, except for the one case where the intent is plain:
    --pdf-only with a .pdf path names the PDF itself.
    """
    out = args.output or default_output(args.tape)
    pdf_path = None
    if args.pdf_only and out.lower().endswith(".pdf"):
        pdf_path = out
        html_path = os.path.splitext(out)[0] + ".tap2pdf-tmp.html"
    else:
        html_path = out
        if args.pdf or args.pdf_only:
            pdf_path = os.path.splitext(html_path)[0] + ".pdf"
    nfo_path = (os.path.splitext(pdf_path or html_path)[0] + ".nfo"
                if args.nfo else None)

    named = [("HTML dossier", html_path), ("NFO", nfo_path),
             ("PDF", pdf_path)]
    named = [(what, p) for what, p in named if p]
    for what, p in named:
        refuse_if_clobbers_input(p, args.tape, what)
    for i, (what_a, a) in enumerate(named):
        for what_b, b in named[i + 1:]:
            if _same_path(a, b):
                raise Refusal(
                    EXIT_USAGE,
                    "the %s and the %s would both be written to %s. -o names "
                    "the HTML dossier; the NFO and PDF are written beside it "
                    "as <name>.nfo and <name>.pdf. Give -o a .html path."
                    % (what_a, what_b, a))
    return html_path, nfo_path, pdf_path


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        if not os.path.isfile(args.tape):
            raise Refusal(EXIT_INPUT, "cannot read: " + args.tape)
        refuse_if_too_large(os.path.getsize(args.tape), args.max_size,
                            os.path.basename(args.tape))
        try:
            with open(args.tape, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise Refusal(EXIT_INPUT, "cannot read %s: %s" % (args.tape, exc))

        dossier = analyse(data, args)

        html_path, nfo_path, pdf_path = plan_outputs(args)
        _write_text(html_path, render_html(dossier))
        if not args.quiet and not args.pdf_only:
            sys.stderr.write("wrote %s\n" % html_path)

        if nfo_path:
            _write_text(nfo_path, render_nfo(dossier))
            if not args.quiet:
                sys.stderr.write("wrote %s\n" % nfo_path)

        if args.extract:
            written = extract_files(dossier, args.extract, args.tape)
            if not args.quiet:
                sys.stderr.write("extracted %d file(s) to %s\n"
                                 % (len(written), args.extract))
                for f, path in zip(dossier.files, written):
                    if f.data_from_repeat:
                        sys.stderr.write(
                            "  %s: taken from the repeat copy - the first "
                            "copy fails its checksum\n"
                            % os.path.basename(path))
                    elif not f.data_ok:
                        sys.stderr.write(
                            "  %s: fails its checksum in every copy on the "
                            "tape; written as recorded\n"
                            % os.path.basename(path))

        if pdf_path:
            try:
                render_pdf(html_path, pdf_path, args.browser)
            except Refusal as r:
                if args.pdf_only:
                    # The HTML was only ever a step towards the PDF, but
                    # with no PDF it is the one result there is: keep it
                    # where it can be found, under its own name.
                    kept = os.path.splitext(pdf_path)[0] + ".html"
                    if (not os.path.exists(kept)
                            and not _same_path(kept, args.tape)):
                        os.replace(html_path, kept)
                        html_path = kept
                    r.message = r.message.replace(
                        "The HTML dossier was still written.",
                        "The HTML dossier was kept as %s." % html_path)
                raise
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

        if args.vice:
            raise Refusal(
                EXIT_ENRICH,
                "--vice was requested, but the VICE title screenshot is not "
                "implemented in this version. Everything else was written; "
                "the dossier's provenance says the screenshot was not used.")

        return EXIT_OK
    except Refusal as r:
        sys.stderr.write("tap2pdf: " + r.message + "\n")
        return r.code


if __name__ == "__main__":
    sys.exit(main())
