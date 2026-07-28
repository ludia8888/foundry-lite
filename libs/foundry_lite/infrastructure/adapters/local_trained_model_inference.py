"""Local deterministic trained-model adapter for governed Pipeline Builder QA."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelInferenceResult,
    TrainedModelInvocation,
)
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    require_trained_model_invocation_pin,
)
from foundry_lite.domain.errors import NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters.trained_model_definitions import (
    TRANSACTION_RISK_DEFINITION,
)

_TRANSACTION_RISK = TRANSACTION_RISK_DEFINITION


class LocalTrainedModelInferenceAdapter:
    """Execute registered local models through the same typed production port."""

    def list_models(self) -> Sequence[TrainedModelDefinition]:
        return (_TRANSACTION_RISK,)

    def resolve(
        self,
        model_ref: str,
        *,
        branch: str,
        fallback_branches: Sequence[str] = (),
    ) -> TrainedModelDefinition:
        branches = (branch, *fallback_branches)
        if model_ref == _TRANSACTION_RISK.model_ref and _TRANSACTION_RISK.branch in branches:
            return _TRANSACTION_RISK
        raise NotFound(
            "trained model was not found on the selected branch or fallbacks",
            details={"modelRef": model_ref, "branches": list(branches)},
        )

    def infer(self, invocation: TrainedModelInvocation) -> TrainedModelInferenceResult:
        current_definition = self.resolve(
            invocation.model_ref,
            branch=invocation.branch,
            fallback_branches=invocation.fallback_branches,
        )
        definition = invocation.pinned_definition or current_definition
        require_trained_model_invocation_pin(invocation, definition)
        rows = tuple(_score_transaction(row) for row in invocation.rows)
        return TrainedModelInferenceResult(
            definition=definition,
            rows=rows,
            runtime_evidence={
                "executionMode": "batch",
                "runtime": "local_trained_model_adapter",
                "warmPoolEnabled": False,
                "inputRowCount": len(invocation.rows),
                "outputRowCount": len(rows),
            },
        )


def _score_transaction(row: Mapping[str, object]) -> Mapping[str, object]:
    raw_amount = row.get("amount")
    if not isinstance(raw_amount, int | float) or isinstance(raw_amount, bool):
        raise ValidationFailed(
            "trained model input amount must be numeric",
            details={"field": "amount", "actualType": type(raw_amount).__name__},
        )
    country = str(row.get("country") or "").upper()
    amount_score = min(float(raw_amount) / 20_000.0, 0.8)
    country_score = 0.15 if country in {"IR", "KP", "SY"} else 0.0
    media_score = 0.05 if isinstance(row.get("document"), Mapping) else 0.0
    score = round(min(amount_score + country_score + media_score, 0.99), 4)
    return {"riskScore": score, "decision": "review" if score >= 0.7 else "allow"}
