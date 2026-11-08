"""Web app tests (FastAPI TestClient). They run the real pipeline on the organisers' example log (~20 s per run).

    .venv/bin/python -m pytest tests -v

The example log is looked up in $LITHO_SAMPLE_DIR, data/, or ../../example/ (the hackathon folder layout).
"""
import importlib
import json
import os
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STEM = "0000000009_d-582_0000020435-0001_log06_fe_5in_md"


def find_sample():
    for d in [os.environ.get("LITHO_SAMPLE_DIR"), ROOT / "data", ROOT.parent.parent / "example"]:
        if d and (Path(d) / f"{STEM}.tif").is_file() and (Path(d) / f"{STEM}.json").is_file():
            d = Path(d)
            csv = next((p for p in (d / "example.csv", ROOT / "labels" / f"{STEM}.csv") if p.is_file()), None)
            return d / f"{STEM}.tif", d / f"{STEM}.json", csv
    return None


SAMPLE = find_sample()
needs_sample = pytest.mark.skipif(SAMPLE is None, reason="example log not found (set LITHO_SAMPLE_DIR)")


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["LITHO_RUNS_DIR"] = str(tmp_path_factory.mktemp("runs"))
    import webapp.app as appmod
    appmod = importlib.reload(appmod)
    from fastapi.testclient import TestClient
    with TestClient(appmod.app) as c:
        c.appmod = appmod
        yield c


def upload(client, rid, *paths, names=None):
    files = [("files", ((names or {}).get(p, Path(p).name), open(p, "rb"), "application/octet-stream")) for p in paths]
    try:
        r = client.post(f"/api/runs/{rid}/files", files=files)
    finally:
        for _, (_, fh, _) in files:
            fh.close()
    assert r.status_code == 200, r.text
    return r.json()


def wait_done(client, rid, timeout=600):
    t = time.time()
    while time.time() - t < timeout:
        st = client.get(f"/api/runs/{rid}/status").json()
        if st["meta"]["status"] not in ("running", "draft"):
            return st
        time.sleep(0.5)
    raise AssertionError("run did not finish in time")


def test_health_and_config(client):
    h = client.get("/api/health").json()
    assert h["ocr"]["lithology"]["ok"] and h["ocr"]["cv"]["ok"], h
    cfg = client.get("/api/config").json()
    names = {f["name"] for f in cfg["fields"]}
    assert {"method", "auto_scale", "tie_ratio", "k_range", "window_px"} <= names
    tie = next(f for f in cfg["fields"] if f["name"] == "tie_ratio")
    assert tie["type"] == "float" and tie["default"] == 0.1 and "tie" in tie["help"]
    assert client.get("/").status_code == 200


@needs_sample
def test_upload_preflight_run_matches_cli(client):
    tif, js, csv = SAMPLE
    rid = client.post("/api/runs", json={"name": "test example"}).json()["id"]
    res = upload(client, rid, tif, js, *( [csv] if csv else [] ), names={csv: "example.csv"} if csv else None)
    assert not res["rejected"]
    pf = client.get(f"/api/runs/{rid}/preflight").json()
    assert pf["n_ok"] == 1 and pf["rows"][0]["stem"] == STEM
    assert pf["rows"][0]["image"]["width"] > 0
    if csv:
        assert pf["rows"][0]["labels"]["ok"]
    # bad config is rejected before anything runs
    assert client.post(f"/api/runs/{rid}/start", json={"config": {"nope": 1}}).status_code == 400
    assert client.post(f"/api/runs/{rid}/start", json={"config": {"tie_ratio": "abc"}}).status_code == 400
    r = client.post(f"/api/runs/{rid}/start", json={"config": {"method": "cv"}})   # the 99.5% regression
    assert r.status_code == 200, r.text
    # one job at a time
    other = client.post("/api/runs", json={}).json()["id"]
    upload(client, other, js)
    assert client.post(f"/api/runs/{other}/start", json={}).status_code in (400, 409)
    st = wait_done(client, rid)
    assert st["meta"]["status"] == "done", st["meta"]
    assert st["progress"]["finished"] == 1 and st["progress"]["fraction"] == 1.0
    s = client.get(f"/api/runs/{rid}/summary").json()
    assert not s["partial"] and s["n_errors"] == 0
    rec = s["logs"][0]
    assert rec["n_intervals"] == 17
    if csv:
        # same numbers as the CLI on the same log (expected 99.53% / 1.31 px)
        cli = ROOT / "outputs_test" / "summary.json"
        if cli.is_file():
            ref = json.loads(cli.read_text())["logs"][0]["metrics"]
            assert rec["metrics"]["row_acc"] == ref["row_acc"]
            assert rec["metrics"]["boundary_mean_px"] == ref["boundary_mean_px"]
        else:
            assert abs(rec["metrics"]["row_acc"] - 99.53) < 0.2
        assert s["mean_metrics"]["row_acc"] == rec["metrics"]["row_acc"]
    # SSE replays the events and ends
    with client.stream("GET", f"/api/runs/{rid}/events") as ev:
        text = "".join(ev.iter_text())
    assert '"event": "log_done"' in text and "event: end" in text
    # outputs, tiles, per-log detail, zip
    d = client.get(f"/api/runs/{rid}/logs/{STEM}").json()
    assert d["record"]["stem"] == STEM and d["boxes"]["interpreted_lithology_column"]
    assert client.get(f"/api/runs/{rid}/files/outputs/{STEM}.csv").status_code == 200
    m = client.get(f"/api/runs/{rid}/tiles/{STEM}_column.png").json()
    assert m["n"] >= 1 and client.get(f"/api/runs/{rid}/files/{m['tiles'][0]}").status_code == 200
    z = client.get(f"/api/runs/{rid}/download?what=csv")
    assert z.status_code == 200 and z.content[:2] == b"PK"
    runs = client.get("/api/runs").json()["runs"]
    assert any(r["id"] == rid and r["mean_metrics"] for r in runs) or not csv
    client.delete(f"/api/runs/{other}")


