import struct

from conftest import FIXTURES
import tap2pdf


def program(target=2064, line_no=10):
    line = b"\x9e" + str(target).encode() + b"\x00"
    nxt = 0x0801 + 4 + len(line)
    return struct.pack("<HH", nxt, line_no) + line + b"\x00\x00"


def test_sys_is_token_9e():
    # If this table shifts by one, every SYS address the tool reports is
    # wrong, and it would report them just as confidently.
    assert tap2pdf.BASIC_TOKENS[0x9E] == "SYS"
    assert tap2pdf.BASIC_TOKENS[0x80] == "END"
    assert tap2pdf.BASIC_TOKENS[0xCB] == "GO"


def test_a_sys_line_detokenizes_and_gives_up_its_address():
    lines = tap2pdf.detokenize(program(2064))
    assert lines == [(10, "SYS2064")]
    assert tap2pdf.find_sys(lines) == 2064


def test_a_program_with_no_sys_returns_none_not_a_guess():
    data = struct.pack("<HH", 0x0808, 10) + b"\x99\x00" + b"\x00\x00"
    lines = tap2pdf.detokenize(data)
    assert lines == [(10, "PRINT")]
    assert tap2pdf.find_sys(lines) is None


def test_the_sys_entry_is_found_in_the_real_fixture_tape():
    data = (FIXTURES / "basic_sys.tap").read_bytes()
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    f = tap2pdf.build_files(tap2pdf.pair_blocks(
        tap2pdf.decode_cbm_blocks(pulses)))[0]
    assert tap2pdf.find_sys(tap2pdf.detokenize(f.data)) == 2064


def test_garbage_does_not_raise():
    assert tap2pdf.detokenize(b"\xff\xff\xff") == []
