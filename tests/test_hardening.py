import builtins
import struct
import subprocess
import sys
import time

from conftest import FIXTURES, ROOT
import tap2pdf


def run(*args):
    return subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"), *args],
                          capture_output=True, text=True)


def test_the_input_tape_is_never_opened_for_writing(monkeypatch):
    real_open = builtins.open
    opened = []

    def watched(path, mode="r", *a, **kw):
        opened.append((str(path), mode))
        return real_open(path, mode, *a, **kw)

    monkeypatch.setattr(builtins, "open", watched)
    p = FIXTURES / "clean_single.tap"
    args = tap2pdf.build_parser().parse_args([str(p)])
    tap2pdf.analyse(p.read_bytes(), args)
    for path, mode in opened:
        if path.lower().endswith(".tap"):
            assert "w" not in mode and "a" not in mode and "+" not in mode


def test_a_truncated_tape_is_refused_with_exit_4():
    r = run(str(FIXTURES / "truncated.tap"))
    assert r.returncode == tap2pdf.EXIT_MALFORMED


def test_a_random_file_is_refused_as_not_a_tap(tmp_path):
    junk = tmp_path / "junk.tap"
    junk.write_bytes(b"\x00" * 4096)
    r = run(str(junk))
    assert r.returncode == tap2pdf.EXIT_NOT_TAP


def test_an_empty_file_is_refused(tmp_path):
    empty = tmp_path / "empty.tap"
    empty.write_bytes(b"")
    r = run(str(empty))
    assert r.returncode == tap2pdf.EXIT_NOT_TAP


def test_a_header_with_no_pulses_is_refused(tmp_path):
    bare = tmp_path / "bare.tap"
    bare.write_bytes(b"C64-TAPE-RAW" + bytes([1, 0, 0, 0])
                     + struct.pack("<I", 0))
    r = run(str(bare))
    assert r.returncode == tap2pdf.EXIT_MALFORMED


def test_a_pathological_tape_finishes_quickly():
    # All-zero v0 body: every byte an unmeasured overflow pulse.
    body = b"\x00" * 400000
    data = (b"C64-TAPE-RAW" + bytes([0, 0, 0, 0])
            + struct.pack("<I", len(body)) + body)
    h = tap2pdf.parse_header(data)
    start = time.time()
    pulses = tap2pdf.decode_pulses(data, h)
    tap2pdf.segment(pulses)
    assert time.time() - start < 20


def test_an_unwritable_output_path_exits_5(tmp_path):
    r = run(str(FIXTURES / "clean_single.tap"),
            "-o", str(tmp_path / "no" / "such" / "dir" / "x.html"))
    assert r.returncode == tap2pdf.EXIT_OUTPUT


def test_a_directory_given_as_the_tape_is_refused(tmp_path):
    r = run(str(tmp_path))
    assert r.returncode == tap2pdf.EXIT_INPUT


def test_pulse_objects_use_slots():
    # Measured: 136 bytes per pulse without __slots__, 96 with. A 2.5 MB
    # tape decodes to ~2.4M pulses, so this is ~100 MB on a real tape.
    assert hasattr(tap2pdf.Pulse, "__slots__")
    p = tap2pdf.Pulse(384, False, 0)
    assert not hasattr(p, "__dict__")


def test_an_absurdly_large_input_is_refused_before_it_is_decoded(tmp_path):
    # Decoding amplifies a TAP by roughly 100x in memory. Without a limit a
    # large file takes the process down instead of producing a message.
    big = tmp_path / "huge.tap"
    big.write_bytes(b"C64-TAPE-RAW" + bytes([1, 0, 0, 0])
                    + struct.pack("<I", 8) + b"\x30" * 8)
    r = subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"), str(big),
                        "--max-size", "0"],
                       capture_output=True, text=True)
    assert r.returncode == tap2pdf.EXIT_INPUT
    assert "--max-size" in r.stderr


def test_the_size_limit_can_be_raised(tmp_path):
    src = FIXTURES / "clean_single.tap"
    out = tmp_path / "ok.html"
    r = subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"), str(src),
                        "-o", str(out), "--max-size", "64", "--quiet"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
