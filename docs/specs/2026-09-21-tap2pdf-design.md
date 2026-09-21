# tap2pdf — design

Status: approved design, 2026-09-21. Target: v1.0, released on GitHub the way
txt2dirart 1.0.2 was.

## 1. What it is

One command. A `.tap` goes in, a self-contained HTML dossier comes out, and with
`--pdf` a PDF of that same document.

```
$ tap2pdf human-race.tap --pdf
human-race-dossier.html   (312 KB, self-contained)
human-race-dossier.pdf    (4 pages)
```

The dossier answers one question: **what is on this tape and how does it boot.**
That is the recon a cracker does first and the record an archivist wants second.

## 2. Who it is for

A cracker facing an unfamiliar tape, and an archivist cataloguing one. Both want
the same facts; they read them for different reasons.

## 3. Non-goals

- **It does not crack anything.** It changes no bytes and writes no tape. The
  input file is opened read-only and never written.
- **It does not replace TAPClean.** TAPClean tests, cleans and repairs tapes and
  knows 130+ named loaders. tap2pdf explains a tape. It can *read* a TAPClean
  report but does not reimplement it.
- **It does not guess.** See §10.
- **No commentary layer in v1.** The hand-written "what this loader does and
  where its check sits" knowledge base is v1.1, as a data file. Everything in
  v1.0 is derived from the bytes.

## 4. The promise

Ported directly from txt2dirart: *no dependencies, no install, no GUI.* The
downloaded binary runs on a machine with nothing else on it.

That is kept by splitting the tool in two:

**Core — always available, Python standard library only.**
TAP parsing, pulse classification, the tape map, CBM block decoding, checksum
verification, turbo detection, BASIC detokenization, the memory map, packer
signature matching, and the whole HTML document.

**Enrichment — optional, absent by default, degrades visibly.**

| Flag | Needs | Adds | Without it |
|---|---|---|---|
| `--tapclean <report>` | a TAPClean report file | named loader identification | blocks are described by pulse signature, not name |
| `--vice <x64sc path>` | VICE | title-screen shot on the cover | no cover image |
| `--pdf` | headless Edge or Chrome | the PDF render | HTML only |

Every absence is **stated in the document**, never hidden. A dossier built
without TAPClean says so, in the dossier.

## 5. Command line

```
tap2pdf <tape.tap> [options]

  -o, --output <path>     output HTML path (default: <tape>-dossier.html)
      --pdf               also render a PDF next to the HTML
      --pdf-only          render the PDF and delete the intermediate HTML
      --nfo               also write <tape>-dossier.nfo, plain ASCII
      --tapclean <file>   ingest a TAPClean report for named loader IDs
      --vice <path>       path to x64sc for the title screenshot
      --browser <path>    headless Edge/Chrome to use for --pdf
      --extract <dir>     write the PRGs recovered from CBM blocks to <dir>
      --pal / --ntsc      clock for time calculations (default: PAL)
      --title <text>      override the document title
      --quiet             suppress the progress lines on stderr
      --version
```

The plain form — `tap2pdf game.tap` — must do the useful thing with no flags.

## 6. Exit codes

| Code | Meaning |
|---|---|
| 0 | dossier written |
| 1 | usage error |
| 2 | input unreadable |
| 3 | not a TAP file (signature does not match) |
| 4 | malformed TAP — refused (see §11) |
| 5 | output could not be written |
| 6 | an enrichment was explicitly requested and is unavailable |

Code 6 matters: if the user asks for `--pdf` and no browser is found, the tool
has not done what was asked. It writes the HTML, says clearly that the PDF was
not produced and why, and exits 6. It never exits 0 on a half-done job.

## 7. Architecture

Single file `tap2pdf.py`, but internally these units, each testable alone:

| Unit | Does | Depends on |
|---|---|---|
| `tapfile` | parse header + pulse stream into a list of pulses in clock cycles | — |
| `classify` | pulse histogram, cluster detection, region segmentation | `tapfile` |
| `cbm` | decode CBM ROM blocks: sync, bytes, parity, checksum, headers | `classify` |
| `basic` | detokenize a BASIC stub, find the `SYS` entry | — |
| `packer` | match decruncher signatures against an extracted PRG | — |
| `tapemap` | the SVG tape map | `classify` |
| `memmap` | the SVG memory map | `cbm` |
| `enrich` | TAPClean report ingest, VICE screenshot, PDF render | subprocess |
| `report` | assemble the HTML; the NFO is a second renderer of the same model | all |

`report` consumes one plain data structure — the *dossier model* — built by
everything above it. Nothing in `report` reads a TAP. That boundary is what
makes golden tests and the `.nfo` renderer cheap.

## 8. TAP parsing

Header, 20 bytes:

| Offset | Meaning |
|---|---|
| `$00-$0B` | signature `C64-TAPE-RAW` (also accept `C16-TAPE-RAW`) |
| `$0C` | version: 0 or 1 |
| `$0D` | platform: 0 = C64, 1 = VIC-20, 2 = C16 |
| `$0E` | video standard: 0 = PAL, 1 = NTSC |
| `$0F` | reserved |
| `$10-$13` | data length, 32-bit little-endian |

