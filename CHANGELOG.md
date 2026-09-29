# Changelog

## 1.0.4

A pre-release QA pass against real tapes, a real TAPClean report and a
review of the decoder. Most of what it found was the dossier stating
something untrue. **Take this version.**

- **`--tapclean` never worked with a real report.** The parser looked for
  `Loader:` and `Loader detected:`, which TAPClean does not write; its report
  says `Loader ID   : Visiload T2`. Every real report therefore "named no
  loader", and the dossier showed that as a **PASS**. The tests passed
  because they were written against the same invented format. It now reads
  the real field (report and console output), and a file that is not a
  TAPClean report, or one whose `TAP Size` does not match the tape, is
  refused with exit 6 instead of being ingested. `Loader ID: n/a` is a pass
  only when no turbo region was found here either. A named loader also
  reaches the Turbo region row, which stays NOT CHECKED - naming a loader is
  not verifying its data.
- **`--pdf-only -o out.pdf` left nothing on disk and exited 0.** The PDF was
  rendered over its own HTML source, then deleted as "the intermediate
  HTML". `-o x.nfo --nfo` likewise wrote the NFO over the HTML. Every output
  path is now worked out before anything is written; two on one path is a
  usage error (exit 1), except `--pdf-only` with a `.pdf` path, which now
  means what it says. `--pdf-only` no longer announces the HTML it deletes.
- **`--vice` made the dossier say "VICE screenshot: used".** Nothing was
  taken - the feature is not implemented. Provenance now says "not used",
  and the run exits 6 after writing everything else, as the exit-code table
  promises for an unavailable enrichment.
- **A good file could vanish from the Files table.** Any 192-byte block was
  taken for a header, so a SEQ file's second data block, or a data block
  whose header was lost, swallowed the next real file as its "data". Headers
  are now recognised by length *and* type byte, SEQ files are assembled
  from all their data blocks (and extracted as `.seq`, without a load
  address), and a header followed by another header stays a loose block.
- **`--extract` wrote the damaged copy when the repeat was intact.** When the
  first copy fails its checksum and the repeat passes, the repeat's bytes
  are used, the Files table says `ok (repeat copy)`, and extraction says so.
  The failing block is still reported under CBM block checksums.
- **Wrong entry points were stated as fact.** `SYS 12*4096` became SYS 12,
  `REM SYS 1234` became SYS 1234, and a `$9E` byte inside a string was read
  as SYS. The entry point is now taken from the raw tokens, outside strings
  and REM, and only when it is a plain constant (`SYS 2064`, `SYS(2061)`,
  `SYS 0`); an expression gives "no plain SYS constant found". Listings no
  longer expand tokens inside strings or REM.
- **`--pal` on an NTSC tape read "PAL, as stated by the header".** The check
  now reports what the header says and, separately, the clock set on the
  command line; the headline shows `(--pal)`. `--pal` with `--ntsc` is a
  usage error.
- A tape shorter than its header declares is no longer called "cosmetic":
  data may be missing. A header end address of `$0000` is the top of memory,
  not a range below the load address. A cruncher found in one file is no
  longer credited to another file with the same name. A C16 signature with a
  C64 platform byte is not a pass. TAP version 2 is refused as unsupported
  rather than "unknown". `--max-size` must be a positive number: `inf` and
  `nan` crashed with a traceback, `0` and negatives were accepted.

## 1.0.3

The Regions table was reporting regions that did not exist.

- **A tape was split into thousands of regions that were not there.** Night
  Breed reported 4,112 regions for a tape with a few dozen; 55% of them were
  a single 256-pulse window. Each window was classified on its own by three
  thresholds - a cluster within tolerance of a CBM width, a single cluster
  above 90%, a third cluster above the 5% noise floor - and real tape speed
  wobbles by a couple of pulse units, which crosses all three edges
  repeatedly. One unchanging stretch of tape came out labelled cbm, turbo,
  leader and unclassified in consecutive windows.

  This mattered twice over. The table was telling the reader something
  untrue, which is the failure this tool exists to avoid; and it made a
  997 KB dossier that headless Edge could not lay out, so two real tapes
  could not render a PDF at all.

  Short runs are now merged with a neighbour and the merged span is
  **re-classified from the pooled histogram**, judging the stretch on all its
  evidence rather than voting on labels decided 256 pulses at a time.
  Smoothing the labels alone does not work: with a strict cbm/turbo
  alternation every run is one window, so each takes its neighbour's label
  and they simply swap, forever.

  "Too short to be real" scales with the tape. A 3-window region is 5% of a
  small tape and 0.03% of a 9,600-window one, so an absolute floor that fixed
  a long tape destroyed short ones - at a floor of 8 windows a two-file tape
  collapsed from its genuine 8 regions to 1. The minimum is now
  `max(3, windows / 400)`, chosen by measuring 5 real tapes against 5
  fixtures.

  Night Breed: 4,112 regions to 48, a 997 KB dossier to 19.6 KB, and a PDF
  that had never rendered now takes seven seconds. Normal tapes are
  untouched - Enduro Racer 6 regions before and after, Human Race 9, Cobra 8
  - and every fixture keeps its real structure.

## 1.0.2

One fix, from a second external review. **Take this version.**

