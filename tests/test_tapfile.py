import struct

import pytest

from conftest import ROOT  # noqa: F401 - puts the repo root on sys.path
import tap2pdf


def header(sig=b"C64-TAPE-RAW", version=1, platform=0, video=0, declared=4,
           body=b"\x30\x30\x30\x30"):
    return (sig + bytes([version, platform, video, 0])
            + struct.pack("<I", declared) + body)


def test_parses_a_well_formed_v1_header():
    h = tap2pdf.parse_header(header())
    assert h.version == 1
    assert h.platform == "C64"
    assert h.video == "PAL"
    assert h.declared_length == 4
    assert h.actual_length == 4
    assert h.length_mismatch == 0


def test_ntsc_and_c16_are_recognised():
    h = tap2pdf.parse_header(header(sig=b"C16-TAPE-RAW", platform=2, video=1))
    assert h.platform == "C16"
    assert h.video == "NTSC"


def test_a_wrong_signature_is_not_a_tap():
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.parse_header(b"NOT-A-TAPE!!" + bytes(8))
    assert e.value.code == tap2pdf.EXIT_NOT_TAP


def test_a_file_too_short_to_hold_a_header_is_refused():
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.parse_header(b"C64-TAPE-RAW")
    assert e.value.code == tap2pdf.EXIT_NOT_TAP


def test_an_unknown_version_is_refused_as_malformed():
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.parse_header(header(version=7))
    assert e.value.code == tap2pdf.EXIT_MALFORMED


def test_a_lying_length_field_is_recorded_not_fatal():
    # Declares 999 bytes, carries 4. Cosmetic; tapclean -rs repairs it.
    h = tap2pdf.parse_header(header(declared=999))
    assert h.length_mismatch == 4 - 999


def test_v1_nonzero_bytes_are_eight_cycles_each():
    data = header(version=1, declared=3, body=b"\x30\x42\x56")
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    assert [p.cycles for p in pulses] == [0x30 * 8, 0x42 * 8, 0x56 * 8]
    assert not any(p.overflow for p in pulses)


def test_v1_zero_byte_takes_three_raw_cycle_bytes():
    # $00 then 0x00E100 little-endian = 57600 cycles, NOT multiplied by 8.
    body = b"\x30\x00\x00\xE1\x00\x30"
    data = header(version=1, declared=len(body), body=body)
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    assert [p.cycles for p in pulses] == [0x30 * 8, 57600, 0x30 * 8]
    assert [p.overflow for p in pulses] == [False, False, False]


def test_v0_zero_byte_is_an_unmeasured_overflow():
    body = b"\x30\x00\x30"
    data = header(version=0, declared=len(body), body=body)
    h = tap2pdf.parse_header(data)
    pulses = tap2pdf.decode_pulses(data, h)
    assert [p.overflow for p in pulses] == [False, True, False]
    assert pulses[1].cycles == tap2pdf.OVERFLOW_CYCLES  # a floor, not a reading


def test_a_v1_stream_truncated_mid_pulse_is_refused():
    body = b"\x30\x00\x01"  # $00 needs three more bytes; only two follow
    data = header(version=1, declared=len(body), body=body)
    h = tap2pdf.parse_header(data)
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.decode_pulses(data, h)
    assert e.value.code == tap2pdf.EXIT_MALFORMED


def test_an_empty_stream_is_refused():
    data = header(declared=0, body=b"")
    h = tap2pdf.parse_header(data)
    with pytest.raises(tap2pdf.Refusal) as e:
        tap2pdf.decode_pulses(data, h)
    assert e.value.code == tap2pdf.EXIT_MALFORMED
