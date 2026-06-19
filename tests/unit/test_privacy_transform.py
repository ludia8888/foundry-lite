from __future__ import annotations

import pytest
from foundry_lite.security.privacy import (
    ANONYMIZED_VALUE,
    REDACTED_PII_VALUE,
    InMemoryProtectedPrivacyMappingStore,
    PrivacyDatasetRef,
    PrivacyFieldRule,
    PrivacyReplicationPolicy,
    PrivacyTransformPlan,
    build_privacy_openlineage_event,
    transform_privacy_rows,
    validate_privacy_replication_policy,
)


def test_same_identifier_maps_to_same_pseudonym_within_scope() -> None:
    plan = _privacy_plan()
    rows = [
        {"customer_id": "C-100", "email": "ada@example.com"},
        {"customer_id": "C-101", "email": "ada@example.com"},
    ]

    result = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)

    assert result.rows[0]["email"] == result.rows[1]["email"]
    assert str(result.rows[0]["email"]).startswith("pseudonym:")
    assert result.rows[0]["email"] != "ada@example.com"


def test_pseudonym_differs_across_tenants() -> None:
    plan = _privacy_plan()
    rows = [{"customer_id": "C-100", "email": "ada@example.com"}]

    tenant_a = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)
    tenant_b = transform_privacy_rows(rows, tenant_id="tenant-b", plan=plan)

    assert tenant_a.rows[0]["email"] != tenant_b.rows[0]["email"]


def test_anonymized_dataset_contains_no_raw_pii_samples() -> None:
    plan = _privacy_plan()
    rows = [
        {
            "customer_id": "C-100",
            "email": "ada@example.com",
            "ssn": "123-45-6789",
            "support_note": "Email ada@example.com or call 202-555-0101",
        }
    ]

    result = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)
    rendered = repr(result.rows)

    assert result.rows[0]["ssn"] == ANONYMIZED_VALUE
    assert REDACTED_PII_VALUE in str(result.rows[0]["support_note"])
    assert "ada@example.com" not in rendered
    assert "123-45-6789" not in rendered
    assert "202-555-0101" not in rendered


def test_privacy_transform_is_versioned_and_replayable() -> None:
    plan = _privacy_plan()
    rows = [{"customer_id": "C-100", "email": "ada@example.com"}]

    first = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)
    replay = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)
    rotated_salt = PrivacyTransformPlan(
        plan_name=plan.plan_name,
        salt="rotated-local-privacy-test-salt",
        rules=plan.rules,
    )

    assert first.rows == replay.rows
    assert first.plan_version == replay.plan_version
    assert rotated_salt.version != first.plan_version
    assert first.lineage == {
        "privacyTransform": {
            "planName": "customer-analytics-safe-copy",
            "planVersion": first.plan_version,
            "ruleCount": 3,
            "rowCount": 1,
        }
    }


def test_anonymized_dataset_lineage_links_source_and_target_versions() -> None:
    plan = _privacy_plan()
    source_dataset = PrivacyDatasetRef(
        namespace="raw",
        name="customers",
        version_id="dsv_raw_customers_1",
        environment="production",
    )
    target_dataset = PrivacyDatasetRef(
        namespace="analytics",
        name="customers_anonymized",
        version_id="dsv_anon_customers_1",
        environment="ai_experiment",
    )

    result = transform_privacy_rows(
        [{"email": "ada@example.com", "ssn": "123-45-6789"}],
        tenant_id="tenant-a",
        plan=plan,
        source_dataset=source_dataset,
        target_dataset=target_dataset,
    )

    assert result.lineage["privacyDatasetLineage"] == {
        "sourceDataset": {
            "namespace": "raw",
            "name": "customers",
            "versionId": "dsv_raw_customers_1",
            "environment": "production",
        },
        "targetDataset": {
            "namespace": "analytics",
            "name": "customers_anonymized",
            "versionId": "dsv_anon_customers_1",
            "environment": "ai_experiment",
        },
        "planVersion": result.plan_version,
        "rowCount": 1,
    }
    assert "ada@example.com" not in repr(result.lineage)
    assert "123-45-6789" not in repr(result.lineage)