Pulse stream:

- A non-zero byte `B` is a pulse of `B * 8` clock cycles.
- **Version 0:** a `$00` byte is an overflow — a pulse longer than `255 * 8`
  cycles, length unrecorded. It is carried through the model as *unknown
  length*, never silently treated as zero.
- **Version 1:** a `$00` byte is followed by three bytes, little-endian, giving
  the pulse length in clock cycles directly (not multiplied by 8).

Clock rates used for the time axis: PAL C64 985248 Hz, NTSC C64 1022727 Hz,
PAL C16 886724 Hz, NTSC C16 894886 Hz, PAL VIC-20 1108405 Hz. The platform byte
selects the default; `--pal`/`--ntsc` overrides it.

## 9. Classification and the tape map

A histogram of pulse widths is built, then peaks are found. Pulse populations
name themselves:

- Three dominant clusters near `$30` / `$42` / `$56` (short/medium/long, in TAP
  byte units) is the **CBM ROM loader**.
- A long unbroken run of a single short pulse is a **pilot/leader tone**.
- A region with two dominant clusters, tighter and faster than the ROM's, is a
  **turbo loader** — family unknown without TAPClean.
- Very long pulses are **gaps / silence**.
- Anything else is **unclassified**, and is labelled exactly that.

The tape map is an inline SVG: one horizontal bar across the whole tape, banded
by region type, with a time axis in seconds derived from cumulative cycles. This
is the picture on page one. It is the thing that makes the document worth
looking at.

## 10. The honesty rule

Borrowed from the txt2dirart refusal ethos, and it governs the whole document.

**The dossier never names something it has not established.** Without a TAPClean
report, an unrecognised turbo region is written as:

> Unidentified turbo loader — 2 pulse clusters at $2F and $42, 41 blocks,
> 18.4 s. No loader name available: run with `--tapclean` to identify it.

It is not written as "probably Novaload". A dossier that confidently names the
wrong loader sends a cracker down a blind alley and is worse than one that says
it does not know.

## 11. Damage and refusal

- **Length field disagrees with the actual file size.** Common and harmless — it
  is what TAPClean's `-rs` repairs. tap2pdf warns on stderr, prints the
  discrepancy prominently in the dossier, and continues using the real byte
  count. It does not repair the file.
- **Truncated mid-pulse** (a v1 `$00` with fewer than 3 bytes after it): the
  file is malformed. Refuse, exit 4. Analysing the fragment would produce a
  document that looks complete and is not.
- **A CBM block whose checksum fails**: reported as failed, with the expected
  and computed values, and its decoded contents still shown — marked
  unreliable. A cracker wants to see a damaged block, not have it hidden.
- **Zero pulses / empty data**: exit 4.

## 12. CBM block decoding

The ROM tape format, decoded properly rather than pattern-matched:

- Bit encoding: each bit is a pulse pair — short+medium = 0, medium+short = 1.
  Each byte is a new-data marker (long+medium), 8 bits LSB-first, then an odd
  parity bit. A block ends with an end-of-data marker (long+short) and an XOR
  checksum byte.
- Each block is recorded **twice**: the first copy is preceded by the countdown
  sync `$89 $88 … $81`, the repeat by `$09 $08 … $01`. Both are decoded; if they
  disagree the dossier says so and shows which bytes differ. That disagreement
  is exactly the kind of tape damage worth seeing.

Header block, 192 bytes:

| Offset | Meaning |
|---|---|
| `0` | type: 1 = relocatable PRG, 2 = SEQ data, 3 = non-relocatable PRG, 4 = SEQ header, 5 = end of tape |
| `1-2` | load address, little-endian |
| `3-4` | end address + 1 |
| `5-20` | filename, 16 chars PETSCII, space padded |
| `21-191` | padding |

From this the dossier gets, per file: name, type, load address, end address,
length, checksum verdict. `--extract` writes each recovered file as a `.prg`
with its correct two-byte load address.

## 13. Content recon

- **BASIC stub:** if a recovered file loads at `$0801`, detokenize it with the
  BASIC V2 token table and find the `SYS` token (`$9E`) and its address. That
  address is printed as the machine-code entry point — the first thing a cracker
  wants.
- **Packer signatures:** a small table of well-known decruncher byte signatures
  (Exomizer, Pucrunch, ByteBoozer, Time Cruncher and a handful more) matched
  against each extracted PRG. A match is reported with the matched offset. No
  match is reported as *no known packer signature found* — not as "unpacked".
- **Memory map:** an inline SVG of `$0000-$FFFF` with each file's load range
  drawn and labelled, and the I/O and ROM areas shaded for orientation.

## 14. Output

**HTML** is the real artifact: one file, no external requests, SVG inline,
screenshot inlined as a data URI, a print stylesheet that paginates cleanly to
A4 so the PDF render is not an afterthought.

Document order:

1. Cover — tape name, platform, PAL/NTSC, total time, title screenshot
2. Tape map
3. Summary — loader(s), file count, health verdict
4. File table — name, type, load, end, size, checksum
5. Memory map
6. Per-region detail — pulse clusters, block counts, timings
7. Provenance — tap2pdf version, which enrichments were used and which were not

