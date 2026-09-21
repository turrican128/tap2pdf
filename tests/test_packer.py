import tap2pdf


def test_an_unrecognised_file_reports_nothing_rather_than_unpacked():
    assert tap2pdf.identify_packer(bytes(range(64)), 0x0801) is None


def test_every_shipped_signature_records_where_it_came_from():
    # A signature written from memory is a guess, and a guessed packer name
    # turns the dossier into exactly the confident liar the honesty rule
    # exists to prevent. Each entry must name the sample it was observed in,
    # so it can be re-derived and checked.
    for sig in tap2pdf.PACKER_SIGNATURES:
        assert sig["source"], sig["name"]
        assert len(sig["pattern"]) >= 8, sig["name"]


def test_a_signature_matches_only_inside_its_declared_window():
    if not tap2pdf.PACKER_SIGNATURES:
        return
    sig = tap2pdf.PACKER_SIGNATURES[0]
    padding = b"\x00" * (sig["max_offset"] + 64)
    assert tap2pdf.identify_packer(padding + sig["pattern"], 0x0801) is None
