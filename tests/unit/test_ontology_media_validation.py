from __future__ import annotations

from typing import Any

import pytest
from foundry_lite.application.services.ontology_media_validation import validate_ontology_media_sets
from foundry_lite.domain.context import demo_admin_context
from foundry_lite.domain.errors import ValidationFailed


class _MediaSetLookup:
    def __init__(self, refs: set[tuple[str, str]]) -> None:
        self.refs = refs

    def media_set_by_ref(
        self,
        *,
        transaction: Any,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> object | None:
        del transaction, tenant_id
        return object() if (namespace, name) in self.refs else None


def test_nested_action_media_set_reference_must_exist() -> None:
    definition = {
        "objectTypes": [],
        "actionTypes": [
            {
                "apiName": "SubmitEvidence",
                "parameters": [
                    {
                        "apiName": "evidence",
                        "type": "struct",
                        "fields": [
                            {
                                "apiName": "photos",
                                "type": "array",
                                "itemType": "media",
                                "mediaSet": "case.photos",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    with pytest.raises(ValidationFailed) as raised:
        validate_ontology_media_sets(_MediaSetLookup(set()), object(), demo_admin_context(), definition)  # type: ignore[arg-type]
    assert raised.value.details == {
        "missing": [{"location": "actionTypes.SubmitEvidence.evidence.photos", "mediaSet": "case.photos"}]
    }


def test_object_and_action_media_references_accept_existing_tenant_media_set() -> None:
    definition = {
        "objectTypes": [
            {
                "apiName": "Case",
                "properties": [{"apiName": "receipt", "type": "attachment", "mediaSet": "case.documents"}],
            }
        ],
        "actionTypes": [
            {
                "apiName": "AttachReceipt",
                "parameters": [{"apiName": "receipt", "type": "attachment", "mediaSet": "case.documents"}],
            }
        ],
    }
    validate_ontology_media_sets(
        _MediaSetLookup({("case", "documents")}),
        object(),
        demo_admin_context(),
        definition,  # type: ignore[arg-type]
    )
