from __future__ import annotations

import pytest
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
