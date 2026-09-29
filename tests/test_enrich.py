import subprocess
import sys

import pytest

from conftest import FIXTURES, ROOT
import tap2pdf


def test_a_tapclean_report_yields_loader_names():
    # The field TAPClean really writes. 1.0.3 was tested against an invented
    # "Loader detected:" line and never matched a real report.
    text = ("TAPClean version: 0.39-pre-7\n"
            "TAP Size    : 788700 bytes (770 kB)\n"
            "Something else entirely\n"
            "Loader ID   : Visiload T2\n")
    got = tap2pdf.parse_tapclean_report(text)
    assert got["loaders"] == ["Visiload T2"]
    assert got["tap_size"] == 788700


def test_an_unexpected_report_format_does_not_raise():
    got = tap2pdf.parse_tapclean_report("\x00 not a report at all \xff")
    assert got["loaders"] == []


def test_a_supplied_report_turns_loader_identification_into_a_pass(tmp_path):
    report = tmp_path / "report.txt"
    path = FIXTURES / "clean_single.tap"
    report.write_text("TAPClean version: 0.39-pre-7\n"
                      "TAP Size    : %d bytes\n"
                      "Loader ID   : Novaload\n" % path.stat().st_size,
                      encoding="utf-8")
    args = tap2pdf.build_parser().parse_args(
        [str(path), "--tapclean", str(report)])
    d = tap2pdf.analyse(path.read_bytes(), args)
    check = dict((c.name, c) for c in d.checks)["Loader identification"]
    assert check.result == tap2pdf.PASS
    assert "Novaload" in check.detail


def test_no_browser_is_a_clear_refusal_not_a_corrupt_pdf(tmp_path):
    src = tmp_path / "x.html"
    src.write_text("<html></html>", encoding="utf-8")
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.render_pdf(str(src), str(tmp_path / "x.pdf"),
                           browser="definitely-not-a-browser")
    assert e.value.code == tap2pdf.EXIT_ENRICH
    assert not (tmp_path / "x.pdf").exists()


def test_the_html_survives_a_failed_pdf(tmp_path):
    out = tmp_path / "out.html"
    r = subprocess.run([sys.executable, str(ROOT / "tap2pdf.py"),
                        str(FIXTURES / "clean_single.tap"), "-o", str(out),
                        "--pdf", "--browser", "definitely-not-a-browser"],
                       capture_output=True, text=True)
    assert r.returncode == tap2pdf.EXIT_ENRICH
    assert out.is_file(), "the dossier must survive a failed PDF render"
    assert "HTML dossier was still written" in r.stderr


def test_a_missing_screenshot_is_reported_rather_than_faked():
    path = FIXTURES / "clean_single.tap"
    args = tap2pdf.build_parser().parse_args([str(path)])
    d = tap2pdf.analyse(path.read_bytes(), args)
    assert d.screenshot_data_uri is None
    assert d.enrichments["VICE screenshot"] is False
    assert "VICE screenshot: not used" in tap2pdf.render_html(d)
