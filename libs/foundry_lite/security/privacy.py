from __future__ import annotations

import hashlib
import hmac
import json
import re
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, Literal, Protocol, cast

PrivacyTransformMode = Literal["passthrough", "pseudonymize", "anonymize", "redact_text"]

ANONYMIZED_VALUE: Final = "***ANONYMIZED***"
REDACTED_PII_VALUE: Final = "***REDACTED_PII***"
PRIVACY_TRANSFORM_MODES: Final = frozenset({"passthrough", "pseudonymize", "anonymize", "redact_text"})
PRIVACY_PROTECTING_MODES: Final = frozenset({"pseudonymize", "anonymize", "redact_text"})

_EMAIL_RE: Final = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_SSN_RE: Final = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_PHONE_RE: Final = re.compile(r"\b(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b")
_OPENLINEAGE_SCHEMA_URL: Final = "https://openlineage.io/spec/1-0-5/OpenLineage.json#/$defs/RunEvent"
_OPENLINEAGE_PRODUCER: Final = "https://github.com/ludia8888/foundry-lite/libs/foundry_lite/security/privacy.py"
_PRIVACY_OPENLINEAGE_RUN_NAMESPACE: Final = uuid.UUID("14ea8b78-5d65-44c2-b9f7-7ad4ec5c6fb4")


@dataclass(frozen=True)
class PrivacyFieldRule:
    field_name: str
    mode: PrivacyTransformMode
    scope: str = "tenant"
    is_reversible: bool = False

    def __post_init__(self) -> None:
        if self.mode not in PRIVACY_TRANSFORM_MODES:
            raise ValueError(f"unsupported privacy transform mode: {self.mode}")


@dataclass(frozen=True)
class PrivacyMappingRecord:
    tenant_id: str
    plan_version: str
    field_name: str
    scope: str
    pseudonym: str
    original_value: object

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (self.tenant_id, self.plan_version, self.field_name, self.scope, self.pseudonym)

    def redacted(self) -> dict[str, object]:
        return {
            "tenantId": self.tenant_id,
            "planVersion": self.plan_version,
            "fieldName": self.field_name,
            "scope": self.scope,
            "pseudonym": self.pseudonym,
            "originalValue": "***PROTECTED***",
            "originalValueHash": _value_hash(self.original_value),
        }


class PrivacyMappingStore(Protocol):
    def store_mapping(self, record: PrivacyMappingRecord) -> None: ...


class InMemoryProtectedPrivacyMappingStore:
    def __init__(self) -> None:
        self._records: dict[tuple[str, str, str, str, str], PrivacyMappingRecord] = {}

    def store_mapping(self, record: PrivacyMappingRecord) -> None:
        self._records[record.key] = record

    def get_original_value(
        self,
        *,
        tenant_id: str,
        plan_version: str,
        field_name: str,
        scope: str,
        pseudonym: str,
    ) -> object | None:
        record = self._records.get((tenant_id, plan_version, field_name, scope, pseudonym))
        return None if record is None else record.original_value

    def redacted_evidence(self) -> tuple[dict[str, object], ...]:
        return tuple(self._records[key].redacted() for key in sorted(self._records))


@dataclass(frozen=True)
class PrivacyReplicationPolicy:
    source_environment: str
    target_environment: str
    sensitive_fields: tuple[str, ...]
    allowed_target_environments: tuple[str, ...] = ("staging", "analytics", "ai_experiment", "development", "test")
    production_environment: str = "production"


@dataclass(frozen=True)
class PrivacyReplicationDecision:
    is_allowed: bool
    reason: str
    unprotected_fields: tuple[str, ...]
    lineage: Mapping[str, object]


@dataclass(frozen=True)
class PrivacyDatasetRef:
    namespace: str
    name: str
    version_id: str
    environment: str

    def lineage_payload(self) -> dict[str, object]:
        return {
            "namespace": self.namespace,
            "name": self.name,
            "versionId": self.version_id,
            "environment": self.environment,
        }


