"""A pipeline source is either pinned to a committed version or declared live — never both.

The execution plan exists so a build can be reasoned about after the fact: replay and
late-data paths read the pinned versions to decide what an earlier run saw. A virtual table
breaks that premise, because the external system owns its own state and issues no version we
control. Palantir says so directly — "Virtual tables do not benefit from Foundry dataset
capabilities such as dataset versioning or branching."

The dangerous move would be to mint a synthetic pin so the contract validates. It would look
reproducible to every downstream reader while not being reproducible at all. So the plan
records the absence instead, and these tests keep the two states mutually exclusive.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    PipelineSourceVersionPin,
    pipeline_source_contract_payload,
)
from foundry_lite.domain.errors import ValidationFailed

_PIN = PipelineSourceVersionPin(version_id="v-1", ordinal=1, content_fingerprint="fp-1")


def _contract(**overrides: object) -> PipelineSourceContract:
    fields: dict[str, object] = {
        "node_id": "n-1",
        "descriptor_id": "source.dataset",
        "artifact_kind": PipelineArtifactKind.DATASET_VERSION,
        "resource_ref": "ds-1",
        "source_id": "src-1",
        "schema_contract": {"columns": []},
        "schema_hash": "hash-1",
        "schema_version": 1,
        "version_pins": (_PIN,),
        "security_envelope": {},
        "access_evidence": {},
    }
    fields.update(overrides)
    return PipelineSourceContract(**fields)  # type: ignore[arg-type]


def test_a_pinned_source_still_requires_a_committed_version() -> None:
    with pytest.raises(ValidationFailed, match="requires a committed version"):
        _contract(version_pins=())


def test_a_live_source_is_valid_without_any_version_pin() -> None:
    contract = _contract(
        descriptor_id="source.virtual_table",
        resource_ref="vt-1",
        version_pins=(),
        is_live_source=True,
    )

    assert contract.is_live_source is True
    assert contract.version_pins == ()


def test_a_live_source_may_not_also_claim_a_pin() -> None:
    """A pin on a live source would be a fiction the replay path would trust."""
    with pytest.raises(ValidationFailed, match="cannot carry version pins"):
        _contract(descriptor_id="source.virtual_table", is_live_source=True)


def test_the_plan_payload_reports_whether_the_source_was_live() -> None:
    """Evidence readers must be able to tell a reproducible run from one that was not."""
    pinned = pipeline_source_contract_payload(_contract())
    live = pipeline_source_contract_payload(
        _contract(descriptor_id="source.virtual_table", version_pins=(), is_live_source=True)
    )

    assert pinned["isLiveSource"] is False
    assert pinned["versionPins"] != []
    assert live["isLiveSource"] is True
    assert live["versionPins"] == []


def test_a_source_defaults_to_pinned_so_existing_plans_keep_their_guarantee() -> None:
    assert _contract().is_live_source is False
