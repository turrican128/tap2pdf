#!/usr/bin/env python3
"""Run tap2pdf over every tape in a folder and report what happened.

The unit tests use synthetic tapes, because no real game tape may enter this
repo. This is how real tapes get exercised: point it at your own archive.

    python tools/qa_sweep.py "C:/tapes" --recurse --json baseline.json
    python tools/qa_sweep.py "C:/tapes" --recurse --compare baseline.json

The invariant:

    A refusal is a pass. A crash is a bug.

tap2pdf refusing a damaged tape with a documented exit code is the tool
working correctly, and is not counted as a failure. An unhandled traceback
is always a failure, whatever the tape looked like.

Nothing here writes into the folder being scanned.

Beyond crashes, every tape is rendered to HTML and NFO in memory, and the
result is held to two more standards:

  - a contradiction - the verdict says the CBM portion reads cleanly while a
    check in the same report FAILs, or the NFO is not 7-bit ASCII - is a bug
    exactly like a crash;
  - a dossier over --max-html-kb, or more regions than --max-regions, is
    flagged: 1.0.3 shredded one tape into 4,112 regions and a 997 KB
    dossier that no browser could turn into a PDF, and nothing here noticed.
"""
import argparse
import json
import os
import sys
import time
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tap2pdf  # noqa: E402


def scan(folder, recurse=False):
    out = []
    if recurse:
        for root, _dirs, names in os.walk(folder):
            for n in names:
                if n.lower().endswith(".tap"):
                    out.append(os.path.join(root, n))
    else:
        for n in os.listdir(folder):
            if n.lower().endswith(".tap"):
                out.append(os.path.join(folder, n))
    return sorted(out)


def identity(path, root=None):
    """A stable key for one tape.

    The basename alone is not unique: TOSEC sets and side-a/side-b
    layouts repeat names across folders, and keying a baseline by
    basename let one tape silently overwrite another, hiding whatever
    changed in the loser.
    """
    if root:
        try:
            rel = os.path.relpath(path, root)
        except ValueError:                   # different drive on Windows
            rel = path
    else:
        rel = os.path.basename(path)
    return rel.replace(os.sep, "/")


def examine(path, root=None):
    row = {"id": identity(path, root),
           "name": os.path.basename(path), "bytes": 0, "outcome": "crash",
           "exit_code": None, "error": "", "version": None, "platform": None,
           "duration_s": 0.0, "elapsed_s": 0.0, "pulses": 0, "regions": [],
           "files": 0, "checks": {}, "check_results": {}, "verdict": "",
           "region_count": 0, "html_bytes": 0, "problems": []}
    started = time.time()
    try:
        # Same limit the CLI applies. One oversized tape in an archive must
        # not take the whole sweep down; it is refused, and a refusal is a
        # pass.
        tap2pdf.refuse_if_too_large(os.path.getsize(path),
                                    tap2pdf.DEFAULT_MAX_INPUT_MB,
                                    os.path.basename(path))
        with open(path, "rb") as fh:             # read only, always
            data = fh.read()
        row["bytes"] = len(data)
        args = tap2pdf.build_parser().parse_args([path])
        d = tap2pdf.analyse(data, args)
        counts = {tap2pdf.PASS: 0, tap2pdf.FAIL: 0, tap2pdf.NOT_CHECKED: 0}
        for c in d.checks:
            counts[c.result] = counts.get(c.result, 0) + 1
        # Render both documents: a crash in the renderer is a crash, and the
        # rendered size is what a browser has to lay out for --pdf.
        html = tap2pdf.render_html(d)
        nfo = tap2pdf.render_nfo(d)
        row.update({
            "outcome": "ok", "exit_code": 0,
            "version": d.header.version, "platform": d.header.platform,
            "duration_s": round(d.duration, 2), "pulses": d.pulse_count,
            "regions": sorted(set(r.kind for r in d.regions)),
            "region_count": len(d.regions),
            "files": len(d.files), "checks": counts,
            "check_results": dict((c.name, c.result) for c in d.checks),
            "verdict": d.verdict_text,
            "html_bytes": len(html.encode("utf-8")),
            "problems": contradictions(d, nfo),
        })
        if row["problems"]:
            row["outcome"] = "contradiction"
    except tap2pdf.Refusal as r:
        # Refusing a bad tape is the tool working correctly.
        row["outcome"] = "refused"
        row["exit_code"] = r.code
        row["error"] = r.message
    except Exception:
        row["outcome"] = "crash"
        row["error"] = traceback.format_exc(limit=6).strip()
    row["elapsed_s"] = round(time.time() - started, 2)
    return row


