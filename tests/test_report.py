import subprocess
import sys

from conftest import FIXTURES, GOLDEN, ROOT
import tap2pdf


def dossier(name):
    path = FIXTURES / name
    args = tap2pdf.build_parser().parse_args([str(path)])
    return tap2pdf.analyse(path.read_bytes(), args)


def test_the_verification_report_comes_before_the_tape_map():
    html = tap2pdf.render_html(dossier("clean_single.tap"))
    assert html.index("Verification report") < html.index("Tape map")


def test_the_html_is_self_contained():
    html = tap2pdf.render_html(dossier("clean_single.tap"))
    for forbidden in ("<script", 'src="http', 'href="http', "<link "):
        assert forbidden not in html


def test_no_timestamp_or_path_leaks_into_the_output():
    html = tap2pdf.render_html(dossier("clean_single.tap"))
    assert str(ROOT) not in html
    assert "clean_single.tap" in html          # the basename is fine
    for year in ("2025-", "2026-", "2027-"):
        assert year not in html


def test_rendering_is_byte_for_byte_reproducible():
    a = tap2pdf.render_html(dossier("clean_single.tap"))
    b = tap2pdf.render_html(dossier("clean_single.tap"))
    assert a == b


def test_the_golden_html_has_not_drifted():
    html = tap2pdf.render_html(dossier("clean_single.tap"))
    expected = (GOLDEN / "clean_single.html").read_text(encoding="utf-8")
    assert html == expected, \
        "output changed - review it, then regenerate the golden"


def test_a_tape_with_no_cbm_files_still_renders():
    html = tap2pdf.render_html(dossier("turbo_region.tap"))
    assert "Verification report" in html
    assert "NOT CHECKED" in html


def test_end_to_end_writes_a_dossier(tmp_path):
    out = tmp_path / "out.html"
    r = subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"),
                        str(FIXTURES / "clean_single.tap"), "-o", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert out.is_file() and out.stat().st_size > 2000
