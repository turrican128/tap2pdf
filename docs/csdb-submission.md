# CSDb submission — tap2pdf 1.0.5

Everything needed to fill in the "Add release" form at
<https://csdb.dk/additem.php?type=release>. Same shape as the txt2dirart 1.0.2
submission, since this is the same kind of thing: a PC-side command-line tool
that works on C64 files.

**Submit this yourself — under your own CSDb account.**

---

## Form fields

| field | value |
|---|---|
| **Name** | `tap2pdf 1.0.5` |
| **Type** | `Other Platform C64 Tool` — same category txt2dirart went under. Confirm the exact wording in the dropdown; CSDb renames these occasionally. |
| **Released by** | DR.J / Delysid |
| **Release date** | the day you submit |
| **Website** | `https://github.com/turrican128/tap2pdf` |

### Credits

```
Code  -  DR.J of Delysid
```

Add a `Test` line if anyone runs it before you submit. This tool's whole claim
is that it does not lie about what it found, so a named tester carries more
weight here than it would for most tools — it says someone other than the
author checked the output against a tape they knew.

Worth asking one or two people in the tape-preservation corner specifically,
since they own the tapes that would break it.

### Downloads

Upload both, in this order:

1. **`tap2pdf-1.0.5.zip`** — from the GitHub release page, not built locally.
   About 14 MB. Contains `tap2pdf.exe` (Windows), `tap2pdf` (Linux),
   `tap2pdf.py` (the source, one file, stdlib only), README, CHANGELOG,
   LICENSE, `examples/` with a tape and the dossier it produces, and
   `tools/qa_sweep.py` for checking a whole archive.

2. **`example-dossier.pdf`** — as its own download.
   This is the equivalent of shipping `examples.d64` with txt2dirart: it lets
   someone see exactly what the tool produces before downloading a binary and
   pointing it at their own tape. For a tool whose output *is* the product,
   that matters more than usual.

   You need to generate it first — the repo ships the HTML and NFO but not a
   PDF:

   ```
   tap2pdf examples/example.tap -o example-dossier.html --pdf
   ```

   Upload the resulting `example-dossier.pdf`.

### Screenshot

Two are in the repo. **Upload `docs/screenshot-dossier.png`** — that is the
one that sells the tool.

- **`docs/screenshot-dossier.png`** — the top of `examples/example-dossier.html`:
  the title block, the verification report, and the tape map. It shows the PASS
  rows, the amber `NOT CHECKED` rows *with their reasons*, the one-sentence
  verdict that refuses to call the tape clean, and the coloured map. Anyone who
  has ever wondered whether a downloaded tape is any good understands the tool
  immediately from that picture.
- **`docs/screenshot-cli.png`** — the command line: the usage summary and a
  real run writing the dossier and the PDF. Useful as a second image, for people
  who want to see it is an ordinary command-line tool with no install.

Do not crop the dossier shot to just the tape map. The map is the prettiest
part, but the verification report is the part that is actually different from
everything else out there.

The dossier shot is rendered from the synthetic `example.tap` that ships with
the tool, so no tape content in it comes from a commercial release. The CLI
shot names a commercial tape on the command line and shows the two lines the
tool printed - a filename and two paths, no tape data and no dossier content.
That is fine to publish; a screenshot of a *dossier* for a commercial tape
would not be.

---

## Summary text

Keep it short — tool entries on CSDb are thin, and the README does the real
work. This text is ready to paste:

```
tap2pdf turns a C64 .tap into an honest dossier about what is on it.

It opens with a verification report: every check it ran, the result, and --
the part worth reading -- every check it did NOT run, and why. A check that
did not run is never shown as a pass, and the verdict never claims more than
the evidence supports. Even a flawless tape refuses a clean bill of health
while anything remains unchecked.

After that: a tape map of the whole recording, banded by region on a time
axis; the files recovered from the CBM ROM loader with their load addresses,
sizes and checksum verdicts; a memory map; and the SYS entry point
detokenized out of the BASIC stub.

It never names a loader it has not established. Without a TAPClean report an
unrecognised turbo region is described by its pulse clusters and block count,
not guessed at -- a dossier that confidently names the wrong loader is worse
than one that admits it does not know. Run TAPClean first and pass its
report with --tapclean tcreport.txt, and the dossier names the loader.

It does not modify your tape: the file is opened read-only and never written.
It is not TAPClean and does not replace it. TAPClean tests, cleans and repairs
a tape; tap2pdf explains one. Use both.

Output is one self-contained HTML file. --pdf renders it to A4, --nfo writes a
plain-ASCII summary you can paste into a release, --extract writes out the
PRGs it recovered.

Command line, no GUI, no dependencies. Single-file binaries for Windows and
Linux; the Python source is one file and imports nothing outside the standard
library.

Swept over 552 tapes - 488 commercial originals plus modern releases -
with no crashes, and checked against a real TAPClean report; see the
CHANGELOG.

Source and full documentation:
https://github.com/turrican128/tap2pdf
```

---

## Before you submit — checklist

- [ ] The GitHub release `v1.0.5` exists and its build was green on both platforms
- [ ] You downloaded the zip **from the release page** and ran **both** binaries
      once on a real machine, not just in CI
- [ ] `tap2pdf --version` prints `tap2pdf 1.0.5` — not 1.0 and not a `-dev` version
- [ ] You generated `example-dossier.pdf` and opened it; the tables are not cut
      across page breaks
- [ ] You uploaded `docs/screenshot-dossier.png`, and the verification report
      is visible in it
- [ ] You opened a dossier and **read it** — every row says something true
- [ ] `tools/qa_sweep.py` over your own archive: zero crashes
- [ ] Repository is public and the README renders
- [ ] No links in the README are 404s
- [ ] `git ls-files | grep .tap` lists only the synthetic fixtures and the
      example — no commercial tape anywhere in the repo or its history

---

## One thing to be ready for

Someone will run it on a tape it handles badly, and say so in the comments.
That is fine, and the tool is built to make it cheap: ask them for the tape's
name and the exit code, and if it crashed, that is a bug worth a point release.
If it *refused*, it did its job.

The distinction is worth stating in your reply if it comes up, because a
refusal reads like a failure to someone who has not read the README.
