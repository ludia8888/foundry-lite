from __future__ import annotations

import json
from argparse import Namespace

from scripts.operations import run_macmini_external_oidc_fault as subject


def test_fault_requires_pre_outage_rejection_and_post_recovery(monkeypatch, tmp_path) -> None:
    args = Namespace(run_id="run-1", namespace="foundry-qa", kubeconfig="k", kubectl="kubectl", duration_seconds=15)
    verifications = iter(({"status": "passed", "step": "pre"}, {"status": "passed", "step": "post"}))
    process = object()
    monkeypatch.setattr(subject, "QA_ROOT", tmp_path)
    monkeypatch.setattr(subject, "assert_host_boundary", lambda: None)
    monkeypatch.setattr(subject, "assert_namespace", lambda _namespace: None)
    monkeypatch.setattr(subject.verify_macmini_external_oidc, "verify", lambda _args: next(verifications))
    monkeypatch.setattr(subject, "_start_fault", lambda _args: process)
    monkeypatch.setattr(subject, "_wait_for_scale_down", lambda _args: True)
    monkeypatch.setattr(
        subject,
        "_verify_outage",
        lambda _args: {"status": "passed", "rejected": True, "rawTokensStored": False},
    )
    monkeypatch.setattr(subject, "_finish_fault", lambda _process, _timeout: {"status": "passed"})
    monkeypatch.setattr(
        subject,
        "write_json_receipt",
        lambda path, payload: path.write_text(json.dumps(payload), encoding="utf-8"),
    )

    receipt = subject.run(args)

    assert receipt["status"] == "passed"
    assert receipt["scaleDownObserved"] is True
    assert receipt["outageVerification"]["rejected"] is True
    assert receipt["postFaultVerification"]["step"] == "post"


def test_outage_verification_fails_if_cached_or_local_auth_still_accepts(monkeypatch) -> None:
    monkeypatch.setattr(subject.verify_macmini_external_oidc, "verify", lambda _args: {"status": "passed"})

    receipt = subject._verify_outage(Namespace())

    assert receipt["status"] == "failed"
    assert receipt["rejected"] is False


def test_finish_fault_accepts_only_zero_exit_passed_receipt() -> None:
    process = _Process(0, b'noise\n{"status":"passed","recoveryObserved":true}\n')

    receipt = subject._finish_fault(process, 10)

    assert receipt["status"] == "passed"
    assert receipt["recoveryObserved"] is True
    assert receipt["rawOutputStored"] is False


class _Process:
    def __init__(self, returncode: int, stdout: bytes) -> None:
        self.returncode = returncode
        self._stdout = stdout

    def communicate(self, timeout: int) -> tuple[bytes, bytes]:
        assert timeout == 10
        return self._stdout, b""

    def terminate(self) -> None:
        raise AssertionError("terminate should not be called")
