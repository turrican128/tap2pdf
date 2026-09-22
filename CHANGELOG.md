# Changelog

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
