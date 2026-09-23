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


def test_the_output_path_may_not_be_the_input_tape(tmp_path):
    # Reported by an external reviewer. Passing the tape's own path to -o
    # replaced it with HTML and exited 0, destroying the thing the tool was
    # asked to examine while the README promises it is never written.
    victim = tmp_path / "victim.tap"
    original = (FIXTURES / "clean_single.tap").read_bytes()
    victim.write_bytes(original)

    r = run(str(victim), "-o", str(victim))
    assert r.returncode == tap2pdf.EXIT_OUTPUT, r.stderr
    assert victim.read_bytes() == original, "the input tape was modified"


def test_a_relative_path_to_the_same_tape_is_also_refused(tmp_path):
    victim = tmp_path / "victim.tap"
    original = (FIXTURES / "clean_single.tap").read_bytes()
    victim.write_bytes(original)

    r = subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"),
                        str(victim), "-o", "./victim.tap"],
                       capture_output=True, text=True, cwd=str(tmp_path))
    assert r.returncode == tap2pdf.EXIT_OUTPUT, r.stderr
    assert victim.read_bytes() == original


def test_extraction_may_not_write_over_the_input_tape(tmp_path):
    # The extracted PRGs are named NN_NAME.prg. A tape that happens to carry
    # such a name, extracted into its own folder, would be overwritten.
    victim = tmp_path / "01_PART_ONE.prg"
    original = (FIXTURES / "two_files.tap").read_bytes()
    victim.write_bytes(original)

    r = run(str(victim), "-o", str(tmp_path / "out.html"),
            "--extract", str(tmp_path))
    assert r.returncode == tap2pdf.EXIT_OUTPUT, r.stderr
    assert victim.read_bytes() == original


def test_the_cli_never_writes_to_the_tape_on_a_normal_run(tmp_path):
    # The old test of this only wrapped analyse(), which does no writing at
    # all. The write happens in main(), so the test proved nothing.
    tape = tmp_path / "t.tap"
    original = (FIXTURES / "two_files.tap").read_bytes()
    tape.write_bytes(original)
    r = run(str(tape), "-o", str(tmp_path / "ok.html"), "--nfo",
            "--extract", str(tmp_path / "out"))
    assert r.returncode == 0, r.stderr
    assert tape.read_bytes() == original
