#!/usr/bin/env python3
"""Synthesise the test tapes.

Every tape here is made from PRGs we wrote ourselves. No game tape is ever
committed to this repo: they are copyrighted, and a public repo is not an
archive. Real tapes stay local and are used for manual checks only - see
tools/qa_sweep.py for running the tool over them.

This is the CBM ROM tape format as an encoder. Writing it this way round is
deliberate: it pins every detail down, and gives the decoder a round-trip
partner rather than a hand-typed byte blob.
"""
import pathlib
import struct
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures"

S, M, L = 0x30, 0x42, 0x56          # short, medium, long, in TAP byte units

# Real tapes use ~27136 and ~6656. Shorter here to keep fixtures small; the
# decoder must not depend on these counts, and nothing in it does.
LEADER_FIRST = 2000
LEADER_REPEAT = 500


def encode_bit(bit):
    return bytes([M, S]) if bit else bytes([S, M])


def encode_byte(value):
    """New-data marker, 8 bits LSB first, then an odd parity bit."""
    out = bytearray([L, M])
    ones = 0
    for i in range(8):
        bit = (value >> i) & 1
        ones += bit
        out += encode_bit(bit)
    out += encode_bit(0 if ones % 2 else 1)      # odd parity across 9 bits
    return bytes(out)


def encode_block(payload, countdown_start, leader, checksum_override=None):
    """Leader, 9 countdown sync bytes, payload, checksum, end-of-data marker.

    checksum_override exists so a fixture can carry a genuinely wrong
    checksum. Corrupting the payload alone does not do it: the checksum is
    computed from the payload, so it would simply follow the corruption and
    the tape would still verify.
    """
    out = bytearray([S] * leader)
    for i in range(9):                           # $89..$81, or $09..$01
        out += encode_byte(countdown_start - i)
    checksum = 0
    for b in payload:
        out += encode_byte(b)
        checksum ^= b
    out += encode_byte(checksum if checksum_override is None
                       else checksum_override)
    out += bytes([L, S])                         # end-of-data marker
    return bytes(out)


def encode_block_pair(payload, leader=LEADER_FIRST, corrupt_repeat=None):
    first = encode_block(payload, 0x89, leader)
    repeat_payload = bytearray(payload)
    if corrupt_repeat is not None:
        index, value = corrupt_repeat
        repeat_payload[index] = value
    repeat = encode_block(bytes(repeat_payload), 0x09, LEADER_REPEAT)
    return first + repeat


def cbm_header_block(name, load, end, ftype=3):
    """The 192-byte header payload."""
    p = bytearray(b"\x20" * 192)
    p[0] = ftype
    p[1:3] = struct.pack("<H", load)
    p[3:5] = struct.pack("<H", end)
    encoded = name.upper().encode("ascii")[:16]
    p[5:5 + len(encoded)] = encoded
    return bytes(p)


def cbm_file(name, load, body, ftype=3):
    """A header block pair followed by a data block pair."""
    end = load + len(body)
    return (encode_block_pair(cbm_header_block(name, load, end, ftype))
            + encode_block_pair(body))


def cbm_file_no_repeats(name, load, body, ftype=3):
    """A tape that carries only the first copy of each block.

    Real tapes exist where the repeat was never recorded, or where the second
    half of the tape is damaged past decoding. The absence of a repeat must
    never be reported as the two copies agreeing.
    """
    end = load + len(body)
    return (encode_block(cbm_header_block(name, load, end, ftype),
                         0x89, LEADER_FIRST)
            + encode_block(body, 0x89, LEADER_REPEAT))


def cbm_file_declaring(name, load, declared_end, body, ftype=3):
    """A file whose header claims an address range that does not match the
    data block that follows it. Real damaged headers do this, and a dossier
    that prints the claimed end beside the real size without noticing the
    contradiction is showing two numbers that cannot both be true."""
    return (encode_block_pair(cbm_header_block(name, load, declared_end, ftype))
            + encode_block_pair(body))


def make_tap(payload, version=1, platform=0, video=0):
    return (b"C64-TAPE-RAW" + bytes([version, platform, video, 0])
            + struct.pack("<I", len(payload)) + payload)


def basic_sys(target):
    """A tokenised '10 SYS <target>', without its two-byte load address."""
    line = b"\x9e" + str(target).encode("ascii") + b"\x00"   # $9E = SYS
    nxt = 0x0801 + 4 + len(line)
    return struct.pack("<HH", nxt, 10) + line + b"\x00\x00"


def turbo_region(blocks=40):
    """Two tight pulse clusters: a plausible turbo belonging to no real loader.

    The values sit well clear of the three CBM widths so the classifier cannot
    mistake this for a ROM-loader region, and so the CBM decoder finds nothing
    here to misread.
    """
    out = bytearray([0x20] * 800)                # turbo pilot
    for i in range(blocks * 64):
        out.append(0x18 if (i * 7) % 3 else 0x26)
    return bytes(out)


