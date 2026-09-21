from conftest import FIXTURES
import tap2pdf


def dossier(name):
    path = FIXTURES / name
    args = tap2pdf.build_parser().parse_args([str(path)])
    return tap2pdf.analyse(path.read_bytes(), args)


def test_the_nfo_is_seven_bit_ascii_within_78_columns():
    text = tap2pdf.render_nfo(dossier("clean_single.tap"))
    text.encode("ascii")                      # raises if anything is not ASCII
    assert max(len(line) for line in text.splitlines()) <= 78


def test_the_nfo_leads_with_the_verification_report():
    text = tap2pdf.render_nfo(dossier("clean_single.tap"))
    assert text.index("VERIFICATION") < text.index("FILES")


def test_not_checked_survives_into_the_nfo():
    text = tap2pdf.render_nfo(dossier("turbo_region.tap"))
    assert "NOT CHECKED" in text
    assert "never as a pass" in text


def test_a_failing_check_survives_into_the_nfo():
    text = tap2pdf.render_nfo(dossier("bad_checksum.tap"))
    assert "FAIL" in text


def test_the_nfo_is_reproducible():
    a = tap2pdf.render_nfo(dossier("clean_single.tap"))
    b = tap2pdf.render_nfo(dossier("clean_single.tap"))
    assert a == b


def test_extract_writes_each_prg_with_its_load_address(tmp_path):
    d = dossier("two_files.tap")
    written = tap2pdf.extract_files(d, str(tmp_path))
    assert len(written) == 2
    first = sorted(tmp_path.iterdir())[0].read_bytes()
    assert first[:2] == bytes([0x01, 0x08])   # $0801, little-endian
    assert len(first) == 2 + d.files[0].size
