from conftest import FIXTURES
import tap2pdf


def analyse(name, tapclean_used=False):
    data = (FIXTURES / name).read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    regions = tap2pdf.segment(pulses)
    files = tap2pdf.build_files(tap2pdf.pair_blocks(
        tap2pdf.decode_cbm_blocks(pulses)))
    return h, pulses, regions, files, tap2pdf.build_checks(
        h, pulses, regions, files, tapclean_used)


def named(checks):
    return dict((c.name, c) for c in checks)


def test_every_check_explains_itself():
    # A blank detail is a check pretending to have been run.
    *_, checks = analyse("clean_single.tap")
    assert checks
    for c in checks:
        assert c.detail.strip(), c.name
        assert c.result in (tap2pdf.PASS, tap2pdf.FAIL, tap2pdf.NOT_CHECKED)


def test_a_clean_tape_passes_its_checksums():
    *_, checks = analyse("clean_single.tap")
    assert named(checks)["CBM block checksums"].result == tap2pdf.PASS


def test_a_damaged_tape_fails_loudly():
    *_, checks = analyse("bad_checksum.tap")
    c = named(checks)["CBM block checksums"]
    assert c.result == tap2pdf.FAIL
    assert "expected" in c.detail and "computed" in c.detail


def test_disagreeing_copies_are_reported():
    *_, checks = analyse("copies_disagree.tap")
    assert named(checks)["First copy vs repeat"].result == tap2pdf.FAIL


def test_loader_identification_is_not_checked_without_tapclean_and_says_so():
    *_, checks = analyse("turbo_region.tap", tapclean_used=False)
    c = named(checks)["Loader identification"]
    assert c.result == tap2pdf.NOT_CHECKED
    assert "--tapclean" in c.detail          # the remedy must be offered


def test_a_turbo_region_is_declared_unchecked_rather_than_passed():
    *_, checks = analyse("turbo_region.tap")
    c = named(checks)["Turbo region integrity"]
    assert c.result == tap2pdf.NOT_CHECKED
    assert c.detail.strip()


def test_the_verdict_does_not_outrun_the_evidence():
    _h, _pulses, regions, _files, checks = analyse("turbo_region.tap")
    text = tap2pdf.verdict(checks, regions)
    assert "CBM" in text
    assert "not been checked" in text


def test_a_clean_tape_still_refuses_a_blanket_clean_bill_of_health():
    # Loader identification is NOT CHECKED without tapclean even on a
    # perfect tape, so the verdict must say the check list is incomplete.
    _h, _pulses, regions, _files, checks = analyse("clean_single.tap")
    text = tap2pdf.verdict(checks, regions)
    assert "reads cleanly" in text
    assert "not a clean bill of health" in text
