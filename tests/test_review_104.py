"""Regressions from the 1.0.3 pre-release QA.

Each test here was written against a failure reproduced on 1.0.3, and most of
them are the dossier stating something untrue - the one thing this tool
exists not to do.
"""
import struct
import subprocess
import sys

import pytest

from conftest import FIXTURES, ROOT
import tap2pdf

sys.path.insert(0, str(ROOT / "tools"))
import make_fixtures as mf  # noqa: E402

TOOL = ROOT / "tap2pdf.py"
HELLO = bytes(range(0x20, 0x60))

# Trimmed from a real TAPClean 0.39-pre-7 report (tcreport.txt) for a
# commercial tape. Only TAPClean's own summary lines, no tape contents.
REAL_REPORT = """TAPClean version: 0.39-pre-7

GENERAL INFO AND TEST RESULTS

TAP Name    : hr.tap
TAP Size    : {size} bytes (770 kB)
TAP Version : 1
Recognized  : 100%
Loader ID   : {loader}

Overall Result    : PASS
"""


def run(*args):
    return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                          capture_output=True, text=True)


def dossier(path, *extra):
    args = tap2pdf.build_parser().parse_args([str(path), *map(str, extra)])
    return tap2pdf.analyse(path.read_bytes(), args)


def checks(d):
    return dict((c.name, c) for c in d.checks)


def write_tape(tmp_path, name, payload, **kw):
    p = tmp_path / name
    p.write_bytes(mf.make_tap(payload, **kw))
    return p


def report_for(tmp_path, tape, loader):
    r = tmp_path / "tcreport.txt"
    r.write_text(REAL_REPORT.format(size=tape.stat().st_size, loader=loader),
                 encoding="utf-8")
    return r


# ------------------------------------------------------------- TAPClean --
def test_the_real_tapclean_report_format_names_the_loader(tmp_path):
    tape = FIXTURES / "clean_single.tap"
    d = dossier(tape, "--tapclean", report_for(tmp_path, tape, "Visiload T2"))
    c = checks(d)["Loader identification"]
    assert c.result == tap2pdf.PASS
    assert "Visiload T2" in c.detail
    assert d.loaders == ["Visiload T2"]


def test_tapclean_console_output_is_accepted_too(tmp_path):
    tape = FIXTURES / "clean_single.tap"
    r = tmp_path / "console.txt"
    r.write_text("TAPClean 0.39-pre-7 - (C)2006-2023 TC Team\n"
                 "  Loader ID: Visiload T2.\n", encoding="utf-8")
    assert dossier(tape, "--tapclean", r).loaders == ["Visiload T2"]


def test_a_file_that_is_not_a_tapclean_report_is_refused(tmp_path):
    r = tmp_path / "junk.txt"
    r.write_text("garbage\x00 report\n", encoding="utf-8")
    out = tmp_path / "x.html"
    res = run(FIXTURES / "clean_single.tap", "--tapclean", r, "-o", out)
    assert res.returncode == tap2pdf.EXIT_ENRICH
    assert "TAPClean report" in res.stderr


def test_a_report_for_a_different_tape_is_refused(tmp_path):
    r = tmp_path / "tcreport.txt"
    r.write_text(REAL_REPORT.format(size=999999, loader="Novaload"),
                 encoding="utf-8")
    res = run(FIXTURES / "clean_single.tap", "--tapclean", r,
              "-o", tmp_path / "x.html")
    assert res.returncode == tap2pdf.EXIT_ENRICH
    assert "different tape" in res.stderr


def test_loader_n_a_on_a_tape_with_turbo_is_not_a_pass(tmp_path):
    tape = FIXTURES / "turbo_region.tap"
    d = dossier(tape, "--tapclean", report_for(tmp_path, tape, "n/a"))
    assert checks(d)["Loader identification"].result == tap2pdf.NOT_CHECKED
    assert d.loaders == []


def test_loader_n_a_on_a_rom_only_tape_agrees_with_the_regions(tmp_path):
    tape = FIXTURES / "clean_single.tap"
    d = dossier(tape, "--tapclean", report_for(tmp_path, tape, "n/a"))
    c = checks(d)["Loader identification"]
    assert c.result == tap2pdf.PASS
    assert "no turbo" in c.detail


def test_a_named_loader_reaches_the_turbo_row(tmp_path):
    tape = FIXTURES / "turbo_region.tap"
    d = dossier(tape, "--tapclean", report_for(tmp_path, tape, "Novaload"))
    c = checks(d)["Turbo region integrity"]
    assert c.result == tap2pdf.NOT_CHECKED
    assert "Novaload" in c.detail
    assert "unidentified" not in c.detail


