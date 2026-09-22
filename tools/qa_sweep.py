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
           "files": 0, "checks": {}, "verdict": ""}
    started = time.time()
    try:
        with open(path, "rb") as fh:             # read only, always
            data = fh.read()
        row["bytes"] = len(data)
        args = tap2pdf.build_parser().parse_args([path])
        d = tap2pdf.analyse(data, args)
        counts = {tap2pdf.PASS: 0, tap2pdf.FAIL: 0, tap2pdf.NOT_CHECKED: 0}
        for c in d.checks:
            counts[c.result] = counts.get(c.result, 0) + 1
        row.update({
            "outcome": "ok", "exit_code": 0,
            "version": d.header.version, "platform": d.header.platform,
            "duration_s": round(d.duration, 2), "pulses": d.pulse_count,
            "regions": sorted(set(r.kind for r in d.regions)),
            "files": len(d.files), "checks": counts,
            "verdict": d.verdict_text,
        })
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
    seen = set(_key(r) for r in current)
    for name in was:
        if name not in seen:
            changes.append("GONE     %s" % name)
    return changes


def report(rows, slow_seconds):
    print("%-40s %-8s %5s %8s %6s  %s"
          % ("TAPE", "OUTCOME", "FILES", "PULSES", "SECS", "NOTE"))
    print("-" * 110)
    for r in rows:
        if r["outcome"] == "ok":
            note = r["verdict"]
        else:
            tail = r["error"].splitlines()
            note = tail[-1] if tail else ""
        print("%-40s %-8s %5s %8s %6.1f  %s"
              % (_key(r)[-40:], r["outcome"], r["files"], r["pulses"],
                 r["elapsed_s"], str(note)[:110]))

    crashes = [r for r in rows if r["outcome"] == "crash"]
    refused = [r for r in rows if r["outcome"] == "refused"]
    failing = [r for r in rows
               if r["outcome"] == "ok" and r["checks"].get(tap2pdf.FAIL)]
    slow = [r for r in rows if r["elapsed_s"] >= slow_seconds]

    print()
    print("%d tape(s): %d ok, %d refused, %d crashed."
          % (len(rows), len(rows) - len(refused) - len(crashes),
             len(refused), len(crashes)))
    print("%d tape(s) read but carry at least one failing check."
          % len(failing))
    if slow:
        print("slow (>= %.1fs): %s"
              % (slow_seconds, ", ".join(_key(r) for r in slow)))
    if crashes:
        print()
        print("CRASHES - these are bugs in tap2pdf, not bad tapes:")
        for r in crashes:
            print("  %s\n%s\n" % (_key(r), r["error"]))
    return crashes


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
    args = p.parse_args(argv)

    if not os.path.isdir(args.folder):
        sys.stderr.write("not a folder: %s\n" % args.folder)
        return 2
    paths = scan(args.folder, args.recurse)
    if not paths:
        print("no .tap files found in %s" % args.folder)
        return 0

    rows = sweep(paths, progress=True, root=args.folder)
    crashes = report(rows, args.slow_seconds)

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
