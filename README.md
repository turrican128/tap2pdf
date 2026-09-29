# tap2pdf

Turn a C64 `.tap` into an honest dossier about what is on it.

No dependencies, no install, no GUI. One tape in, one document out. Windows and Linux binaries need nothing on the machine — not even Python.

```
$ tap2pdf "Human Race.tap" --nfo

VERIFICATION
------------
TAP signature                  PASS
    C64-TAPE-RAW, version 1
Header platform and timing     PASS
    C64, PAL, as stated by the header
Header length vs actual data   PASS
    declared 788680 bytes, file holds 788680
Pulse stream integrity         PASS
    788644 pulses, none truncated
CBM block checksums            PASS
    all 4 decoded blocks pass
Block structure                PASS
    all 4 decoded block(s) belong to a complete file
First copy vs repeat           PASS
    all 1 file(s) agree
File length vs header range    PASS
    all 1 PRG file(s) carry exactly the bytes their header declares
Byte parity                    PASS
    no parity errors
Turbo region integrity         NOT CHECKED
    1 turbo region(s), 735908 pulses. The format is unidentified, so there is
    no checksum model to verify them against.
Unclassified stretches         NOT CHECKED
    256 pulses (0.0% of the tape) match no pulse pattern this tool
    recognises. They are folded into neighbouring regions in the table above
    so it stays readable, but nothing here analysed them.
Loader identification          NOT CHECKED
    no loader names available: run with `--tapclean <report>` to identify
    them
    [one further NOT CHECKED row trimmed for this README]

FILES
-----
NAME              TYPE                 LOAD   END+1     SIZE SUM
THE HUMAN RACE    non-relocatable PRG  $029F  $03C0      289 ok
```

That is the real output for a real tape. The HTML dossier beside it carries the same thing with a tape map, a memory map and the pulse breakdown, and `--pdf` renders it to A4.

---

## Contents

