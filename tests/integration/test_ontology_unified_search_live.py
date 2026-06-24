"""L12b ontology-anchored unified search — real end-to-end (Palantir OAG).

Drives the WHOLE loop through the composed ``FoundryLite`` with REAL embeddings (fastembed
bge for object semantic vectors AND media content vectors): seed ontology Order objects with
distinct semantic notes and rebuild the object search index; commit a real PDF media version,
bind it to one Order via a media-reference (M4), process it (pypdf) into content units and
project them into a promoted content generation. Then a SINGLE natural-language query returns
the OWNING OBJECTS, RRF-ranked: one object lifted through the media content match, a second
matched by its own semantic note. The result unit is the OBJECT, reached via the media edge.
"""

from __future__ import annotations

import io
from dataclasses import replace
from pathlib import Path

import pytest
from foundry_lite.application.foundry import FoundryLite
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure.adapters import LocalEmbeddingAdapter
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.local_runtime import create_local_core_dependencies

from tests.conftest import prepare_indexed_demo

_PDF_SPEC = ProcessorSpec(processor="pdf_text_v1", processor_version="1.0")


def _make_pdf(pages: list[str]) -> bytes:
    """Minimal real text-extractable PDF (mirrors test_pdf_text_processor._make_pdf)."""
    objs: list[bytes] = [b"<</Type/Catalog/Pages 2 0 R>>"]
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(("<</Type/Pages/Kids[" + kids + f"]/Count {len(pages)}>>").encode())
    font_obj = 3 + 2 * len(pages)
    for i, text in enumerate(pages):
        content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
        objs.append(
            f"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents {4 + 2 * i} 0 R"
            f"/Resources<</Font<</F1 {font_obj} 0 R>>>>>>".encode()
        )
        objs.append(b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream")
    objs.append(b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>")
    out = b"%PDF-1.4\n"
    offsets: list[int] = []
    for number, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj".encode() + body + b"endobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    return out


def _real_foundry(tmp_path: Path) -> FoundryLite:
    deps = replace(
        create_local_core_dependencies(storage_root=tmp_path / "flite"),
        embedding_model_adapter=LocalEmbeddingAdapter(
            embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION
        ),
    )
    return FoundryLite(dependencies=deps)


def _approve(foundry: FoundryLite, ctx: RequestContext, object_id: str, reason: str) -> None:
    order = foundry.objects.get("Order", object_id, ctx=ctx)
    foundry.actions.apply(
        "ApproveOrder",
        object_type="Order",
        object_id=object_id,
        expected_object_version=order["objectVersion"],
        params={"reason": reason},
        idempotency_key=f"unified-{object_id}",
        ctx=ctx,
    )


def _commit_pdf(foundry: FoundryLite, ctx: RequestContext, *, body: bytes) -> str:
    media_set = foundry.media.create_media_set(
        ctx,
        namespace="legal",
        name="order-scans",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        classification="confidential",
    )
    tx = foundry.media.open_transaction(ctx, media_set_id=media_set.media_set_id, idempotency_key="unified-tx-1")
    staged = foundry.media.upload(
        ctx,
        media_set_id=media_set.media_set_id,
        media_transaction_id=tx,
        logical_path="/o1002-contract.pdf",
        source=io.BytesIO(body),
        supplied_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        security_envelope={"tenantId": ctx.tenant_id, "classification": "confidential"},
    )
    foundry.media.commit(ctx, media_transaction_id=tx)
    return staged.media_item_version_id


@pytest.mark.integration_scenario("ontology-unified-search")
def test_unified_search_returns_owning_objects_fused_by_rrf(tmp_path: Path) -> None:
    foundry = _real_foundry(tmp_path)
    ctx = prepare_indexed_demo(foundry)

    # 1) Two Orders get distinct semantic notes; O-1001 matches the query by its OWN text.
    _approve(foundry, ctx, "O-1001", "refrigerated cold chain truck delivery")
    _approve(foundry, ctx, "O-1002", "an unrelated invoice dispute")
    foundry.objects.rebuild_search("Order", ctx=ctx)  # real bge object vectors

    # 2) A real PDF whose text is about the SAME topic is bound to O-1002 (no own-text match).
    version_id = _commit_pdf(foundry, ctx, body=_make_pdf(["frozen cold chain refrigerated truck logistics"]))
    foundry.media.bind_reference(
        ctx,
        holder_type="Order",
        holder_id="O-1002",
        property_name="contractScan",
        media_item_version_id=version_id,
        idempotency_key="unified-bind-1",
    )

    # 3) Process the PDF (pypdf) into content units and project them into a promoted generation.
    outcome = foundry.media.process(ctx, media_item_version_id=version_id, spec=_PDF_SPEC)
    foundry.media.configure_content_generation(ctx, generation="g1")
    foundry.media.index_derivative(ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    foundry.media.promote_content_generation(ctx, expected_active="", generation="g1")

    # 4) ONE natural-language query -> ranked ontology OBJECTS (the result unit), fused via RRF.
    hits = foundry.objects.unified_search(
        ctx, query_text="cold chain refrigerated truck", object_type="Order", limit=10
    )
    by_id = {hit.object_id: hit for hit in hits}

    assert "O-1001" in by_id and "O-1002" in by_id  # both surface as ranked ontology objects
    # O-1002 is LIFTED through the media-reference edge: its bound PDF content matched the query,
    # and the hit carries the content citation (content unit + source media version) that lifted it.
    # (Dense nearestNeighbors returns the whole Object Set ranked, so O-1002 also carries a weak
    # own-text rank — but it is its bound media content that supplies the topical match.)
    o1002 = by_id["O-1002"]
    assert o1002.media_citations and o1002.media_citations[0].source_media_item_version_id == version_id
    assert o1002.media_citations[0].property_name == "contractScan"
    # O-1001 surfaces via its OWN cold-chain semantic note, with NO media citation (nothing bound).
    assert by_id["O-1001"].has_own_text_match is True
    assert by_id["O-1001"].media_citations == ()


@pytest.mark.integration_scenario("ontology-unified-search")
def test_unified_search_excludes_media_bound_to_unreadable_object(tmp_path: Path) -> None:
    foundry = _real_foundry(tmp_path)
    ctx = prepare_indexed_demo(foundry)
    foundry.objects.rebuild_search("Order", ctx=ctx)

    version_id = _commit_pdf(foundry, ctx, body=_make_pdf(["cold chain refrigerated truck"]))
    # Bind the media to an object id that does NOT exist (get_object -> NotFound): the content
    # match can never lift to a readable object, so the ontology-anchored result stays empty.
    foundry.media.bind_reference(
        ctx,
        holder_type="Order",
        holder_id="O-DOES-NOT-EXIST",
        property_name="contractScan",
        media_item_version_id=version_id,
        idempotency_key="unified-bind-ghost",
    )
    outcome = foundry.media.process(ctx, media_item_version_id=version_id, spec=_PDF_SPEC)
    foundry.media.configure_content_generation(ctx, generation="g1")
    foundry.media.index_derivative(ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    foundry.media.promote_content_generation(ctx, expected_active="", generation="g1")

    hits = foundry.objects.unified_search(
        ctx, query_text="cold chain refrigerated truck", object_type="Order", limit=10
    )
    assert all(hit.object_id != "O-DOES-NOT-EXIST" for hit in hits)
    assert all(hit.media_citations == () for hit in hits)  # no media lifted to any readable object