def test_reversible_mapping_requires_protected_store() -> None:
    plan = _reversible_privacy_plan()
    rows = [{"customer_id": "C-100", "email": "ada@example.com"}]

    with pytest.raises(ValueError) as exc_info:
        transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan)

    assert str(exc_info.value) == "reversible privacy mapping requires a protected mapping store"


def test_reversible_mapping_is_stored_outside_rows_and_lineage() -> None:
    plan = _reversible_privacy_plan()
    mapping_store = InMemoryProtectedPrivacyMappingStore()
    rows = [{"customer_id": "C-100", "email": "ada@example.com"}]

    result = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan, mapping_store=mapping_store)
    pseudonym = str(result.rows[0]["email"])
    evidence = mapping_store.redacted_evidence()

    assert (
        mapping_store.get_original_value(
            tenant_id="tenant-a",
            plan_version=result.plan_version,
            field_name="email",
            scope="tenant-a",
            pseudonym=pseudonym,
        )
        == "ada@example.com"
    )
    assert evidence == (
        {
            "tenantId": "tenant-a",
            "planVersion": result.plan_version,
            "fieldName": "email",
            "scope": "tenant-a",
            "pseudonym": pseudonym,
            "originalValue": "***PROTECTED***",
            "originalValueHash": evidence[0]["originalValueHash"],
        },
    )
    assert "ada@example.com" not in repr(result.rows)
    assert "ada@example.com" not in repr(result.lineage)
    assert "ada@example.com" not in repr(evidence)


def test_production_replication_requires_privacy_plan_for_sensitive_fields() -> None:
    policy = PrivacyReplicationPolicy(
        source_environment="production",
        target_environment="analytics",
        sensitive_fields=("email", "ssn"),
    )

    decision = validate_privacy_replication_policy(policy, plan=None)

    assert not decision.is_allowed
    assert decision.reason == "privacy_transform_required"
    assert decision.unprotected_fields == ("email", "ssn")
    assert decision.lineage == {
        "privacyReplicationPolicy": {
            "sourceEnvironment": "production",
            "targetEnvironment": "analytics",
            "isAllowed": False,
            "reason": "privacy_transform_required",
            "sensitiveFieldCount": 2,
            "unprotectedFields": ["email", "ssn"],
            "planVersion": None,
        }
    }


def test_replication_policy_rejects_unprotected_sensitive_fields() -> None:
    policy = PrivacyReplicationPolicy(
        source_environment="production",
        target_environment="staging",
        sensitive_fields=("email", "ssn", "plain_note"),
    )
    plan = PrivacyTransformPlan(
        plan_name="incomplete-safe-copy",
        salt="local-privacy-test-salt",
        rules=(
            PrivacyFieldRule("email", "pseudonymize"),
            PrivacyFieldRule("plain_note", "passthrough"),
        ),
    )

    decision = validate_privacy_replication_policy(policy, plan=plan)

    assert not decision.is_allowed
    assert decision.reason == "sensitive_fields_unprotected"
    assert decision.unprotected_fields == ("ssn", "plain_note")
    with pytest.raises(ValueError) as exc_info:
        transform_privacy_rows(
            [{"email": "ada@example.com", "ssn": "123-45-6789"}],
            tenant_id="tenant-a",
            plan=plan,
            replication_policy=policy,
        )
    assert str(exc_info.value) == "privacy replication denied: sensitive_fields_unprotected"


def test_allowed_replication_policy_adds_lineage_without_raw_values() -> None:
    policy = PrivacyReplicationPolicy(
        source_environment="production",
        target_environment="ai_experiment",
        sensitive_fields=("email", "ssn", "support_note"),
    )
    plan = _privacy_plan()
    rows = [
        {
            "email": "ada@example.com",
            "ssn": "123-45-6789",
            "support_note": "Email ada@example.com",
        }
    ]

    result = transform_privacy_rows(rows, tenant_id="tenant-a", plan=plan, replication_policy=policy)

    assert result.lineage["privacyReplicationPolicy"] == {
        "sourceEnvironment": "production",
        "targetEnvironment": "ai_experiment",
        "isAllowed": True,
        "reason": "privacy_transform_applied",
        "sensitiveFieldCount": 3,
        "unprotectedFields": [],
        "planVersion": result.plan_version,
    }
    assert "ada@example.com" not in repr(result.rows)
    assert "ada@example.com" not in repr(result.lineage)