**PDF:** the HTML rendered by headless Edge or Chrome, discovered in this order:
`--browser` if given, then `EDGE`/`CHROME` on PATH, then the standard Windows
install locations, then `chromium`/`google-chrome` on Linux. This is the path
already proven on this machine — the existing `CHEAT_HUNT_DEMO-HE.pdf` and
`CRACKTRO-CODE-HE.pdf` were both produced by `Skia/PDF` from HeadlessChrome.

**NFO:** the same dossier model rendered as plain 7-bit ASCII inside a 78-column
box, for pasting into a CSDb release.

## 15. Determinism

The HTML contains **no timestamp and no input path**, only the input file's
basename and the tap2pdf version. This is not cosmetic: golden-output tests diff
the generated HTML byte-for-byte, and a clock in the output makes that
impossible. Any future need for a date is an explicit `--date` flag.

## 16. Testing

Mirrors txt2dirart: `pytest`, fixtures, golden files.

**Fixtures are synthetic.** `tools/make_fixtures.py` synthesizes valid TAP images
from PRGs we author ourselves — a clean CBM-loader tape, a two-file tape, a
tape with a deliberately corrupted checksum, a truncated tape, a v0 tape with
overflow pulses, and a synthetic turbo region.

**No real game tape is ever committed to this repo.** They are copyrighted, and
a public GitHub repo is not an archive. Real tapes — THE HUMAN RACE, RAMBO —
stay where they already live, locally, for archival and manual eyeball checks
only. They are how we confirm the dossier is *right*; the synthetic fixtures are
how CI confirms it is *unchanged*. Neither role requires shipping a game.

Test groups:

- `test_tapfile.py` — header parsing, v0 vs v1 pulse decoding, overflow handling,
  the malformed cases and their exit codes
- `test_classify.py` — histogram, cluster detection, region segmentation
- `test_cbm.py` — bit/byte decoding, parity, checksum, header field extraction,
  first-copy vs repeat disagreement
- `test_basic.py` — detokenization and `SYS` extraction
- `test_report.py` — golden HTML, byte-for-byte
- `test_refusals.py` — every exit code in §6 is actually produced
- `test_hardening.py` — the input file is never opened for writing; a
  pathological TAP does not hang or exhaust memory

## 17. Repo layout

```
tap2pdf/
  tap2pdf.py            the tool, stdlib only
  README.md             output shown in the first screen, as txt2dirart does
  CHANGELOG.md
  LICENSE
  examples/             a synthetic tape + its dossier HTML and PDF, shipped
  docs/
    specs/              this file
    *.png               README images
  tests/
    fixtures/  golden/  conftest.py  test_*.py
  tools/
    make_fixtures.py    synthesizes the test tapes
    make_examples.py    rebuilds the shipped example dossier
  .github/workflows/
    ci.yml  release.yml
```

## 18. CI and release

`ci.yml`, matching txt2dirart:

- matrix: ubuntu-latest + windows-latest, Python 3.9 and 3.12 (3.9 is the floor;
  one OS is enough for it)
- `pytest tests/ -q`
- **a job that runs the tool with no packages installed at all** — the release
  binaries bundle only the stdlib, so a stdlib-only run is the thing that has to
  work
- **a job that rebuilds `examples/` and fails if it drifted** from the committed
  copy — a committed artifact drifts silently otherwise

`release.yml`, matching txt2dirart:

- triggered by a `v*` tag
- tests must pass before anything is built
- PyInstaller `--onefile`, `--strip` on Linux only (it corrupts the bundled
  Python DLL on Windows), with the same aggressive `--exclude-module` list
  trimmed to what tap2pdf actually imports
- **a binary smoke test that exercises real paths**, including at least one
  refusal and an assertion on its exit code
- a zip assembled with an explicit required-files manifest that **fails the build
  if any file is missing**
- `prerelease: true` for any tag containing `-`

## 19. Risks and open questions

| Risk | Handling |
|---|---|
| Turbo detection is heuristic and may mis-segment an exotic tape | §10: it reports clusters and counts, never a name. A mis-segmented region is visible in the tape map rather than asserted as fact. |
| PyInstaller exclude list breaks a binary that built cleanly | Same mitigation txt2dirart uses: the binary smoke test is not optional. |
| Headless browser flags change between Chrome versions | The PDF path is optional and isolated in `enrich`. Failure is exit 6 with a clear message, never a corrupt PDF. |
| Golden HTML tests are brittle under cosmetic changes | Accepted. Regenerating goldens is a deliberate, reviewed step — that is the point of them. |
| Packer signature table gives a false positive | Signatures are matched at plausible offsets only, and a match names the signature and offset so it can be judged. |

## 20. Version plan

- **1.0** — everything in this document.
- **1.1** — the loader knowledge base as a data file: what each named loader
  does and where its checks sit, keyed on the TAPClean loader name.
- **later** — `--batch` over a folder, producing one catalog document. Once the
  dossier model exists this is a renderer, not a new tool.