- **The input tape could be overwritten.** Passing the tape's own path to
  `-o` replaced it with the HTML dossier and exited 0, destroying the file
  the tool had been asked to examine. The README promises the tape is opened
  read-only and never written; 1.0 and 1.0.1 did not keep that promise.

  Every output path is now checked against the input before anything is
  written: the HTML, the NFO, the PDF, and each extracted PRG. A collision is
  refused with exit 5 and the tape is left untouched. Extraction works out
  all its target paths and checks them before writing any, so a refusal on
  the second file cannot leave the first one on disk.

  The test that was supposed to cover this only wrapped `analyse()`, which
  does no writing at all - the write happens in `main()`. It proved nothing.
  It now exercises the real command line.

## 1.0.1

Fixes from an external code review of 1.0. Take this version: 1.0 could
report a clean result for checks it had not actually run.

- **A missing repeat copy was reported as the copies agreeing.** A tape
  carrying only the first copy of each block got PASS, "all files agree",
  for a comparison that never happened. Missing repeats are now counted
  separately: FAIL when copies differ, NOT CHECKED when there is no repeat,
  PASS only when a real comparison passed.
- **Blocks that decoded but formed no file vanished from verification.** On
  Cobra (The Hit Squad) that hid a failing checksum behind "no CBM ROM-loader
  blocks were found on this tape"; on WWF Side 1 it hid two. Every decoded
  block is now verified, and a new Block structure check reports blocks that
  belong to no complete file instead of dropping them.
- **Repeat blocks were never checksum-verified at all.** The block list held
  only the first copy of each pair, so half the blocks on a normal tape went
  unchecked. A clean single-file tape reported "all 2 blocks pass" for a tape
  that has four.
- **The header's address range was never compared with the data recovered.**
  The dossier could print a claimed range and a recovered size that cannot
  both be true. Now reported per file, including a header whose end address
  falls below its load address.
- **An unknown platform or video byte became a confident "C64 / PAL".** Every
  duration in the document was then computed from a clock the file never
  named. The bytes are now reported as unknown and the timing stated as an
  assumption; `--pal`/`--ntsc` resolves it.
- **The QA sweep keyed tapes by basename**, so in a recursive run two tapes
  called `game.tap` in different folders overwrote each other and whatever
  changed in the loser was never reported. Rows now carry a path-relative id.
- **Large inputs could exhaust memory rather than produce a message.**
  Measured at 179x the file size; `__slots__` on the pulse object brings that
  to 139x, and an input above `--max-size` (default 16 MB) is refused before
  it is read.
- The release build pins PyInstaller, pytest and the third-party release
  action, since those decide the bytes people download.

## 1.0

First release.

A `.tap` goes in; a self-contained HTML dossier comes out, opening with a
verification report of the file's health. `--pdf` renders it, `--nfo` gives you
the same thing as plain ASCII for a release, `--extract` writes out the files it
recovered.

- **The verification report says what was not checked.** Every check carries its
  result and its reason, and a check that did not run is shown as `NOT CHECKED`,
  never as a pass and never as a blank row. The verdict is scoped to the
  evidence: with a turbo region unverified, it says the CBM portion reads
  cleanly rather than that the tape is good.
- **It never names a loader it has not established.** An unrecognised turbo
  region is reported by its pulse clusters and block count. Pass `--tapclean` to
  get real names. A dossier that confidently names the wrong loader is worse
  than one that admits it does not know.
- **The CBM ROM-loader portion is decoded properly** — bit pairs, LSB-first
  bytes, odd parity, XOR checksums, and both recorded copies compared against
  each other. A block with a failing checksum is shown with its expected and
  computed values, not hidden.
- Tape map and memory map as inline SVG. Tape-map bands are proportional to
  time, not pulse count, so a gap of few long pulses looks as long as it is.
- The `SYS` entry point is detokenized out of the BASIC stub.
- The input tape is opened read-only and never written. There is a test for it.
- A tape truncated mid-pulse is refused with exit 4 rather than analysed: a
  document built from a fragment looks complete and is not.
- `--pdf` uses headless Edge or Chrome. If one is not found the HTML is still
  written and the tool exits 6 saying why. A browser named with `--browser` is
  used or nothing is — it will not quietly render with a different one.
- No cruncher signature is written from memory, and none is added until it has
  been shown to be *stable*. The first Exomizer signature derived looked fine
  and was worthless: taken from one sample, it carried address operands that
  move with the crunched file's size, so it would have matched that single file
  and nothing else — the tool would have appeared to support Exomizer while
  silently finding none. The signature that ships is the bytes that stayed
  identical across four genuinely different crunched files, and a committed
  sample keeps it tested. When nothing matches, the dossier says no known
  cruncher was recognised — never that the file is uncrunched.
- **The verdict cannot call a read clean when nothing was read.** Sweeping 49
  real commercial tapes found four - Cobra (The Hit Squad), Time Scanner side 2,
  both WWF sides - whose pulses look like the CBM ROM loader but yield no
  complete block. The verdict claimed the CBM portion read cleanly while the
  checksum row in the same table said NOT CHECKED. Pulses shaped like a loader
  are not data that was read, and it says so now.
- **Verified against 49 commercial tapes** - Ocean, Hit Squad, Activision, Sega
  conversions, multi-side releases, up to 2.4M pulses - with no crashes and no
  contradictory verdicts. None of them are in this repository.
- `tools/qa_sweep.py` runs the tool over a folder of real tapes, where a refusal
  is a pass and only a crash is a failure. Real tapes cannot live in this repo,
  so this is how they get exercised.