# ------------------------------------------------------ output collisions --
def test_pdf_only_with_a_pdf_output_path_keeps_the_pdf(tmp_path):
    out = tmp_path / "out.pdf"
    res = run(FIXTURES / "clean_single.tap", "--pdf-only", "-o", out,
              "--browser", "definitely-not-a-browser")
    # No browser here, so exit 6 - but the refusal must be about the browser,
    # and nothing may be left pretending to be the PDF.
    assert res.returncode == tap2pdf.EXIT_ENRICH
    assert not out.exists() or out.read_bytes()[:4] == b"%PDF"


def test_two_outputs_may_not_share_a_path(tmp_path):
    out = tmp_path / "x.nfo"
    res = run(FIXTURES / "clean_single.tap", "--nfo", "-o", out)
    assert res.returncode == tap2pdf.EXIT_USAGE
    assert not out.exists()


def test_pdf_with_a_pdf_output_path_is_refused(tmp_path):
    out = tmp_path / "x.pdf"
    res = run(FIXTURES / "clean_single.tap", "--pdf", "-o", out)
    assert res.returncode == tap2pdf.EXIT_USAGE
    assert not out.exists()


def test_pdf_only_does_not_announce_the_html_it_deletes(tmp_path):
    out = tmp_path / "x.html"
    res = run(FIXTURES / "clean_single.tap", "--pdf-only", "-o", out,
              "--browser", "definitely-not-a-browser")
    assert "wrote %s" % out not in res.stderr


# ------------------------------------------------------------------ VICE --
def test_vice_is_refused_not_claimed(tmp_path):
    out = tmp_path / "x.html"
    res = run(FIXTURES / "clean_single.tap", "--vice", "x64sc", "-o", out)
    assert res.returncode == tap2pdf.EXIT_ENRICH
    assert out.is_file(), "the dossier is still written"
    assert "VICE screenshot: not used" in out.read_text(encoding="utf-8")


# ------------------------------------------------------- file assembly --
def test_a_seq_file_does_not_swallow_the_file_after_it():
    d = dossier(FIXTURES / "seq_then_prg.tap")
    names = [f.name for f in d.files]
    assert names == ["SEQFILE", "AFTER"]
    seq = d.files[0]
    assert seq.type_name == "SEQ header"
    assert seq.size == 2 * 191
    assert checks(d)["Block structure"].result == tap2pdf.PASS
    assert checks(d)["File length vs header range"].result == tap2pdf.PASS


def test_a_headerless_data_block_does_not_swallow_the_next_file():
    d = dossier(FIXTURES / "lost_header.tap")
    assert [f.name for f in d.files] == ["REAL"]
    assert d.files[0].load == 0xC000
    assert checks(d)["Block structure"].result == tap2pdf.NOT_CHECKED


def test_extract_takes_the_good_repeat_when_the_first_copy_is_bad(tmp_path):
    out = tmp_path / "ex"
    res = run(FIXTURES / "bad_first_copy.tap", "--extract", out,
              "-o", tmp_path / "x.html")
    assert res.returncode == 0
    prg = (out / "01_BADFIRST.prg").read_bytes()
    assert prg[2:] == HELLO
    assert "repeat" in res.stderr


def test_the_files_table_says_which_copy_it_used():
    d = dossier(FIXTURES / "bad_first_copy.tap")
    f = d.files[0]
    assert f.data == HELLO
    assert f.data_from_repeat
    assert "repeat" in tap2pdf.render_html(d)
    # The failure on the tape is still reported: the first copy IS bad.
    assert checks(d)["CBM block checksums"].result == tap2pdf.FAIL


def test_the_cruncher_is_not_credited_to_a_namesake(tmp_path):
    exo = (FIXTURES / "exomizer_sfx_sample.prg").read_bytes()
    load = exo[0] | (exo[1] << 8)
    tape = write_tape(tmp_path, "dup.tap",
                      mf.cbm_file("GAME", 0x0801, HELLO)
                      + mf.cbm_file("GAME", load, exo[2:]))
    d = dossier(tape)
    html = tap2pdf.render_html(d)
    assert html.count("Exomizer 3.x (sfx sys) at +") == 1