- [What it is for](#what-it-is-for)
- [The honesty rule](#the-honesty-rule)
- [Install](#install)
- [Quick start](#quick-start)
- [What the dossier tells you](#what-the-dossier-tells-you)
- [What it does not check](#what-it-does-not-check)
- [Command line](#command-line)
- [Exit codes](#exit-codes)
- [Checking a whole archive](#checking-a-whole-archive)
- [Building from source](#building-from-source)
- [Credits](#credits)

## What it is for

Most people who own a `.tap` did not make it. They downloaded it, and the question they actually have is **is this file any good** — before spending an evening on a tape that was already broken when they got it.

So that is what the dossier opens with: a verification report. After it come the things you want if you are about to crack the thing — what loads where, what the entry point is, and what the tape is actually made of.

It does not modify your tape. The file is opened read-only and never written, and every output path is checked against it first — pointing `-o` at the tape itself is refused with exit 5 rather than overwriting it.

`--extract` does overwrite existing files in the directory you name, which is the normal behaviour for an output directory. It will not write over the input tape.

## The honesty rule

The dossier never names something it has not established.

Without a TAPClean report, an unrecognised turbo region is written as *"unidentified turbo loader — 2 pulse clusters at $2F and $42, 41 blocks, 18.4 s"*. It is **not** written as "probably Novaload". A dossier that confidently names the wrong loader sends you down a blind alley, and is worse than one that admits it does not know.

The same rule governs the verification report, and it is the part worth trusting:

- **A check that did not run is never rendered as a pass.** Blank rows and green-by-default do not exist. `NOT CHECKED` is a result, and it carries its reason and, where there is one, the remedy.
- **The verdict never exceeds the evidence.** With a turbo region unchecked, the verdict says the *CBM portion* reads cleanly — not that the tape is good. Even a flawless tape refuses a clean bill of health while anything is still unchecked.
- **A damaged block is shown, not hidden.** A failing checksum is reported with its expected and computed values, and the block's contents are still listed.

## Install

Download the release zip, unpack it, and run the binary. Nothing else is needed.

Or run the script directly with any Python 3.9 or newer:

```sh
python tap2pdf.py mytape.tap
```

## Quick start

```sh
tap2pdf mytape.tap                    # writes mytape-dossier.html
tap2pdf mytape.tap --pdf              # and mytape-dossier.pdf
tap2pdf mytape.tap --nfo              # and a plain-ASCII mytape-dossier.nfo
tap2pdf mytape.tap --extract out/     # write the recovered PRGs too
```

`examples/` in the release holds a tape and the dossier it produces, so you can see the output before pointing it at anything of your own.

## What the dossier tells you

| Section | What is in it |
|---|---|
| Verification report | Every check, its result, and every check that did *not* run, with the reason |
| Verdict | One plain sentence, scoped to what was actually checked |
| Tape map | The whole tape as coloured bands — leader, CBM data, turbo, gaps — on a time axis |
| Files | Name, type, load address, end, size, checksum verdict |
| Entry point | The `SYS` address, detokenized out of the BASIC stub |
| Memory map | Where each file lands in the C64's 64K, with the ROM and I/O areas marked |
| Regions | Pulse-width clusters and timings, region by region |
| Provenance | The tap2pdf version, and which optional enrichments were used and which were not |

The HTML is one self-contained file: no external requests, SVG inline, and a print stylesheet so the PDF is not an afterthought.

## What it does not check

Being straight about this is the point of the tool, so it is in the README too:

- **Turbo loader data is not verified.** tap2pdf reads the CBM ROM-loader portion of a tape properly — checksums, parity, both recorded copies. For turbo data it can tell you the pulse clusters, block count and timing, but there is no checksum model for a format it has not identified.
- **Loaders are not named without help.** Run [TAPClean](https://github.com/Chesterbr/tapclean) on the tape first (`tapclean -t mytape.tap` writes `tcreport.txt`), then pass `--tapclean tcreport.txt`. TAPClean knows 130+ loaders; on *The Human Race* it gives `Loader identification  PASS  identified from the supplied TAPClean report: Visiload T2`. Without it, regions are described by their pulse signature. A file that is not a TAPClean report, or a report whose `TAP Size` does not match the tape, is refused with exit 6 rather than ingested.
- **A named loader is still not a verified one.** TAPClean's name for a turbo loader goes into the dossier, but tap2pdf has no checksum model for turbo data, so that row stays NOT CHECKED. TAPClean's own report carries its checksum test.
- **Protection schemes are not identified.** tap2pdf reports what is on a tape, not what the loader does to defend it. You get the shape of a scheme without a name for it: the size and load address of the CBM bootstrap, how many turbo regions follow it and how long they run, which blocks fail a checksum, and how much of the tape matched no pattern at all. On *The Human Race* that is a 289-byte non-relocatable PRG at `$029F` followed by 11 turbo regions holding 731,812 pulses — the silhouette of the scheme, with the reading of it left to you. Naming a protection it has not established would be the same mistake as naming a loader it has not established.
- **Crunchers are only reported when a signature is compiled in.** One ships today, for Exomizer 3.x `sfx sys`. Signatures are never written from memory: each is derived from a file crunched locally, and kept only if it stays identical across several differently-sized samples. When nothing matches, the dossier says no known cruncher was recognised — **not** that the file is uncrunched.
- **It is not TAPClean.** TAPClean tests, cleans and repairs tapes. tap2pdf explains one. Use both.

## Command line

```
tap2pdf <tape.tap> [options]

  -o, --output <path>     output HTML path (default: <tape>-dossier.html)
      --pdf               also render a PDF next to the HTML
      --pdf-only          render the PDF and remove the intermediate HTML;
                          -o may then name the .pdf itself
      --nfo               also write a plain-ASCII .nfo, for a release
      --tapclean <file>   ingest a TAPClean report for named loader IDs
      --vice <path>       x64sc, for a title screenshot - not implemented
                          yet: everything else is written, then exit 6
      --browser <path>    headless Edge/Chrome to use for --pdf
      --extract <dir>     write the recovered PRGs here
      --max-size <MB>     refuse an input larger than this (default 16)
      --pal / --ntsc      clock for time calculations (default: from the file);
                          the dossier marks the clock as set on the command line
      --title <text>      override the document title
      --quiet             suppress the progress lines on stderr
      --version
```

`--pdf` needs a headless Edge or Chrome, which every Windows machine already has. If one is not found, the HTML dossier is still written and the tool exits 6 saying so — it never exits 0 on a half-done job, and it never silently renders with a browser you did not ask for.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | dossier written |
| 1 | usage error — including two outputs that would land on one path (`-o x.nfo --nfo`) |
| 2 | input unreadable |
| 3 | not a TAP file |
| 4 | malformed TAP — refused |
| 5 | output could not be written |
| 6 | an enrichment was requested and is unavailable |

A tape truncated mid-pulse is refused rather than analysed, because a document built from a fragment looks complete and is not.

Exit 2 also covers a file larger than `--max-size`. Decoding needs roughly 130x the file size in memory, so an absurdly large input is refused with a message rather than taking the process down. The default of 16 MB covers any genuine C64 tape by a wide margin — the largest of 49 commercial tapes tested was 2.5 MB.

## Checking a whole archive

`tools/qa_sweep.py` runs the tool over every tape in a folder and reports one line each:

```sh
python tools/qa_sweep.py "C:/tapes" --recurse --json baseline.json
python tools/qa_sweep.py "C:/tapes" --recurse --compare baseline.json
```

1.0 was swept over 49 commercial tapes this way - Ocean, Hit Squad, Activision,
Sega conversions, multi-side releases, up to 2.4M pulses - with no crashes and
no contradictory verdicts. None of those tapes are in this repository, which is
the whole reason the sweep exists.

1.0.4 was swept over 552 local tapes - 488 commercial originals and the
rest modern releases - against a 1.0.3 baseline: no crashes, no
self-contradicting dossiers, one tape correctly refused as truncated, and
every changed verdict inspected by hand.

1.0.5 touched the output guards and the copy comparison, so it was re-swept
against a 1.0.4 baseline over the 49 commercial tapes still on the build
machine: no crashes, no self-contradicting dossiers, and not one verdict
moved. The 552-tape archive is not kept locally, so that larger sweep stands
as a 1.0.4 result and is not claimed for 1.0.5.

Every tape is rendered as well as analysed. A dossier that contradicts
itself - a clean verdict beside a FAIL - counts as a bug like a crash, and a
dossier over `--max-html-kb` or a tape split into more than `--max-regions`
regions is flagged. `--compare` names each check that moved.

Its invariant: **a refusal is a pass, a crash is a bug.** Refusing a damaged tape is the tool working. `--compare` diffs a run against a saved baseline, which is what to use after changing anything in the classifier.

## Building from source

```sh
git clone https://github.com/turrican128/tap2pdf
cd tap2pdf
python -m pytest tests/ -q
```

The tests need only Python and pytest. The fixture tapes are synthetic — generated by `tools/make_fixtures.py` from PRGs written for the purpose — because no copyrighted game tape belongs in a public repository. `.gitignore` enforces that.

Release binaries are built by GitHub Actions with PyInstaller on `windows-latest` and `ubuntu-latest`.

## Credits

Code by **DR.J/Delysid**.

Written while cracking *The Human Race* (Mastertronic, 1985) and *Rambo: First Blood Part II*, where the first hour of every tape went on working out what was even on it.

MIT licensed. Do what you like with it.
