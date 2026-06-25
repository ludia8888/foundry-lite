"""Governed Model Gateway (AIP-lite P0b, §8.1/§11.6).

One boundary for all LLM access. ``invoke``:
  1. **Resolves** the request's stable alias → (provider, model, revision) via the registry — pinned
     to the alias's ``version`` if set, else the model's ``revision``. The resolved provider comes
     from the joined provider row (``provider_type``/``profile_name``). (Alias→provider/model/revision
     is OUR extension of the documented SDK ``ModelInput.alias`` indirection, NOT a copy of a proxy
     feature — the proxy binds to a concrete RID.)
  2. **No silent fallback** (OUR invariant + §8.2 alias lifecycle): a resolved model that is
     ``sunset``/``deprecated`` fails typed, AND an alias whose ``status`` is not ``enabled``
     (draft/deprecated/disabled) fails typed — the gateway never swaps in a different model/alias.
  3. **Egress-gates** BEFORE any provider call (§9.3): a request whose ``data_classification`` is not
     in the resolved model's ``allowed_classifications`` (export-control marking), or whose
     ``region_requirement`` disagrees with the provider's pinned region (georestriction), is DENIED
     with a typed ``AdapterError`` carrying ``details.reason="egress_denied"`` (§11.6 — model-level
     egress denial is ``egress_denied``, NOT the tool-level ``policy_denied``). The adapter is never
     called.
  4. Calls the wired ``LanguageModelAdapter`` (credentials referenced by NAME/version via the
     adapter's SecretProvider — the raw key never enters app logic, the response, or any trace).
  5. Returns the ``ModelResponse`` with provider/resolved id+revision and per-call ``model_hash`` /
     ``prompt_hash`` filled (OUR invariant — hashes available for the P0c ledger; raw prompt is never
     logged here).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureKind
from foundry_lite.application.ports.language_model import ModelRequest, ModelResponse
from foundry_lite.application.ports.model_registry_repository import (
    ModelAliasRecord,
    ModelProviderRecord,
    ModelRecord,
)
from foundry_lite.application.services.base import CoreService
from foundry_lite.domain.context import RequestContext

_GATEWAY_PROFILE = "model-gateway"
_NON_SERVING_LIFECYCLES = frozenset({"sunset", "deprecated"})
_SERVING_ALIAS_STATUS = "enabled"


class ModelGatewayService(CoreService):
    """Single governed seam: resolve alias, gate egress, forbid silent fallback, invoke, hash."""

    required_dependencies = ("engine", "model_registry_repository", "language_model_adapter")
    required_collaborators = ()

    def invoke(self, ctx: RequestContext, request: ModelRequest) -> ModelResponse:
        alias, model, provider = self._resolve(ctx, request.model_alias)
        revision = alias.version or model.revision
        self._guard_serving(alias, model)
        self._guard_egress(request, model, provider)
        response = self.language_model_adapter.complete(request)
        return replace(
            response,
            provider=provider.provider_type,
            resolved_model_id=model.model_id,
            resolved_model_revision=revision,
            model_hash=_model_hash(model.model_id, revision),
            prompt_hash=_prompt_hash(request),
        )

    def _resolve(
        self, ctx: RequestContext, model_alias: str
    ) -> tuple[ModelAliasRecord, ModelRecord, ModelProviderRecord]:
        with self.engine.begin() as conn:
            aliases = self.model_registry_repository.get_aliases(
                transaction=conn, tenant_id=ctx.tenant_id, aliases=[model_alias]
            )
            if not aliases:
                raise self._error("not_found", f"unknown model alias: {model_alias}", model_alias)
            alias = aliases[0]
            models = self.model_registry_repository.get_models(
                transaction=conn, tenant_id=ctx.tenant_id, model_ids=[alias.model_id]
            )
            if not models:
                raise self._error("not_found", f"alias {model_alias} resolves to a missing model", model_alias)
            model = models[0]
            providers = self.model_registry_repository.get_providers(
                transaction=conn, tenant_id=ctx.tenant_id, provider_ids=[model.provider_id]
            )
        if not providers:
            raise self._error("not_found", f"model {model.model_id} resolves to a missing provider", model.model_id)
        return alias, model, providers[0]

    def _guard_serving(self, alias: ModelAliasRecord, model: ModelRecord) -> None:
        # OUR invariant + §8.2: never silently route to a different model/alias — a non-serving
        # model OR a non-enabled alias fails typed (no swap to another model).
        if model.lifecycle in _NON_SERVING_LIFECYCLES:
            raise self._error(
                "unavailable",
                f"model {model.model_id} is {model.lifecycle}; no silent fallback is permitted",
                model.model_id,
            )
        if alias.status != _SERVING_ALIAS_STATUS:
            raise self._error(
                "unavailable",
                f"alias {alias.alias} is {alias.status}; only an enabled alias may serve",
                alias.alias,
            )

    def _guard_egress(self, request: ModelRequest, model: ModelRecord, provider: ModelProviderRecord) -> None:
        # Export-control marking: classification must be exportable to the destination (§9.3).
        if request.data_classification and request.data_classification not in model.allowed_classifications:
            raise self._error(
                "authorization",
                f"classification {request.data_classification!r} is not exportable to {provider.provider_type}",
                model.model_id,
            )
        # Georestriction: a pinned region requirement must match the provider's region (§9.3).
        if request.region_requirement is not None and request.region_requirement != provider.region:
            raise self._error(
                "authorization",
                f"region {request.region_requirement!r} is not permitted for {provider.provider_type}",
                model.model_id,
            )

    def _error(self, kind: AdapterFailureKind, message: str, resource: str) -> AdapterError:
        reason = "egress_denied" if kind == "authorization" else "language_model_unavailable"
        return AdapterError(
            AdapterFailure(
                _GATEWAY_PROFILE,
                "invoke",
                kind,
                is_retryable=False,
                operator_message=message,
                details={"reason": reason, "resource": resource},
            )
        )


def _model_hash(model_id: str, revision: str) -> str:
    return hashlib.sha256(f"{model_id}@{revision}".encode()).hexdigest()


def _prompt_hash(request: ModelRequest) -> str:
    joined = "\n".join(f"{message.role}:{message.content}" for message in request.messages)
    return hashlib.sha256(joined.encode()).hexdigest()
