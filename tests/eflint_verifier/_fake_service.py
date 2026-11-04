"""An in-memory fake of the eFLINT model-checking webservice's job API (``/v1/jobs``)."""
from __future__ import annotations

import json

import httpx

PROCESS_PN = "place ready = 1\nplace active\nplace done\ntransition begin: ready -> active\ntransition finish: active -> done\n"
NORMS = 'Fact recorded Identified by String.\nEvent register Holds when True Creates recorded("case").\n'
RULES = "Sequence\n    begin, finish\nFluents\n    done = 1\nNormative Fluents\nQualify as\n    register.\n"
CHECKS = ('Property completion-recorded Where\n    Globally ((Tokens done = 1) Implies (Holds recorded("case"))).\n\n'
          "Property never-finish Where Globally (Not (Tokens done = 1)).\n")

NO_CE = {"results": [{"id": "Compliance", "bound": 30, "outcome": "no_counterexample_within_bound",
                      "completion": None, "traces": []}]}
CE = {"results": [{"id": "never-finish", "bound": 2, "outcome": "counterexample", "completion": None,
                   "traces": [{"steps": [{"step": 1, "actions": ["begin"]}, {"step": 2, "actions": ["finish"]}]}]}]}


class FakeService:
    """``result`` is returned for every job; ``fail`` makes jobs fail; ``reject`` makes submission 400;
    ``polls_until_done`` running polls precede the terminal status (``None`` = never finishes)."""

    def __init__(self, result=None, *, fail=None, reject=None, polls_until_done=1):
        self.result = result if result is not None else NO_CE
        self.fail, self.reject, self.polls_until_done = fail, reject, polls_until_done
        self.submitted: list[dict] = []
        self.calls: list[tuple[str, str]] = []
        self._polls: dict[str, int] = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        self.calls.append((method, path))
        if method == "POST" and path == "/v1/jobs":
            if self.reject:
                return httpx.Response(400, json={"error": {"code": "invalid_request", "message": self.reject}})
            self.submitted.append(json.loads(request.content))
            job = f"job-{len(self.submitted)}"
            self._polls[job] = 0
            return httpx.Response(202, json={"id": job, "status": "queued", "status_url": f"/v1/jobs/{job}"})
        job = path.split("/")[3]
        if job not in self._polls:
            return httpx.Response(404, json={"error": {"code": "not_found", "message": "unknown job"}})
        if method == "GET" and path.endswith("/result"):
            return httpx.Response(200, json=self.result)
        if method == "GET":
            self._polls[job] += 1
            done = self.polls_until_done is not None and self._polls[job] > self.polls_until_done
            status = ("failed" if self.fail else "succeeded") if done else "running"
            return httpx.Response(200, json={"id": job, "operation": "x", "status": status,
                                             "error": self.fail if done and self.fail else None})
        if method == "POST" and path.endswith("/cancel"):
            return httpx.Response(200, json={"id": job, "status": "cancelled"})
        if method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(405)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def make_handler(service: FakeService, tmp_path, **settings):
    from agents.eflint_verifier.agent import EflintVerifierHandler
    from agents.eflint_verifier.client import EflintVerifierClient
    from agents.eflint_verifier.config import VerifierSettings

    client = EflintVerifierClient(VerifierSettings(base_url="http://fake", **settings),
                                  transport=service.transport(), sleep=lambda _s: None)
    return EflintVerifierHandler(client=client, runs_dir=tmp_path / "verifier_runs")
