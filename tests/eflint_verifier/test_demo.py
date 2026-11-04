"""The eflint_verifier demo app — against the fake service (offline)."""
import json
import re
import shutil
import subprocess

import pytest

from ._deps import require_deps

require_deps()

from fastapi.testclient import TestClient  # noqa: E402

import agents.eflint_verifier.demo.main as demo  # noqa: E402

from ._fake_service import CE, CHECKS, NORMS, PROCESS_PN, RULES, FakeService, make_handler  # noqa: E402

PAYLOAD = {"physical": {"language": "petri-net", "source": PROCESS_PN},
           "normative": {"language": "eflint", "source": NORMS}, "qualification": RULES}


@pytest.fixture
def client(tmp_path, monkeypatch):
    def _make(service=None):
        handler = make_handler(service or FakeService(), tmp_path)
        monkeypatch.setattr(demo, "_handler", lambda: handler)
        return TestClient(demo.app)
    return _make


def test_verify_records_a_run_that_can_be_listed_and_reopened(client):
    c = client(FakeService(CE))
    res = c.post("/api/verify", json={**PAYLOAD, "analysis": CHECKS, "options": {"max_states": 2}}).json()
    assert res["ok"] and res["output"]["verdict"] == "failed" and res["output"]["operation"] == "model-check"
    assert res["output"]["properties"][0]["witness"] == [["begin"], ["finish"]]

    (row,) = c.get("/api/runs").json()["runs"]
    assert row["run_id"] == res["run_id"] and row["verdict"] == "failed" and row["properties"] == 1

    run = c.get(f"/api/runs/{res['run_id']}").json()
    assert run["report"]["verdict"] == "failed"
    assert run["request"]["inputs"]["analysis"] == CHECKS and run["request"]["options"]["max_states"] == 2


def test_failures_are_reported_and_recorded(client):
    c = client(FakeService(reject="bad qualification"))
    res = c.post("/api/verify", json=PAYLOAD).json()
    assert not res["ok"] and "bad qualification" in res["error"]
    (row,) = c.get("/api/runs").json()["runs"]
    assert row["verdict"] == "error" and "bad qualification" in row["summary"]


def test_invalid_payload_is_a_failed_result_not_a_crash(client):
    res = client().post("/api/verify", json={"physical": {"language": "petri-net", "source": PROCESS_PN},
                                             "qualification": RULES}).json()  # rules without norms
    assert not res["ok"] and "normative and qualification" in res["error"]


def test_bad_run_ids(client):
    c = client()
    assert c.get("/api/runs/nope").status_code == 404
    assert c.get("/api/runs/..%2Fsecret").status_code == 404


def test_sources_from_other_agents_storage(tmp_path, monkeypatch, client):
    nets = tmp_path / "nets"
    (nets / "abc123").mkdir(parents=True)
    (nets / "abc123" / "net.pnml").write_text("<pnml/>")
    (nets / "abc123" / "response.json").write_text(json.dumps({"process_name": "Permit process"}))
    monkeypatch.setattr(demo, "_nets_dir", lambda: nets)

    class Specs:
        def list_runs(self):
            return [{"run_id": "r1", "title": "Awb", "status": "succeeded", "created_at": "2026-11-05"}]

        def load_run(self, run_id):
            if run_id != "r1":
                raise KeyError(run_id)
            return {"spec": {"title": "Awb"}, "files": {"spec.eflint": "Fact x.", "Norms.eflint": "…"}}

    monkeypatch.setattr(demo, "_eflint_store", lambda: Specs())
    c = client()
    assert c.get("/api/sources/nets").json()["nets"][0] == {"ref": "abc123", "name": "Permit process",
                                                           "modified": pytest.approx(0, abs=1e12)}
    assert c.get("/api/sources/nets/abc123").json()["source"] == "<pnml/>"
    assert c.get("/api/sources/nets/missing").status_code == 404
    assert c.get("/api/sources/nets/..%2F..").status_code in (400, 404)
    assert c.get("/api/sources/eflint").json()["specs"][0]["run_id"] == "r1"
    assert c.get("/api/sources/eflint/r1").json()["source"] == "Fact x."
    assert c.get("/api/sources/eflint/zz").status_code == 404


def test_sample_runs_through_the_agent(client):
    sample = client().get("/api/sample").json()
    payload = {"physical": {"language": "petri-net", "source": sample["physical"]},
               "normative": {"language": "eflint", "source": sample["normative"]},
               "qualification": sample["qualification"], "analysis": sample["analysis"],
               "options": {"max_states": sample["max_states"]}}
    service = FakeService(CE)
    res = client(service).post("/api/verify", json=payload).json()
    assert res["ok"], res["error"]
    assert service.submitted[0]["operation"] == "model-check"


def test_health_reports_unreachable_service(client, monkeypatch):
    monkeypatch.setenv("EFLINT_VERIFIER_URL", "http://127.0.0.1:9")  # nothing listens on port 9
    h = client().get("/api/health").json()
    assert h["status"] == "ok" and h["service_reachable"] is False and h["service_url"].endswith(":9")


def test_index_page_script_is_valid():
    html = (demo.STATIC_DIR / "index.html").read_text()
    for el in ("physical", "normative", "qualification", "analysis", "runs", "netSel", "specSel"):
        assert f'id="{el}"' in html
    node = shutil.which("node")
    if node:
        script = re.search(r"<script>(.*)</script>", html, re.S).group(1)
        proc = subprocess.run([node, "--check", "-"], input=script, text=True, capture_output=True)
        assert proc.returncode == 0, proc.stderr
