#!/usr/bin/env python3
"""Derive a cruncher signature from a file we crunch ourselves.

Never write a signature from memory. A packer table that guesses makes the
dossier lie about what is on someone's tape, which is the one thing it must
not do. Run this, read the bytes it prints, paste the entry into
PACKER_SIGNATURES in tap2pdf.py.

A developer aid: it is not part of CI, and it exits 0 when the packer is not
installed, because not having exomizer is not an error.
"""
import argparse
import pathlib
import shutil
import subprocess
import sys
import tempfile

EXOMIZER_HINTS = [
    r"C:\ClaudeSandbox\TapClean\trainer\tools\exomizer\src\exomizer.exe",
    r"C:\ClaudeSandbox\TapClean\trainer\tools\exomizer\src\exomizer",
    "exomizer",
]


def find_exomizer(explicit=None):
    for hint in ([explicit] if explicit else []) + EXOMIZER_HINTS:
        found = shutil.which(hint)
        if found:
            return found
        if pathlib.Path(hint).is_file():
            return hint
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--exomizer", help="path to exomizer")
    p.add_argument("--bytes", type=int, default=12,
                   help="how many bytes of signature to print (default 12)")
    args = p.parse_args(argv)

    exo = find_exomizer(args.exomizer)
    if not exo:
        print("exomizer not found - nothing to derive. This is not an error.")
        print("Pass --exomizer <path> if you have it somewhere else.")
        return 0

    print("using %s" % exo)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        src = tmp / "sample.prg"
        # A compressible payload of our own, behind a real '10 SYS 2064'
        # BASIC stub - `exomizer sfx sys` refuses input without one, and a
        # real crunched game has one anyway. Never a game file.
        import struct
        line = b"\x9e" + b"2064" + b"\x00"
        basic = struct.pack("<HH", 0x0801 + 4 + len(line), 10) + line
        basic += b"\x00\x00"
        basic += b"\x00" * (0x0810 - (0x0801 + len(basic)))
        src.write_bytes(b"\x01\x08" + basic + bytes(range(256)) * 8)
        out = tmp / "packed.prg"
        try:
            subprocess.run([exo, "sfx", "sys", str(src), "-o", str(out)],
                           check=True, capture_output=True, timeout=120)
        except Exception as exc:
            print("exomizer failed: %s" % exc)
            return 0
        packed = out.read_bytes()

    body = packed[2:2 + max(args.bytes, 32)]
    print()
    print("first %d bytes after the load address:" % len(body))
    print("  " + " ".join("%02X" % b for b in body))
    print()
    print("paste into PACKER_SIGNATURES in tap2pdf.py, and keep the source "
          "line:")
    print('    {"name": "Exomizer (sfx sys)",')
    print('     "pattern": bytes.fromhex("%s"),'
          % "".join("%02x" % b for b in body[:args.bytes]))
    print('     "max_offset": 64,')
    print('     "source": "exomizer sfx sys, local build, derived with '
          'tools/derive_signatures.py"},')
    print()
    print("Then run the tests: a signature without a source line fails them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