def test_privacy_openlineage_event_excludes_raw_pii() -> None:
    plan = _privacy_plan()
    policy = PrivacyReplicationPolicy(
        source_environment="production",
        target_environment="analytics",
        sensitive_fields=("email", "ssn", "support_note"),
    )
    result = transform_privacy_rows(
        [{"email": "ada@example.com", "ssn": "123-45-6789", "support_note": "Email ada@example.com"}],
        tenant_id="tenant-a",
        plan=plan,
        replication_policy=policy,
        source_dataset=_source_dataset(),
        target_dataset=_target_dataset(),
    )

    event = build_privacy_openlineage_event(
        result,
        tenant_id="tenant-a",
        job_name="privacy.customers.safe_copy",
        event_time="2026-06-19T00:00:00Z",
    )
    rendered = repr(event)

    assert event["eventType"] == "COMPLETE"
    assert event["job"] == {
        "namespace": "foundry-lite://tenant-a/privacy-transform",
        "name": "privacy.customers.safe_copy",
    }
    assert event["inputs"] == [
        {
            "namespace": "foundry-lite://tenant-a/raw",
            "name": "customers@dsv_raw_customers_1",
            "facets": {
                "datasetVersion": {"datasetVersion": "dsv_raw_customers_1"},
                "environment": {"environment": "production"},
            },
        }
    ]
    assert event["outputs"] == [
        {
            "namespace": "foundry-lite://tenant-a/analytics",
            "name": "customers_anonymized@dsv_anon_customers_1",
            "facets": {
                "datasetVersion": {"datasetVersion": "dsv_anon_customers_1"},
                "environment": {"environment": "analytics"},
            },
        }
    ]
    assert "ada@example.com" not in rendered
    assert "123-45-6789" not in rendered
    assert "202-555-0101" not in rendered
    assert result.plan_version in rendered


def test_privacy_openlineage_event_is_replay_stable() -> None:
    plan = _privacy_plan()
    result = transform_privacy_rows(
        [{"email": "ada@example.com", "ssn": "123-45-6789"}],
        tenant_id="tenant-a",
        plan=plan,
        source_dataset=_source_dataset(),
        target_dataset=_target_dataset(),
    )

    first = build_privacy_openlineage_event(
        result,
        tenant_id="tenant-a",
        job_name="privacy.customers.safe_copy",
        event_time="2026-06-19T00:00:00Z",
    )
    replay = build_privacy_openlineage_event(
        result,
        tenant_id="tenant-a",
        job_name="privacy.customers.safe_copy",
        event_time="2026-06-19T00:00:00Z",
    )

    assert first == replay


def _privacy_plan() -> PrivacyTransformPlan:
    return PrivacyTransformPlan(
        plan_name="customer-analytics-safe-copy",
        salt="local-privacy-test-salt",
        rules=(
            PrivacyFieldRule("email", "pseudonymize"),
            PrivacyFieldRule("ssn", "anonymize"),
            PrivacyFieldRule("support_note", "redact_text"),
        ),
    )


def _reversible_privacy_plan() -> PrivacyTransformPlan:
    return PrivacyTransformPlan(
        plan_name="customer-support-safe-copy",
        salt="local-privacy-test-salt",
        rules=(PrivacyFieldRule("email", "pseudonymize", is_reversible=True),),
    )


def _source_dataset() -> PrivacyDatasetRef:
    return PrivacyDatasetRef(
        namespace="raw",
        name="customers",
        version_id="dsv_raw_customers_1",
        environment="production",
    )


def _target_dataset() -> PrivacyDatasetRef:
    return PrivacyDatasetRef(
        namespace="analytics",
        name="customers_anonymized",
        version_id="dsv_anon_customers_1",
        environment="analytics",
    )
