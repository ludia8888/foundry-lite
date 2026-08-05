from __future__ import annotations

import pytest
from foundry_lite.application.services.action_function_batch import parse_action_function_batch_items
from foundry_lite.domain.action_runtime.ontology_edit_batch import OntologyEditBatch
from foundry_lite.domain.errors import ValidationFailed


def test_ontology_edit_batch_builds_deterministic_edit_plan() -> None:
    batch = OntologyEditBatch.from_payload(
        {
            "edits": [
                {
                    "kind": "modifyObject",
                    "objectType": "Order",
                    "objectId": "O-1",
                    "expectedVersion": 3,
                    "patch": {"status": "APPROVED"},
                },
                {
                    "kind": "createLink",
                    "linkType": "OrderOwner",
                    "sourceObjectId": "O-1",
                    "targetObjectId": "U-7",
                },
            ],
            "readSetVersions": {"Order:O-1": 3},
            "provenance": {"model": "logic-dag"},
        }
    )

    first = batch.to_edit_plan(operation_prefix="run-1:function")
    second = batch.to_edit_plan(operation_prefix="run-1:function")

    assert first == second
    assert first.objects_to_modify[0].operation_key == "run-1:function:0"
    assert first.links_to_create[0].operation_key == "run-1:function:1"
    assert first.read_set_versions == {"Order:O-1": 3}


def test_ontology_edit_batch_rejects_unknown_edits_and_bad_versions() -> None:
    with pytest.raises(ValidationFailed, match="unsupported ontology edit"):
        OntologyEditBatch.from_payload({"edits": [{"kind": "runSql"}]}).to_edit_plan(operation_prefix="r")
    with pytest.raises(ValidationFailed, match="expectedVersion"):
        OntologyEditBatch.from_payload(
            {"edits": [{"kind": "deleteObject", "objectType": "Order", "objectId": "O-1", "expectedVersion": -1}]}
        ).to_edit_plan(operation_prefix="r")


def test_function_action_batch_enforces_request_limit_and_unique_targets() -> None:
    item = {"objectId": "O-1", "expectedObjectVersion": 1, "params": {"status": "APPROVED"}}
    with pytest.raises(ValidationFailed, match="batch limit exceeded"):
        parse_action_function_batch_items([item] * 21, maximum=20)
    with pytest.raises(ValidationFailed, match="targets must be unique"):
        parse_action_function_batch_items([item, item], maximum=20)


def test_ontology_edit_batch_enforces_edit_and_object_type_limits() -> None:
    edit = {
        "kind": "modifyObject",
        "objectType": "Order",
        "objectId": "O-1",
        "expectedVersion": 1,
        "patch": {"status": "APPROVED"},
    }
    with pytest.raises(ValidationFailed, match="edit limit exceeded"):
        OntologyEditBatch.from_payload({"edits": [edit] * 10_001})

    object_type_edits = [
        {
            **edit,
            "objectType": f"Type{index}",
            "objectId": f"O-{index}",
        }
        for index in range(51)
    ]
    with pytest.raises(ValidationFailed, match="object type limit exceeded"):
        OntologyEditBatch.from_payload({"edits": object_type_edits})


def test_combined_function_results_reject_inconsistent_read_set_versions() -> None:
    first = OntologyEditBatch.from_payload(
        {
            "edits": [
                {
                    "kind": "modifyObject",
                    "objectType": "Order",
                    "objectId": "O-1",
                    "expectedVersion": 1,
                    "patch": {"status": "APPROVED"},
                }
            ],
            "readSetVersions": {"Order:O-1": 1},
        }
    )
    second = OntologyEditBatch.from_payload(
        {
            "edits": [
                {
                    "kind": "modifyObject",
                    "objectType": "Order",
                    "objectId": "O-2",
                    "expectedVersion": 1,
                    "patch": {"status": "APPROVED"},
                }
            ],
            "readSetVersions": {"Order:O-1": 2},
        }
    )

    with pytest.raises(ValidationFailed, match="disagree on a read-set version"):
        OntologyEditBatch.combine((first, second))
