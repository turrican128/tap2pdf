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


def test_a_trailing_byte_is_a_difference_not_a_match():
    # Reported by an external reviewer. _compare_copies padded the shorter
    # payload with zeros, so 64 bytes and the same 64 bytes plus a trailing
    # zero compared as identical and the tool said the copies agreed.
    def blk(payload):
        return tap2pdf.CbmBlock(0x89, payload, 0, 0, 0, 0, 0)

    same = bytes(range(64))
    diff, missing = tap2pdf._compare_copies(blk(same), blk(same + b"\x00"))
    assert missing == 0
    assert diff == 1, "a payload one byte longer must not compare equal"


def test_identical_payloads_still_agree():
    def blk(payload):
        return tap2pdf.CbmBlock(0x89, payload, 0, 0, 0, 0, 0)

    same = bytes(range(64))
    assert tap2pdf._compare_copies(blk(same), blk(same)) == (0, 0)


def test_a_longer_tail_counts_every_extra_byte():
    def blk(payload):
        return tap2pdf.CbmBlock(0x89, payload, 0, 0, 0, 0, 0)

    a = bytes(range(64))
    diff, _ = tap2pdf._compare_copies(blk(a), blk(a + b"\x00\x00\x00"))
    assert diff == 3, diff
