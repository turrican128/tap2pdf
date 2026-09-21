from conftest import FIXTURES
import tap2pdf

EXPECTED = ["clean_single.tap", "two_files.tap", "bad_checksum.tap",
            "copies_disagree.tap", "v0_overflow.tap", "turbo_region.tap",
            "truncated.tap", "basic_sys.tap"]


def test_every_fixture_exists():
    missing = [n for n in EXPECTED if not (FIXTURES / n).is_file()]
    assert missing == [], "run tools/make_fixtures.py"


def test_fixtures_are_real_taps_that_parse():
    for name in EXPECTED:
        if name == "truncated.tap":
            continue                      # that one is malformed on purpose
        data = (FIXTURES / name).read_bytes()
        h = tap2pdf.parse_header(data)
        assert h.signature == b"C64-TAPE-RAW"
        assert tap2pdf.decode_pulses(data, h)


def test_the_declared_length_is_honest_in_every_fixture():
    for name in EXPECTED:
        data = (FIXTURES / name).read_bytes()
        h = tap2pdf.parse_header(data)
        assert h.length_mismatch == 0, name