def wobble_region(windows=60, window=256):
    """One unchanging stretch of tape that the classifier used to shred.

    Taken from what a real tape actually does: a dominant short pulse with a
    secondary that drifts a couple of units either side of the CBM medium
    width, plus an occasional long pulse crossing the noise floor. Nothing
    about the tape changes, but the wobble crosses three separate
    classification thresholds, so each window was labelled differently and
    one region became hundreds.
    """
    out = bytearray()
    for w in range(windows):
        secondary = 0x48 if w % 2 == 0 else 0x4A   # inside / outside tolerance
        for i in range(window):
            if i % 32 == 0 and w % 3 == 0:
                out.append(0x7F)                   # a third cluster, sometimes
            elif i % 6 == 0:
                out.append(secondary)
            else:
                out.append(0x2E)
    return bytes(out)


def buried_unknown(window=256):
    """A long recognisable stretch with a single unrecognisable window in it.

    That lone window is shorter than the minimum run, so merging folds it
    into the leader around it and no "unclassified" region survives in the
    table. The verification report must still say those pulses were never
    analysed - readability of the table must not quietly raise the tool's
    confidence about the tape.
    """
    out = bytearray([0x2E] * (window * 10))
    for i in range(window):                  # three clusters: unrecognisable
        out.append((0x2E, 0x48, 0x7F)[i % 3])
    out += bytearray([0x2E] * (window * 10))
    return bytes(out)


def write(name, data):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(data)
    print("%-22s %8d bytes" % (name, len(data)))


def main():
    hello = bytes(range(0x20, 0x60))             # 64 bytes of our own

    write("clean_single.tap",
          make_tap(cbm_file("HELLO", 0x0801, hello)))

    write("two_files.tap",
          make_tap(cbm_file("PART ONE", 0x0801, hello)
                   + cbm_file("PART TWO", 0xC000, bytes(range(0x40)))))

    # A genuinely wrong checksum byte, not a corrupted payload.
    good = 0
    for b in hello:
        good ^= b
    write("bad_checksum.tap",
          make_tap(encode_block_pair(
              cbm_header_block("BADSUM", 0x0801, 0x0801 + len(hello)))
              + encode_block(hello, 0x89, LEADER_FIRST,
                             checksum_override=good ^ 0xFF)
              + encode_block(hello, 0x09, LEADER_REPEAT,
                             checksum_override=good ^ 0xFF)))

    write("copies_disagree.tap",
          make_tap(encode_block_pair(
              cbm_header_block("DISAGREE", 0x0801, 0x0801 + len(hello)))
              + encode_block_pair(hello, corrupt_repeat=(5, 0x99))))

    write("v0_overflow.tap",
          make_tap(cbm_file("V ZERO", 0x0801, hello) + b"\x00" * 4
                   + bytes([S] * 100), version=0))

    write("turbo_region.tap",
          make_tap(cbm_file("LOADER", 0x0801, basic_sys(2064), ftype=1)
                   + turbo_region()))

    write("basic_sys.tap",
          make_tap(cbm_file("SYSDEMO", 0x0801, basic_sys(2064), ftype=1)))

    write("no_repeats.tap",
          make_tap(cbm_file_no_repeats("LONELY", 0x0801, hello)))

    # Blocks that decode but never become a file: a data block pair with no
    # header in front of it, carrying a wrong checksum. Seen on real tapes
    # (Cobra, WWF) where the CBM portion is partial. The checksum failure
    # must still be reported, and the dossier must not claim no blocks were
    # found when four of them were decoded.
    write("orphan_blocks.tap",
          make_tap(encode_block(hello, 0x89, LEADER_FIRST,
                                checksum_override=good ^ 0xFF)
                   + encode_block(hello, 0x09, LEADER_REPEAT,
                                  checksum_override=good ^ 0xFF)))

    # Header claims $0801-$0900 (255 bytes); the data block carries 64.
    write("length_mismatch.tap",
          make_tap(cbm_file_declaring("SHORTFALL", 0x0801, 0x0900, hello)))

    # Header claims an end address BELOW its load address.
    write("reversed_range.tap",
          make_tap(cbm_file_declaring("BACKWARDS", 0x0900, 0x0801, hello)))

    # Platform and video bytes outside the documented values. All 49 real
    # tapes checked carry 0/0, so an unknown value here is genuinely odd and
    # must not be silently rendered as a confident "C64 / PAL".
    write("odd_header.tap",
          make_tap(cbm_file("ODDBALL", 0x0801, hello), platform=7, video=5))

    write("wobble.tap", make_tap(wobble_region()))

    write("buried_unknown.tap", make_tap(buried_unknown()))

    write("truncated.tap",
          make_tap(cbm_file("HELLO", 0x0801, hello) + b"\x00\x01"))


if __name__ == "__main__":
    sys.exit(main())
