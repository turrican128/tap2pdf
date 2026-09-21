from conftest import FIXTURES
import tap2pdf


def files_of(name):
    data = (FIXTURES / name).read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    blocks = tap2pdf.decode_cbm_blocks(pulses)
    return tap2pdf.build_files(tap2pdf.pair_blocks(blocks))


def test_a_clean_tape_yields_one_file_with_its_real_name_and_address():
    files = files_of("clean_single.tap")
    assert len(files) == 1
    f = files[0]
    assert f.name == "HELLO"
    assert f.load == 0x0801
    assert f.size == 64
    assert f.data == bytes(range(0x20, 0x60))
    assert f.header_block.checksum_ok
    assert f.data_block.checksum_ok
    assert f.copies_agree


def test_two_files_are_both_recovered_with_their_own_addresses():
    files = files_of("two_files.tap")
    assert [f.name for f in files] == ["PART ONE", "PART TWO"]
    assert [f.load for f in files] == [0x0801, 0xC000]


def test_a_bad_checksum_is_reported_not_hidden():
    f = files_of("bad_checksum.tap")[0]
    assert not f.data_block.checksum_ok
    assert f.data_block.checksum_stored != f.data_block.checksum_computed
    assert f.data, "the damaged block's contents must still be shown"


def test_copies_that_disagree_are_counted():
    f = files_of("copies_disagree.tap")[0]
    assert not f.copies_agree
    assert f.disagreement_count == 1


def test_the_file_type_is_named_not_left_as_a_number():
    assert files_of("basic_sys.tap")[0].type_name == "relocatable PRG"


def test_no_parity_errors_on_a_clean_tape():
    f = files_of("clean_single.tap")[0]
    assert f.header_block.parity_errors == 0
    assert f.data_block.parity_errors == 0
