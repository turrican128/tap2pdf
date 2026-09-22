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
    _h, _pulses, regions, files, checks = analyse("clean_single.tap")
    text = tap2pdf.verdict(checks, regions, files)
    assert "reads cleanly" in text
    assert "not a clean bill of health" in text


def test_cbm_shaped_pulses_with_nothing_decoded_do_not_claim_a_clean_read():
    # Found by the real-tape sweep: 4 of 49 commercial tapes have regions
    # that LOOK like the CBM ROM loader but yield no complete block. The
    # verdict said "the CBM portion of this tape reads cleanly" while the
    # checksum row in the same table said NOT CHECKED. Pulses that look
    # like a loader are not data that was read.
    regions = [tap2pdf.Region("cbm", 0, 256, 256, 100000)]
    checks = [tap2pdf.Check("CBM block checksums", tap2pdf.NOT_CHECKED,
                            "no CBM ROM-loader blocks were found on this tape")]
    text = tap2pdf.verdict(checks, regions, files=[])
    assert "reads cleanly" not in text
    assert "no complete block could be decoded" in text


def test_cbm_regions_with_files_still_read_cleanly():
    regions = [tap2pdf.Region("cbm", 0, 256, 256, 100000)]
    checks = [tap2pdf.Check("CBM block checksums", tap2pdf.PASS, "all pass")]
    text = tap2pdf.verdict(checks, regions, files=[object()])
    assert "reads cleanly" in text


def test_a_missing_repeat_is_never_reported_as_the_copies_agreeing():
    # A tape carrying only the first copy of each block has nothing to
    # compare against. Reporting PASS "all files agree" for a comparison
    # that never happened is the tool claiming more than it established.
    *_, checks = analyse("no_repeats.tap")
    c = named(checks)["First copy vs repeat"]
    assert c.result == tap2pdf.NOT_CHECKED, c.detail
    assert "repeat" in c.detail.lower()


def test_a_file_with_no_repeat_does_not_claim_its_copies_agree():
    data = (FIXTURES / "no_repeats.tap").read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    f = tap2pdf.build_files(tap2pdf.pair_blocks(
        tap2pdf.decode_cbm_blocks(pulses)))[0]
    assert f.missing_repeats > 0
    assert not f.copies_agree


def test_decoded_blocks_that_form_no_file_are_still_verified():
    # Found on real tapes (Cobra, WWF): blocks decode fine but never pair
    # into a file, so their checksum failures vanished and the dossier
    # claimed no blocks were found at all. Both statements were false.
    data = (FIXTURES / "orphan_blocks.tap").read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    blocks = tap2pdf.decode_cbm_blocks(pulses)
    files = tap2pdf.build_files(tap2pdf.pair_blocks(blocks))
    assert blocks and not files, "fixture must decode blocks but build no file"

    checks = tap2pdf.build_checks(h, pulses, tap2pdf.segment(pulses), files,
                                  False, blocks=blocks)
    c = named(checks)["CBM block checksums"]
    assert c.result == tap2pdf.FAIL, c.detail
    assert "no CBM ROM-loader blocks were found" not in c.detail


def test_blocks_outside_a_file_are_reported_as_such():
    data = (FIXTURES / "orphan_blocks.tap").read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    blocks = tap2pdf.decode_cbm_blocks(pulses)
    checks = tap2pdf.build_checks(h, pulses, tap2pdf.segment(pulses), [],
                                  False, blocks=blocks)
    c = named(checks)["Block structure"]
    assert c.result in (tap2pdf.FAIL, tap2pdf.NOT_CHECKED)
    assert "2" in c.detail