def contradictions(d, nfo):
    """Statements in one dossier that cannot all be true."""
    out = []
    failed = [c.name for c in d.checks if c.result == tap2pdf.FAIL]
    if failed and "reads cleanly" in d.verdict_text:
        out.append("verdict says the tape reads cleanly, but %s FAIL"
                   % ", ".join(failed))
    if not d.files and "reads cleanly" in d.verdict_text:
        out.append("verdict says the tape reads cleanly, but no file was "
                   "recovered")
    try:
        nfo.encode("ascii")
    except UnicodeEncodeError as exc:
        out.append("NFO is not 7-bit ASCII: %s" % exc)
    return out


def sweep(paths, progress=False, root=None):
    rows = []
    for i, p in enumerate(paths, 1):
        if progress:
            sys.stderr.write("[%d/%d] %s\n"
                             % (i, len(paths), identity(p, root)))
            sys.stderr.flush()
        rows.append(examine(p, root))
    return rows


def _key(row):
    """Prefer the path-relative id; fall back to the bare name so a
    baseline written before this change still compares."""
    return row.get("id") or row["name"]


def compare(baseline, current):
    was = dict((_key(r), r) for r in baseline)
    changes = []
    for r in current:
        old = was.get(_key(r))
        if old is None:
            changes.append("NEW      %s (%s)" % (_key(r), r["outcome"]))
            continue
        if old["outcome"] != r["outcome"]:
            changes.append("OUTCOME  %s: %s -> %s"
                           % (_key(r), old["outcome"], r["outcome"]))
        elif old.get("verdict") != r.get("verdict"):
            changes.append("VERDICT  %s: %r -> %r"
                           % (_key(r), old.get("verdict"),
                              r.get("verdict")))
        elif old.get("files") != r.get("files"):
            changes.append("FILES    %s: %s -> %s"
                           % (_key(r), old.get("files"), r.get("files")))
        elif old.get("checks") != r.get("checks"):
            changes.append("CHECKS   %s: %s -> %s"
                           % (_key(r), old.get("checks"), r.get("checks")))
        else:
            continue
        # Which named checks moved. A baseline written before check_results
        # existed has none, and then this says nothing.
        before = old.get("check_results") or {}
        after = r.get("check_results") or {}
        for name in sorted(set(before) | set(after)):
            if before and after and before.get(name) != after.get(name):
                changes.append("    %-28s %s -> %s"
                               % (name[:28], before.get(name, "-"),
                                  after.get(name, "-")))
    seen = set(_key(r) for r in current)
    for name in was:
        if name not in seen:
            changes.append("GONE     %s" % name)
    return changes


def check_summary(rows):
    """PASS / FAIL / NOT CHECKED per check, across every tape that read."""
    table = {}
    for r in rows:
        for name, result in (r.get("check_results") or {}).items():
            t = table.setdefault(name, {tap2pdf.PASS: 0, tap2pdf.FAIL: 0,
                                        tap2pdf.NOT_CHECKED: 0})
            t[result] = t.get(result, 0) + 1
    lines = ["%-30s %6s %6s %12s" % ("CHECK", "PASS", "FAIL", "NOT CHECKED")]
    for name in sorted(table):
        t = table[name]
        lines.append("%-30s %6d %6d %12d"
                     % (name[:30], t[tap2pdf.PASS], t[tap2pdf.FAIL],
                        t[tap2pdf.NOT_CHECKED]))
    return lines