def test_broken_json_reported(client, tmp_path):
    rid = client.post("/api/runs", json={}).json()["id"]
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json")
    nobox = tmp_path / "nobox.json"
    nobox.write_text(json.dumps({"image_name": "nobox.tif"}))
    pf = upload(client, rid, bad, nobox)["preflight"]
    rows = {r["stem"]: r for r in pf["rows"]}
    assert not rows["broken"]["ok"] and any("invalid JSON" in e for e in rows["broken"]["errors"])
    assert not rows["nobox"]["ok"] and any("bounding_boxes" in e for e in rows["nobox"]["errors"])
    assert pf["n_ok"] == 0
    assert client.post(f"/api/runs/{rid}/start", json={}).status_code == 400
    other = tmp_path / "Thumbs.db"
    other.write_bytes(b"x")
    assert upload(client, rid, other)["rejected"]


@needs_sample
def test_unlabelled_log_has_no_accuracy(client, tmp_path):
    tif, js, _ = SAMPLE
    folder = tmp_path / "logs"
    folder.mkdir()
    # another name, so the example's ground truth (picked up automatically from labels/) does not apply
    (folder / "unlabelled_copy.tif").symlink_to(tif)
    ann = json.loads(js.read_text())
    ann["image_name"] = "unlabelled_copy.tif"
    (folder / "unlabelled_copy.json").write_text(json.dumps(ann))
    rid = client.post("/api/runs", json={}).json()["id"]
    r = client.post(f"/api/runs/{rid}/link", json={"path": str(folder)})
    assert r.status_code == 200 and r.json()["linked"] == 1
    assert r.json()["preflight"]["rows"][0]["labels"] is None
    # settings are applied: a smaller preview scale shows up in the summary config
    assert client.post(f"/api/runs/{rid}/start", json={"config": {"column_scale": 0.3}}).status_code == 200
    st = wait_done(client, rid)
    assert st["meta"]["status"] == "done"
    s = client.get(f"/api/runs/{rid}/summary").json()
    assert s["mean_metrics"] is None and "metrics" not in s["logs"][0]
    assert s["config"]["column_scale"] == 0.3
    assert tif.is_file() and js.is_file()                       # linked originals untouched
    clone = client.post(f"/api/runs/{rid}/clone", json={}).json()
    assert clone["status"] == "draft" and clone["config_overrides"] == {"column_scale": 0.3}
    assert client.get(f"/api/runs/{clone['id']}/preflight").json()["n_ok"] == 1
    assert client.delete(f"/api/runs/{clone['id']}").status_code == 200
    assert tif.is_file() and (folder / "unlabelled_copy.tif").exists()


@needs_sample
def test_cancel(client):
    tif, js, _ = SAMPLE
    rid = client.post("/api/runs", json={}).json()["id"]
    upload(client, rid, tif, js)
    assert client.post(f"/api/runs/{rid}/start", json={}).status_code == 200
    time.sleep(2)
    assert client.post(f"/api/runs/{rid}/cancel").status_code == 200
    st = wait_done(client, rid, timeout=30)
    assert st["meta"]["status"] == "cancelled"


def test_path_traversal_rejected(client, tmp_path):
    rid = client.post("/api/runs", json={}).json()["id"]
    for bad in ("outputs/../meta.json", "outputs/%2e%2e/meta.json", "../meta.json", "meta.json", "data/x.json",
                "outputs/..%2F..%2Fmeta.json"):
        r = client.get(f"/api/runs/{rid}/files/{bad}")
        assert r.status_code in (400, 404), (bad, r.status_code)
    assert client.get("/api/runs/..%2F..%2Fetc/status").status_code == 404
    evil = tmp_path / "evil.json"
    evil.write_text("{}")
    for name in ("../evil.json", "..\\evil.json", ".hidden.json", "a/b.json"):
        res = upload(client, rid, evil, names={evil: name})
        assert res["rejected"] and not res["saved"], name
    assert not (Path(os.environ["LITHO_RUNS_DIR"]) / "evil.json").exists()
    assert client.get(f"/api/runs/{rid}/tiles/..%2Fmeta.json").status_code in (400, 404)
    assert client.post(f"/api/runs/{rid}/link", json={"path": "relative/path"}).status_code == 400
