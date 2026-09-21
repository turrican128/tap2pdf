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

    write("truncated.tap",
          make_tap(cbm_file("HELLO", 0x0801, hello) + b"\x00\x01"))


if __name__ == "__main__":
    sys.exit(main())