def report(rows, slow_seconds, max_html_kb=400, max_regions=200):
    print("%-40s %-8s %5s %8s %6s  %s"
          % ("TAPE", "OUTCOME", "FILES", "PULSES", "SECS", "NOTE"))
    print("-" * 110)
    for r in rows:
        if r["outcome"] == "ok":
            note = r["verdict"]
        elif r["outcome"] == "contradiction":
            note = "; ".join(r["problems"])
        else:
            tail = r["error"].splitlines()
            note = tail[-1] if tail else ""
        print("%-40s %-8s %5s %8s %6.1f  %s"
              % (_key(r)[-40:], r["outcome"], r["files"], r["pulses"],
                 r["elapsed_s"], str(note)[:110]))

    crashes = [r for r in rows if r["outcome"] == "crash"]
    contra = [r for r in rows if r["outcome"] == "contradiction"]
    refused = [r for r in rows if r["outcome"] == "refused"]
    failing = [r for r in rows
               if r["outcome"] in ("ok", "contradiction")
               and r["checks"].get(tap2pdf.FAIL)]
    slow = [r for r in rows if r["elapsed_s"] >= slow_seconds]
    large = [r for r in rows if r.get("html_bytes", 0) > max_html_kb * 1024]
    shredded = [r for r in rows if r.get("region_count", 0) > max_regions]

    print()
    print("%d tape(s): %d ok, %d refused, %d crashed, %d contradicted "
          "themselves."
          % (len(rows), len(rows) - len(refused) - len(crashes) - len(contra),
             len(refused), len(crashes), len(contra)))
    print("%d tape(s) read but carry at least one failing check."
          % len(failing))
    if slow:
        print("slow (>= %.1fs): %s"
              % (slow_seconds, ", ".join(_key(r) for r in slow)))
    if large:
        print("dossier over %d KB (a browser may not render the PDF): %s"
              % (max_html_kb, ", ".join("%s (%d KB)"
                                         % (_key(r), r["html_bytes"] // 1024)
                                         for r in large)))
    if shredded:
        print("more than %d regions (the classifier may be shredding the "
              "tape): %s" % (max_regions, ", ".join(
                  "%s (%d)" % (_key(r), r["region_count"])
                  for r in shredded)))
    summary = check_summary(rows)
    if len(summary) > 1:
        print()
        for line in summary:
            print(line)
    if crashes:
        print()
        print("CRASHES - these are bugs in tap2pdf, not bad tapes:")
        for r in crashes:
            print("  %s\n%s\n" % (_key(r), r["error"]))
    if contra:
        print()
        print("CONTRADICTIONS - the dossier states things that cannot all "
              "be true:")
        for r in contra:
            print("  %s: %s" % (_key(r), "; ".join(r["problems"])))
    return crashes + contra


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Run tap2pdf over a folder of tapes. "
                    "A refusal is a pass; a crash is a bug.")
    p.add_argument("folder")
    p.add_argument("--recurse", action="store_true")
    p.add_argument("--json", metavar="FILE", help="write the results as JSON")
    p.add_argument("--compare", metavar="FILE",
                   help="diff against a saved run")
    p.add_argument("--slow-seconds", type=float, default=10.0)
    p.add_argument("--max-html-kb", type=int, default=400,
                   help="flag a dossier larger than this (default 400)")
    p.add_argument("--max-regions", type=int, default=200,
                   help="flag a tape split into more regions (default 200)")
    args = p.parse_args(argv)

    if not os.path.isdir(args.folder):
        sys.stderr.write("not a folder: %s\n" % args.folder)
        return 2
    paths = scan(args.folder, args.recurse)
    if not paths:
        print("no .tap files found in %s" % args.folder)
        return 0

    rows = sweep(paths, progress=True, root=args.folder)
    crashes = report(rows, args.slow_seconds, args.max_html_kb,
                     args.max_regions)

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=1, sort_keys=True)
        print("wrote %s" % args.json)

    changed = []
    if args.compare:
        with open(args.compare, encoding="utf-8") as fh:
            changed = compare(json.load(fh), rows)
        print()
        if changed:
            print("CHANGED SINCE THE BASELINE:")
            for line in changed:
                print("  " + line)
        else:
            print("nothing changed since the baseline.")

    if crashes:
        return 1
    if changed:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
