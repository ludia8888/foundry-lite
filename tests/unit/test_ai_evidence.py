from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import pytest
from foundry_lite.application.services.ai_evidence import (
    EvidenceModelRef,
    EvidenceSourceRef,
    EvidenceSourceSpan,
    build_insight_claim_payload,
    build_llm_extraction_evidence,
    revise_evidence_reference,
)
from foundry_lite.domain.errors import ValidationFailed


def _evidence():
    return build_llm_extraction_evidence(
        "llm_extraction",
        source=EvidenceSourceRef(
            dataset_version_id="dsv_social_posts_v1",
            object_type="SocialPost",
            object_id="post-1",
            object_version=3,
        ),
        model=EvidenceModelRef(
            extractor_version="sentiment-extractor@1.0.0",
            model_name="gpt-5-mini",
            model_version="2026-06-01",
            prompt_version="sentiment-prompt@4",
            model_parameters={"temperature": 0, "top_p": 1},
        ),
        human_review_status="pending",
        source_span=EvidenceSourceSpan(quote="delivery was late", timecode_start="00:00:03"),
        confidence=0.82,
    )


def test_insight_claim_requires_evidence_objects() -> None:
    evidence = _evidence()

    with pytest.raises(ValidationFailed, match="requires evidence objects"):
        build_insight_claim_payload(
            "claim-1",
            "Customers mention late delivery",
            evidence_object_ids=[],
            evidence_refs=[evidence],
            human_review_status="pending",
        )

    payload = build_insight_claim_payload(
        "claim-1",
        "Customers mention late delivery",
        evidence_object_ids=["post-1"],
        evidence_refs=[evidence],
        human_review_status="pending",
    )

    assert payload["evidenceObjectIds"] == ["post-1"]
    evidence_refs = cast(list[Mapping[str, object]], payload["evidenceRefs"])
    assert evidence_refs[0]["sourceDatasetVersionId"] == "dsv_social_posts_v1"


def test_llm_extraction_pins_prompt_and_model_version() -> None:
    payload = _evidence().payload()

    assert payload["sourceDatasetVersionId"] == "dsv_social_posts_v1"
    assert payload["sourceObjectVersion"] == 3
    assert payload["extractorVersion"] == "sentiment-extractor@1.0.0"
    assert payload["modelVersion"] == "2026-06-01"
    assert payload["promptVersion"] == "sentiment-prompt@4"
    assert str(payload["parametersHash"]).startswith("sha256:")


def test_reprocessing_preserves_old_evidence_and_creates_new_revision() -> None:
    previous = _evidence()
    revised = revise_evidence_reference(
        previous,
        extractor_version="sentiment-extractor@1.1.0",
        model_version="2026-06-15",
        prompt_version="sentiment-prompt@5",
        model_parameters={"temperature": 0, "top_p": 1},
        source_span=EvidenceSourceSpan(quote="delivery arrived late"),
        confidence=0.91,
    )

    assert previous.revision == 1
    assert revised.revision == 2
    assert revised.previous_evidence_id == previous.evidence_id
    assert revised.evidence_id != previous.evidence_id
    assert revised.payload()["modelVersion"] == "2026-06-15"


def test_masked_source_span_is_not_exposed_to_unauthorized_user() -> None:
    span = EvidenceSourceSpan(
        quote="Jane Doe disclosed her SSN 123-45-6789",
        timecode_start="00:00:01",
        timecode_end="00:00:05",
        bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
    )

    masked = span.redacted_payload(is_masked=True)

    assert masked == {"isMasked": True, "quote": "***MASKED***"}
    assert "Jane Doe" not in repr(masked)
    assert "123-45-6789" not in repr(masked)