@dataclass(frozen=True)
class PrivacyTransformPlan:
    plan_name: str
    salt: str
    rules: tuple[PrivacyFieldRule, ...]

    @property
    def version(self) -> str:
        salt_fingerprint = hashlib.sha256(self.salt.encode("utf-8")).hexdigest()[:12]
        payload = {
            "planName": self.plan_name,
            "rules": [
                {
                    "fieldName": rule.field_name,
                    "isReversible": rule.is_reversible,
                    "mode": rule.mode,
                    "scope": rule.scope,
                }
                for rule in self.rules
            ],
            "saltFingerprint": salt_fingerprint,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return "privacy:v1:" + hashlib.sha256(encoded).hexdigest()[:16]


@dataclass(frozen=True)
class PrivacyTransformResult:
    rows: tuple[dict[str, object], ...]
    plan_version: str
    lineage: Mapping[str, object]


def transform_privacy_rows(
    rows: Sequence[Mapping[str, object]],
    *,
    tenant_id: str,
    plan: PrivacyTransformPlan,
    mapping_store: PrivacyMappingStore | None = None,
    replication_policy: PrivacyReplicationPolicy | None = None,
    source_dataset: PrivacyDatasetRef | None = None,
    target_dataset: PrivacyDatasetRef | None = None,
) -> PrivacyTransformResult:
    _validate_dataset_lineage_refs(source_dataset=source_dataset, target_dataset=target_dataset)
    replication_decision = _require_allowed_replication(replication_policy=replication_policy, plan=plan)
    rules = {rule.field_name: rule for rule in plan.rules}
    transformed = tuple(
        _transform_row(row, tenant_id=tenant_id, plan=plan, rules=rules, mapping_store=mapping_store) for row in rows
    )
    lineage: dict[str, object] = {
        "privacyTransform": {
            "planName": plan.plan_name,
            "planVersion": plan.version,
            "ruleCount": len(plan.rules),
            "rowCount": len(transformed),
        }
    }
    if replication_decision is not None:
        lineage["privacyReplicationPolicy"] = replication_decision.lineage["privacyReplicationPolicy"]
    if source_dataset is not None and target_dataset is not None:
        lineage["privacyDatasetLineage"] = _dataset_lineage(
            source_dataset=source_dataset,
            target_dataset=target_dataset,
            plan=plan,
            row_count=len(transformed),
        )
    return PrivacyTransformResult(
        rows=transformed,
        plan_version=plan.version,
        lineage=lineage,
    )


def validate_privacy_replication_policy(
    policy: PrivacyReplicationPolicy,
    *,
    plan: PrivacyTransformPlan | None,
) -> PrivacyReplicationDecision:
    if policy.source_environment != policy.production_environment:
        return _replication_decision(policy, plan=plan, is_allowed=True, reason="non_production_source")
    if policy.source_environment == policy.target_environment:
        return _replication_decision(policy, plan=plan, is_allowed=True, reason="same_environment")
    if policy.target_environment not in policy.allowed_target_environments:
        return _replication_decision(policy, plan=plan, is_allowed=False, reason="target_environment_not_allowed")
    if plan is None:
        return _replication_decision(
            policy,
            plan=plan,
            is_allowed=False,
            reason="privacy_transform_required",
            unprotected_fields=policy.sensitive_fields,
        )
    unprotected_fields = _unprotected_sensitive_fields(policy.sensitive_fields, plan)
    if unprotected_fields:
        return _replication_decision(
            policy,
            plan=plan,
            is_allowed=False,
            reason="sensitive_fields_unprotected",
            unprotected_fields=unprotected_fields,
        )
    return _replication_decision(policy, plan=plan, is_allowed=True, reason="privacy_transform_applied")


def redact_text_pii(value: str) -> str:
    redacted = _EMAIL_RE.sub(REDACTED_PII_VALUE, value)
    redacted = _SSN_RE.sub(REDACTED_PII_VALUE, redacted)
    return _PHONE_RE.sub(REDACTED_PII_VALUE, redacted)


def build_privacy_openlineage_event(
    result: PrivacyTransformResult,
    *,
    tenant_id: str,
    job_name: str,
    event_time: str,
    run_key: str | None = None,
) -> dict[str, object]:
    dataset_lineage = _require_dataset_lineage(result)
    source_dataset = _dataset_payload(dataset_lineage, key="sourceDataset")
    target_dataset = _dataset_payload(dataset_lineage, key="targetDataset")
    run_id = _privacy_openlineage_run_id(
        run_key
        or json.dumps(
            {"jobName": job_name, "tenantId": tenant_id, "privacyDatasetLineage": dataset_lineage},
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return {
        "eventType": "COMPLETE",
        "eventTime": event_time,
        "producer": _OPENLINEAGE_PRODUCER,
        "schemaURL": _OPENLINEAGE_SCHEMA_URL,
        "run": {"runId": run_id, "facets": {"foundry_lite_privacy": _privacy_run_facet(result)}},
        "job": {"namespace": f"foundry-lite://{tenant_id}/privacy-transform", "name": job_name},
        "inputs": [_openlineage_dataset(source_dataset, tenant_id=tenant_id)],
        "outputs": [_openlineage_dataset(target_dataset, tenant_id=tenant_id)],
    }


def _transform_row(
    row: Mapping[str, object],
    *,
    tenant_id: str,
    plan: PrivacyTransformPlan,
    rules: Mapping[str, PrivacyFieldRule],
    mapping_store: PrivacyMappingStore | None,
) -> dict[str, object]:
    transformed: dict[str, object] = {}
    for field_name, value in row.items():
        rule = rules.get(field_name)
        transformed[field_name] = (
            value
            if rule is None
            else _transform_value(value, tenant_id=tenant_id, plan=plan, rule=rule, mapping_store=mapping_store)
        )
    return transformed


def _transform_value(
    value: object,
    *,
    tenant_id: str,
    plan: PrivacyTransformPlan,
    rule: PrivacyFieldRule,
    mapping_store: PrivacyMappingStore | None,
) -> object:
    if rule.mode == "passthrough":
        return value
    if value is None:
        return None
    if rule.mode == "pseudonymize":
        pseudonym = _pseudonym(value, tenant_id=tenant_id, plan=plan, rule=rule)
        if rule.is_reversible:
            _store_reversible_mapping(
                value,
                tenant_id=tenant_id,
                plan=plan,
                rule=rule,
                pseudonym=pseudonym,
                mapping_store=mapping_store,
            )
        return pseudonym
    if rule.mode == "anonymize":
        return ANONYMIZED_VALUE
    if rule.mode == "redact_text":
        if not isinstance(value, str):
            raise ValueError("redact_text privacy field requires a string value")
        return redact_text_pii(value)
    raise ValueError(f"unsupported privacy transform mode: {rule.mode}")


def _pseudonym(
    value: object,
    *,
    tenant_id: str,
    plan: PrivacyTransformPlan,
    rule: PrivacyFieldRule,
) -> str:
    digest = hmac.new(
        plan.salt.encode("utf-8"),
        _pseudonym_message(value, tenant_id=tenant_id, rule=rule),
        hashlib.sha256,
    ).hexdigest()
    return f"pseudonym:{digest[:24]}"


def _pseudonym_message(value: object, *, tenant_id: str, rule: PrivacyFieldRule) -> bytes:
    payload = {
        "fieldName": rule.field_name,
        "scope": _effective_scope(tenant_id=tenant_id, rule=rule),
        "value": value,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _store_reversible_mapping(
    value: object,
    *,
    tenant_id: str,
    plan: PrivacyTransformPlan,
    rule: PrivacyFieldRule,
    pseudonym: str,
    mapping_store: PrivacyMappingStore | None,
) -> None:
    if mapping_store is None:
        raise ValueError("reversible privacy mapping requires a protected mapping store")
    mapping_store.store_mapping(
        PrivacyMappingRecord(
            tenant_id=tenant_id,
            plan_version=plan.version,
            field_name=rule.field_name,
            scope=_effective_scope(tenant_id=tenant_id, rule=rule),
            pseudonym=pseudonym,
            original_value=value,
        )
    )


def _require_allowed_replication(
    *,
    replication_policy: PrivacyReplicationPolicy | None,
    plan: PrivacyTransformPlan,
) -> PrivacyReplicationDecision | None:
    if replication_policy is None:
        return None
    decision = validate_privacy_replication_policy(replication_policy, plan=plan)
    if not decision.is_allowed:
        raise ValueError(f"privacy replication denied: {decision.reason}")
    return decision


def _replication_decision(
    policy: PrivacyReplicationPolicy,
    *,
    plan: PrivacyTransformPlan | None,
    is_allowed: bool,
    reason: str,
    unprotected_fields: tuple[str, ...] = (),
) -> PrivacyReplicationDecision:
    return PrivacyReplicationDecision(
        is_allowed=is_allowed,
        reason=reason,
        unprotected_fields=unprotected_fields,
        lineage={
            "privacyReplicationPolicy": {
                "sourceEnvironment": policy.source_environment,
                "targetEnvironment": policy.target_environment,
                "isAllowed": is_allowed,
                "reason": reason,
                "sensitiveFieldCount": len(policy.sensitive_fields),
                "unprotectedFields": list(unprotected_fields),
                "planVersion": None if plan is None else plan.version,
            }
        },
    )


def _unprotected_sensitive_fields(sensitive_fields: tuple[str, ...], plan: PrivacyTransformPlan) -> tuple[str, ...]:
    rules = {rule.field_name: rule for rule in plan.rules}
    return tuple(
        field_name
        for field_name in sensitive_fields
        if field_name not in rules or rules[field_name].mode not in PRIVACY_PROTECTING_MODES
    )


def _effective_scope(*, tenant_id: str, rule: PrivacyFieldRule) -> str:
    return tenant_id if rule.scope == "tenant" else rule.scope


def _value_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()[:16]


def _validate_dataset_lineage_refs(
    *,
    source_dataset: PrivacyDatasetRef | None,
    target_dataset: PrivacyDatasetRef | None,
) -> None:
    if (source_dataset is None) != (target_dataset is None):
        raise ValueError("privacy dataset lineage requires both source and target dataset refs")


def _dataset_lineage(
    *,
    source_dataset: PrivacyDatasetRef,
    target_dataset: PrivacyDatasetRef,
    plan: PrivacyTransformPlan,
    row_count: int,
) -> dict[str, object]:
    return {
        "sourceDataset": source_dataset.lineage_payload(),
        "targetDataset": target_dataset.lineage_payload(),
        "planVersion": plan.version,
        "rowCount": row_count,
    }


def _require_dataset_lineage(result: PrivacyTransformResult) -> Mapping[str, object]:
    dataset_lineage = result.lineage.get("privacyDatasetLineage")
    if not isinstance(dataset_lineage, Mapping):
        raise ValueError("privacy OpenLineage event requires privacy dataset lineage")
    return cast(Mapping[str, object], dataset_lineage)


def _dataset_payload(dataset_lineage: Mapping[str, object], *, key: str) -> Mapping[str, object]:
    payload = dataset_lineage.get(key)
    if not isinstance(payload, Mapping):
        raise ValueError(f"privacy dataset lineage is missing {key}")
    return cast(Mapping[str, object], payload)


def _privacy_openlineage_run_id(run_key: str) -> str:
    return str(uuid.uuid5(_PRIVACY_OPENLINEAGE_RUN_NAMESPACE, run_key))


def _privacy_run_facet(result: PrivacyTransformResult) -> dict[str, object]:
    facet: dict[str, object] = {
        "planVersion": result.plan_version,
        "privacyTransform": result.lineage["privacyTransform"],
        "privacyDatasetLineage": result.lineage["privacyDatasetLineage"],
    }
    replication_policy = result.lineage.get("privacyReplicationPolicy")
    if replication_policy is not None:
        facet["privacyReplicationPolicy"] = replication_policy
    return facet


def _openlineage_dataset(dataset: Mapping[str, object], *, tenant_id: str) -> dict[str, object]:
    namespace = str(dataset["namespace"])
    name = str(dataset["name"])
    version_id = str(dataset["versionId"])
    environment = str(dataset["environment"])
    return {
        "namespace": f"foundry-lite://{tenant_id}/{namespace}",
        "name": f"{name}@{version_id}",
        "facets": {
            "datasetVersion": {"datasetVersion": version_id},
            "environment": {"environment": environment},
        },
    }