# ------------------------------------------------------------ timing --
def test_pal_override_does_not_claim_the_header_said_pal(tmp_path):
    tape = write_tape(tmp_path, "ntsc.tap",
                      mf.cbm_file("HELLO", 0x0801, HELLO), video=1)
    d = dossier(tape, "--pal")
    c = checks(d)["Header platform and timing"]
    assert "NTSC" in c.detail and "--pal" in c.detail
    assert "PAL, as stated by the header" not in c.detail
    assert "--pal" in tap2pdf.timing_label(d.header)


def test_pal_and_ntsc_together_is_a_usage_error(tmp_path):
    res = run(FIXTURES / "clean_single.tap", "--pal", "--ntsc",
              "-o", tmp_path / "x.html")
    assert res.returncode == tap2pdf.EXIT_USAGE


def test_a_c16_signature_with_a_c64_platform_byte_is_not_a_pass(tmp_path):
    data = bytearray(mf.make_tap(mf.cbm_file("HELLO", 0x0801, HELLO)))
    data[:12] = b"C16-TAPE-RAW"
    tape = tmp_path / "c16.tap"
    tape.write_bytes(bytes(data))
    c = checks(dossier(tape))["Header platform and timing"]
    assert c.result == tap2pdf.NOT_CHECKED


def test_tap_version_2_is_refused_as_unsupported(tmp_path):
    tape = write_tape(tmp_path, "v2.tap", bytes([0x30] * 100), version=2)
    res = run(tape, "-o", tmp_path / "x.html")
    assert res.returncode == tap2pdf.EXIT_MALFORMED
    assert "not supported" in res.stderr


# ------------------------------------------------------------ SYS entry --
def basic(line_bytes):
    nxt = 0x0801 + 4 + len(line_bytes) + 1
    return struct.pack("<HH", nxt, 10) + line_bytes + b"\x00\x00\x00"


@pytest.mark.parametrize("line, want", [
    (b"\x9e2064", 2064),
    (b"\x9e 2064", 2064),
    (b"\x9e(2061)", 2061),
    (b"\x9e2064:\x80", 2064),
    (b"\x9e0", 0),
    (b"\x9e12\xac4096", None),           # SYS 12*4096: an expression
    (b"\x9e\xc2(43)", None),             # SYS PEEK(43): not a constant
    (b"\x8f SYS 1234", None),            # REM SYS 1234: a comment
    (b"\x99\"\x9e1234\"", None),         # PRINT "<SYS>1234": a string
])
def test_the_entry_point_is_only_a_plain_constant(line, want):
    assert tap2pdf.find_sys(tap2pdf.detokenize(basic(line))) == want


def test_tokens_are_not_expanded_inside_strings_or_rem():
    lines = tap2pdf.detokenize(basic(b"\x99\"\x9e\"\x3a\x8f \x9e"))
    assert lines == [(10, 'PRINT"{9E}":REM {9E}')]


def test_sys_0_is_reported_not_dropped(tmp_path):
    tape = write_tape(tmp_path, "sys0.tap",
                      mf.cbm_file("ZERO", 0x0801, basic(b"\x9e0"), ftype=1))
    d = dossier(tape)
    assert d.sys_entry == 0
    assert "$0000 (SYS 0)" in tap2pdf.render_html(d)


# ------------------------------------------------------ header checks --
def test_a_short_file_is_not_called_cosmetic(tmp_path):
    t = bytearray(mf.make_tap(mf.cbm_file("HELLO", 0x0801, HELLO)))
    t[16:20] = struct.pack("<I", len(t) - 20 + 50000)
    tape = tmp_path / "short.tap"
    tape.write_bytes(bytes(t))
    c = checks(dossier(tape))["Header length vs actual data"]
    assert c.result == tap2pdf.FAIL
    assert "Cosmetic" not in c.detail
    assert "missing" in c.detail


def test_an_end_address_of_0000_means_the_top_of_memory(tmp_path):
    tape = write_tape(tmp_path, "wrap.tap",
                      mf.encode_block_pair(
                          mf.cbm_header_block("TOP", 0xFFC0, 0x0000))
                      + mf.encode_block_pair(bytes(64)))
    d = dossier(tape)
    assert checks(d)["File length vs header range"].result == tap2pdf.PASS


@pytest.mark.parametrize("value", ["0", "-1", "inf", "nan", "abc"])
def test_a_nonsense_size_limit_is_a_usage_error(value, tmp_path):
    res = run(FIXTURES / "clean_single.tap", "--max-size", value,
              "-o", tmp_path / "x.html")
    assert res.returncode == tap2pdf.EXIT_USAGE
    assert "Traceback" not in res.stderr
