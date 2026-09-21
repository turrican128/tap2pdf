# Changelog

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
- No cruncher signature is written from memory. The table ships empty and
  entries may only be added after being observed in a file crunched locally; a
  test enforces that each one names its sample.
- `tools/qa_sweep.py` runs the tool over a folder of real tapes, where a refusal
  is a pass and only a crash is a failure. Real tapes cannot live in this repo,
  so this is how they get exercised.
