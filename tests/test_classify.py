from conftest import FIXTURES
import tap2pdf


def load(name):
    data = (FIXTURES / name).read_bytes()
    h = tap2pdf.parse_header(data)
    return h, tap2pdf.decode_pulses(data, h)


def test_a_cbm_tape_shows_the_three_rom_clusters():
    _h, pulses = load("clean_single.tap")
    clusters = tap2pdf.find_clusters(tap2pdf.histogram(pulses))
    centers = [c.center for c in clusters]
    for expected in (tap2pdf.CBM_SHORT, tap2pdf.CBM_MEDIUM, tap2pdf.CBM_LONG):
        assert any(abs(c - expected) <= tap2pdf.CBM_TOLERANCE
                   for c in centers), centers
    assert tap2pdf.looks_like_cbm(clusters)


def test_a_cbm_tape_segments_into_leader_and_cbm_regions():
    _h, pulses = load("clean_single.tap")
    kinds = [r.kind for r in tap2pdf.segment(pulses)]
    assert "leader" in kinds
    assert "cbm" in kinds


def test_a_turbo_region_is_seen_but_never_named():
    _h, pulses = load("turbo_region.tap")
    regions = tap2pdf.segment(pulses)
    turbo = [r for r in regions if r.kind == "turbo"]
    assert turbo, [r.kind for r in regions]
    assert not tap2pdf.looks_like_cbm(turbo[0].clusters)


def test_regions_cover_every_pulse_exactly_once():
    _h, pulses = load("two_files.tap")
    regions = tap2pdf.segment(pulses)
    assert regions[0].start_index == 0
    assert regions[-1].end_index == len(pulses)
    for a, b in zip(regions, regions[1:]):
        assert a.end_index == b.start_index


def test_a_plain_cbm_tape_reports_no_turbo_region_at_all():
    # The long pulse occurs once per byte, so in a short window it can fall
    # below the noise floor and leave two clusters. Judging a window by "all
    # three widths present" therefore shredded a plain ROM tape into
    # alternating cbm/turbo bands - inventing turbo regions that do not
    # exist. The dossier must never claim a loader it has not established.
    for name in ("clean_single.tap", "two_files.tap", "basic_sys.tap"):
        _h, pulses = load(name)
        kinds = [r.kind for r in tap2pdf.segment(pulses)]
        assert "turbo" not in kinds, (name, kinds)


def test_a_clean_tape_segments_into_only_a_handful_of_regions():
    # A barcode of dozens of alternating bands is a broken tape map even
    # when every band is individually defensible.
    _h, pulses = load("clean_single.tap")
    regions = tap2pdf.segment(pulses)
    assert len(regions) <= 8, [r.kind for r in regions]
