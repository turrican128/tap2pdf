import json
import shutil
import subprocess
import sys

from conftest import FIXTURES, ROOT

sys.path.insert(0, str(ROOT / "tools"))
import qa_sweep  # noqa: E402
import tap2pdf  # noqa: E402


def test_it_finds_the_tapes_in_a_folder():
    found = qa_sweep.scan(str(FIXTURES))
    assert any(p.endswith("clean_single.tap") for p in found)


def test_a_good_tape_is_reported_ok_with_its_verdict():
    r = qa_sweep.examine(str(FIXTURES / "clean_single.tap"))
    assert r["outcome"] == "ok"
    assert r["files"] == 1
    assert r["checks"]["FAIL"] == 0
    assert r["verdict"]


def test_a_refusal_is_a_pass_not_a_failure():
    r = qa_sweep.examine(str(FIXTURES / "truncated.tap"))
    assert r["outcome"] == "refused"
    assert r["exit_code"] == tap2pdf.EXIT_MALFORMED
    assert r["error"]


def test_a_crash_is_reported_as_a_crash(monkeypatch):
    def boom(*a, **kw):
        raise ValueError("synthetic explosion")

    monkeypatch.setattr(tap2pdf, "segment", boom)
    r = qa_sweep.examine(str(FIXTURES / "clean_single.tap"))
    assert r["outcome"] == "crash"
    assert "synthetic explosion" in r["error"]


def test_the_sweep_exits_zero_when_only_refusals_occur(tmp_path):
    out = tmp_path / "b.json"
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "qa_sweep.py"),
                        str(FIXTURES), "--json", str(out)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    saved = json.loads(out.read_text(encoding="utf-8"))
    assert any(x["outcome"] == "refused" for x in saved)
    assert any(x["outcome"] == "ok" for x in saved)


def test_comparing_against_a_baseline_flags_a_changed_verdict():
    base = [{"name": "a.tap", "outcome": "ok", "verdict": "clean",
             "files": 1, "checks": {"PASS": 5, "FAIL": 0, "NOT CHECKED": 1}}]
    now = [{"name": "a.tap", "outcome": "ok", "verdict": "DIFFERENT",
            "files": 1, "checks": {"PASS": 5, "FAIL": 0, "NOT CHECKED": 1}}]
    changes = qa_sweep.compare(base, now)
    assert changes and "a.tap" in changes[0]


def test_an_unchanged_run_reports_no_changes():
    rows = [{"name": "a.tap", "outcome": "ok", "verdict": "clean",
             "files": 1, "checks": {"PASS": 5, "FAIL": 0, "NOT CHECKED": 1}}]
    assert qa_sweep.compare(rows, list(rows)) == []


def test_the_sweep_never_writes_into_the_folder_it_scans(tmp_path):
    shutil.copy(str(FIXTURES / "clean_single.tap"), str(tmp_path / "x.tap"))
    before = sorted(p.name for p in tmp_path.iterdir())
    qa_sweep.sweep([str(tmp_path / "x.tap")])
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_tapes_with_the_same_basename_do_not_collide(tmp_path):
    # TOSEC sets and side-a/side-b layouts repeat basenames across folders.
    # Keying a baseline by basename alone made one silently overwrite the
    # other, hiding whatever changed in the loser.
    a = tmp_path / "side-a"
    b = tmp_path / "side-b"
    a.mkdir()
    b.mkdir()
    shutil.copy(str(FIXTURES / "clean_single.tap"), str(a / "game.tap"))
    shutil.copy(str(FIXTURES / "bad_checksum.tap"), str(b / "game.tap"))

    rows = qa_sweep.sweep(qa_sweep.scan(str(tmp_path), recurse=True),
                          root=str(tmp_path))
    ids = [r["id"] for r in rows]
    assert len(set(ids)) == 2, ids
    assert all("/" in i for i in ids), ids


def test_a_changed_verdict_is_not_hidden_by_a_duplicate_basename():
    base = [{"id": "side-a/game.tap", "name": "game.tap", "outcome": "ok",
             "verdict": "clean", "files": 1, "checks": {}},
            {"id": "side-b/game.tap", "name": "game.tap", "outcome": "ok",
             "verdict": "clean", "files": 1, "checks": {}}]
    now = [dict(base[0]),
           dict(base[1], verdict="SOMETHING ELSE")]
    changes = qa_sweep.compare(base, now)
    assert len(changes) == 1, changes
    assert "side-b/game.tap" in changes[0]


def test_every_tape_is_rendered_and_measured():
    r = qa_sweep.examine(str(FIXTURES / "clean_single.tap"))
    assert r["html_bytes"] > 0
    assert r["region_count"] >= 1
    assert r["check_results"]["CBM block checksums"] == tap2pdf.PASS


def test_a_renderer_crash_is_a_crash(monkeypatch):
    def boom(*a, **kw):
        raise ValueError("renderer exploded")

    monkeypatch.setattr(tap2pdf, "render_nfo", boom)
    r = qa_sweep.examine(str(FIXTURES / "clean_single.tap"))
    assert r["outcome"] == "crash"
    assert "renderer exploded" in r["error"]


def test_a_clean_verdict_beside_a_fail_is_a_contradiction(monkeypatch):
    monkeypatch.setattr(
        tap2pdf, "verdict",
        lambda *a, **kw: "The CBM portion of this tape reads cleanly.")
    r = qa_sweep.examine(str(FIXTURES / "bad_checksum.tap"))
    assert r["outcome"] == "contradiction"
    assert "CBM block checksums" in r["problems"][0]


def test_a_contradiction_fails_the_sweep(monkeypatch, capsys):
    rows = [qa_sweep.examine(str(FIXTURES / "clean_single.tap"))]
    rows[0].update(outcome="contradiction", problems=["x"])
    assert qa_sweep.report(rows, 10.0)


def test_an_oversized_or_shredded_dossier_is_flagged(capsys):
    row = qa_sweep.examine(str(FIXTURES / "clean_single.tap"))
    row.update(html_bytes=900 * 1024, region_count=4112)
    qa_sweep.report([row], 10.0, max_html_kb=400, max_regions=200)
    out = capsys.readouterr().out
    assert "over 400 KB" in out
    assert "4112" in out


def test_the_compare_names_the_check_that_moved():
    base = [{"id": "a.tap", "name": "a.tap", "outcome": "ok",
             "verdict": "v", "files": 1,
             "checks": {"PASS": 2, "FAIL": 0, "NOT CHECKED": 0},
             "check_results": {"Byte parity": "PASS", "Block structure": "PASS"}}]
    now = [dict(base[0], checks={"PASS": 1, "FAIL": 0, "NOT CHECKED": 1},
                check_results={"Byte parity": "PASS",
                               "Block structure": "NOT CHECKED"})]
    changes = qa_sweep.compare(base, now)
    assert any("Block structure" in c and "NOT CHECKED" in c
               for c in changes), changes
    assert not any("Byte parity" in c for c in changes)


def test_the_summary_counts_each_check_across_the_archive():
    rows = qa_sweep.sweep([str(FIXTURES / "clean_single.tap"),
                           str(FIXTURES / "bad_checksum.tap")])
    lines = qa_sweep.check_summary(rows)
    row = [l for l in lines if l.startswith("CBM block checksums")][0]
    assert row.split()[-3:] == ["1", "1", "0"]
