from __future__ import annotations

import json
from pathlib import Path

import pytest
from foundry_lite.infrastructure.runners import trained_model_runner as runner


def test_trained_model_runner_scores_rows_and_writes_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "modelRef": "demo.transaction-risk",
                "rows": [
                    {"amount": 1_000, "country": "US"},
                    {"amount": 20_000, "country": "IR", "document": {"mediaItemRid": "ri.media.1"}},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_runtime_evidence", lambda count: {"inputRowCount": count})
    monkeypatch.setattr(runner.sys, "argv", ["runner", str(request_path), str(result_path)])

    assert runner.main() == 0
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["rows"] == [
        {"decision": "allow", "riskScore": 0.05},
        {"decision": "review", "riskScore": 0.99},
    ]
    assert result["runtimeEvidence"] == {"inputRowCount": 2}


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "invalid trained-model request contract"),
        ({"schemaVersion": 2}, "invalid trained-model request contract"),
        (
            {"schemaVersion": 1, "modelRef": "other", "rows": []},
            "modelRef is not served",
        ),
        (
            {"schemaVersion": 1, "modelRef": "demo.transaction-risk", "rows": "bad"},
            "rows must be objects",
        ),
        (
            {"schemaVersion": 1, "modelRef": "demo.transaction-risk", "rows": [1]},
            "rows must be objects",
        ),
    ],
)
def test_trained_model_runner_validates_request_contract(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        runner._read_request(request_path)


@pytest.mark.parametrize("amount", [True, "100", None])
def test_trained_model_runner_rejects_non_numeric_amount(amount: object) -> None:
    with pytest.raises(ValueError, match="amount must be numeric"):
        runner._score_transaction({"amount": amount})


def test_trained_model_runner_writes_redacted_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_path = tmp_path / "request.json"
    result_path = tmp_path / "result.json"
    request_path.write_text("not-json-sensitive-value", encoding="utf-8")
    monkeypatch.setattr(runner.sys, "argv", ["runner", str(request_path), str(result_path)])

    assert runner.main() == 2
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    assert result["failure"]["type"] == "model_execution_error"
    assert "not-json-sensitive-value" not in result_path.read_text(encoding="utf-8")


def test_trained_model_runtime_evidence_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Connection:
        def __enter__(self) -> None:
            return None

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(runner.socket, "create_connection", lambda *_args, **_kwargs: _Connection())
    assert runner._network_blocked() is False

    def network_failure(*_args: object, **_kwargs: object) -> None:
        raise OSError("blocked")

    monkeypatch.setattr(runner.socket, "create_connection", network_failure)
    assert runner._network_blocked() is True

    original_write = Path.write_text

    def blocked_write(path: Path, *_args: object, **_kwargs: object) -> int:
        if str(path).startswith(("/trained-model-root-write", "/model-output/")):
            raise OSError("blocked")
        return original_write(path, *_args, **_kwargs)

    monkeypatch.setattr(Path, "write_text", blocked_write)
    assert runner._root_write_blocked() is True
    assert runner._output_directory_write_blocked() is True

    status = tmp_path / "status"
    status.write_text("CapEff:\t0000\nNoNewPrivs:\t1\n", encoding="utf-8")
    monkeypatch.setattr(runner.Path, "__new__", lambda cls, *_args: status)
    assert runner._status_value("CapEff") == "0000"
    assert runner._status_value("Missing") == ""


def test_trained_model_runtime_evidence_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runner, "_network_blocked", lambda: True)
    monkeypatch.setattr(runner, "_root_write_blocked", lambda: True)
    monkeypatch.setattr(runner, "_output_directory_write_blocked", lambda: True)
    monkeypatch.setattr(runner, "_status_value", lambda name: {"CapEff": "0", "NoNewPrivs": "1"}[name])

    evidence = runner._runtime_evidence(3)

    assert evidence["networkBlocked"] is True
    assert evidence["effectiveCapabilities"] == "0"
    assert evidence["noNewPrivileges"] == "1"
    assert evidence["inputRowCount"] == evidence["outputRowCount"] == 3
