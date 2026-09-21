from conftest import FIXTURES
import tap2pdf


def analyse(name):
    data = (FIXTURES / name).read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    regions = tap2pdf.segment(pulses)
    files = tap2pdf.build_files(tap2pdf.pair_blocks(
        tap2pdf.decode_cbm_blocks(pulses)))
    return h, regions, files


def test_the_tape_map_is_self_contained_svg():
    h, regions, _files = analyse("clean_single.tap")
    svg = tap2pdf.tape_map_svg(regions, h)
    assert svg.startswith("<svg")
    assert svg.rstrip().endswith("</svg>")
    for forbidden in ("<script", "http://", "https://", "<image"):
        assert forbidden not in svg.replace(
            'xmlns="http://www.w3.org/2000/svg"', "")


def test_the_tape_map_draws_one_band_per_region():
    h, regions, _files = analyse("two_files.tap")
    svg = tap2pdf.tape_map_svg(regions, h)
    assert svg.count("<rect") >= len(regions)


def test_no_band_collapses_to_nothing():
    h, regions, _files = analyse("clean_single.tap")
    svg = tap2pdf.tape_map_svg(regions, h)
    assert 'width="0"' not in svg
    assert 'width="0.00"' not in svg


def test_the_memory_map_labels_each_file_at_its_load_address():
    _h, _regions, files = analyse("two_files.tap")
    svg = tap2pdf.memory_map_svg(files)
    assert "$0801" in svg
    assert "$C000" in svg
    assert "PART ONE" in svg


def test_a_filename_with_markup_characters_is_escaped():
    assert "&lt;" in tap2pdf.svg_escape("<x>")
    assert "&amp;" in tap2pdf.svg_escape("a & b")


def test_the_maps_are_reproducible():
    h, regions, files = analyse("two_files.tap")
    assert tap2pdf.tape_map_svg(regions, h) == tap2pdf.tape_map_svg(regions, h)
    assert tap2pdf.memory_map_svg(files) == tap2pdf.memory_map_svg(files)
