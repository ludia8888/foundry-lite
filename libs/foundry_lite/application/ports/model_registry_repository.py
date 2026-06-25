"""Model registry port (AIP-lite P0b — governed Model Catalog + alias table, §10.1).

Mirrors Palantir's **Model Catalog**: per-model metadata of a model TYPE
(completion|embedding|vision), a LIFECYCLE (experimental|stable|sunset|deprecated), the
provider-native model id, context/output token limits, and regional availability
(palantir.com/docs/foundry/model-catalog/overview). The alias table is our EXTENSION of the
documented SDK ``ModelInput(alias, model_version)`` indirection — it maps a stable alias to a model
id plus an OPTIONAL pinned ``version`` (omit → floating latest; set → pinned, which docs RECOMMEND
for production), with an alias ``status`` lifecycle (draft/enabled/deprecated/disabled, §8.2).

Egress governance lives on the provider + model rows (§9.3): the provider carries the
``retention_policy``/``training_policy`` (ZDR / no-retrain egress assurance), ``region``
(georestriction), and ``secret_ref`` (the secret NAME the SecretProvider resolves by — never a
value); the model carries ``allowed_classifications`` (export-control marking the destination
accepts). The gateway gates a request's data_classification/region_requirement against these BEFORE
any provider call.

Reads are batch by convention (N+1 prevention): ``get_models``/``get_aliases``/``get_providers``
accept lists.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from foundry_lite.application.ports.transaction_context import TransactionContext

ModelLifecycle = Literal["experimental", "stable", "sunset", "deprecated"]
AliasStatus = Literal["draft", "enabled", "deprecated", "disabled"]


@dataclass(frozen=True)
class ModelProviderRecord:
    """A provider the platform can route to (§10.1 ``ai_model_providers``)."""

    provider_id: str
    tenant_scope: str
    provider_type: str
    profile_name: str
    region: str
    # Credentials are referenced by NAME only; the raw value never lives in the DB or trace.
    secret_ref: str
    # ZDR / no-retrain egress assurance the destination promises (§9.3).
    retention_policy: str
    training_policy: str
    status: str
    created_at: str


@dataclass(frozen=True)
class ModelRecord:
    """One Catalog model (§10.1 ``ai_models``): provider-native id, lifecycle, limits, egress facts.

    The resolved provider comes from the joined provider row's ``provider_type``/``profile_name`` —
    it is NOT duplicated here.
    """

    model_id: str
    tenant_scope: str
    provider_id: str
    provider_model_id: str
    revision: str
    lifecycle: ModelLifecycle
    capabilities_json: dict[str, object]
    context_limit: int
    output_limit: int
    pricing_json: dict[str, object]
    allowed_classifications: tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class ModelAliasRecord:
    """Stable alias → model_id with an optional pinned ``version`` (§10.1 ``ai_model_aliases``).

    ``version`` IS the pinned model revision (None → floating latest). ``status`` is the alias
    lifecycle; only an ``enabled`` alias may serve (§8.2).
    """

    alias: str
    tenant_scope: str
    environment: str
    model_id: str
    version: str | None
    status: AliasStatus
    eval_run_id: str | None
    effective_at: str | None
    retired_at: str | None


class ModelRegistryRepository(Protocol):
    """DB boundary for the model catalog, providers, and alias indirection."""

    def create_provider(self, *, transaction: TransactionContext, record: ModelProviderRecord) -> None:
        """Persist one provider row."""
        ...

    def create_model(self, *, transaction: TransactionContext, record: ModelRecord) -> None:
        """Persist one catalog model row."""
        ...

    def create_alias(self, *, transaction: TransactionContext, record: ModelAliasRecord) -> None:
        """Persist one alias → model row."""
        ...

    def get_aliases(
        self, *, transaction: TransactionContext, tenant_id: str, aliases: list[str]
    ) -> list[ModelAliasRecord]:
        """Resolve aliases for one tenant (batch; N+1 prevention)."""
        ...

    def get_models(self, *, transaction: TransactionContext, tenant_id: str, model_ids: list[str]) -> list[ModelRecord]:
        """Resolve catalog models for one tenant (batch; N+1 prevention)."""
        ...

    def get_providers(
        self, *, transaction: TransactionContext, tenant_id: str, provider_ids: list[str]
    ) -> list[ModelProviderRecord]:
        """Resolve providers for one tenant (batch; N+1 prevention)."""
        ...
