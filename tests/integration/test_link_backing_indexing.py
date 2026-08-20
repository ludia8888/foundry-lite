"""Integration proof for datasource-backed many-to-many Ontology links."""

from __future__ import annotations

from pathlib import Path

from foundry_lite.application.foundry import FoundryLite
from foundry_lite.domain.context import RequestContext, demo_admin_context
from foundry_lite.infrastructure import schema as db
from sqlalchemy import select


def test_many_to_many_link_reads_its_join_dataset_and_tombstones_removed_pairs(
    foundry: FoundryLite,
    tmp_path: Path,
) -> None:
    """A join-backed link must not derive pairs from the source object rows."""
    ctx = demo_admin_context()
    for dataset_ref, primary_key in (
        ("clean.orders", ["order_id"]),
        ("clean.tags", ["tag_id"]),
        ("clean.order_tag_memberships", ["membership_id"]),
    ):
        foundry.datasets.ensure(dataset_ref, ctx=ctx, primary_key=primary_key)

    _upload_csv(foundry, "clean.orders", tmp_path / "orders.csv", "order_id\nO-1\nO-2\n", ctx)
    _upload_csv(foundry, "clean.tags", tmp_path / "tags.csv", "tag_id\nT-1\nT-2\n", ctx)
    initial_memberships = foundry.datasets.upload_csv(
        "clean.order_tag_memberships",
        _write_csv(
            tmp_path / "memberships-v1.csv",
            "membership_id,order_id,tag_id\nM-1,O-1,T-1\nM-2,O-1,T-2\nM-3,O-2,T-2\n",
        ),
        ctx=ctx,
    )
    foundry.ontology.apply(_write_ontology(tmp_path / "order-tag.yaml"), ctx=ctx)

    foundry.objects.reindex("Order", ctx=ctx)
    foundry.objects.reindex("Tag", ctx=ctx)

    first_links = foundry.objects.links("Order", "O-1", "OrderTag", ctx=ctx)
    assert {link["to"]["objectId"] for link in first_links} == {"T-1", "T-2"}

    with foundry.engine.begin() as conn:
        source_versions = {
            row["source_dataset_version_id"]
            for row in conn.execute(
                select(db.object_links.c.source_dataset_version_id).where(
                    db.object_links.c.link_type_api_name == "OrderTag"
                )
            ).mappings()
        }
        first_run_source = conn.execute(
            select(db.index_runs.c.source_ref)
            .where(db.index_runs.c.object_type_api_name == "Order")
            .order_by(db.index_runs.c.created_at.desc())
            .limit(1)
        ).scalar_one()
    assert source_versions == {initial_memberships.version_id}
    assert first_run_source["linkSourceDatasetVersionIds"] == {"OrderTag": initial_memberships.version_id}

    replacement_memberships = foundry.datasets.upload_csv(
        "clean.order_tag_memberships",
        _write_csv(
            tmp_path / "memberships-v2.csv",
            "membership_id,order_id,tag_id\nM-1,O-1,T-1\nM-3,O-2,T-2\n",
        ),
        ctx=ctx,
    )
    foundry.objects.reindex("Order", ctx=ctx)

    refreshed_links = foundry.objects.links("Order", "O-1", "OrderTag", ctx=ctx)
    assert {link["to"]["objectId"] for link in refreshed_links} == {"T-1"}
    with foundry.engine.begin() as conn:
        rows = conn.execute(
            select(
                db.object_links.c.from_object_id,
                db.object_links.c.to_object_id,
                db.object_links.c.deleted,
                db.object_links.c.source_dataset_version_id,
            )
            .where(db.object_links.c.link_type_api_name == "OrderTag")
            .order_by(db.object_links.c.from_object_id, db.object_links.c.to_object_id)
        ).all()
        refreshed_run_source = conn.execute(
            select(db.index_runs.c.source_ref)
            .where(db.index_runs.c.object_type_api_name == "Order")
            .order_by(db.index_runs.c.created_at.desc())
            .limit(1)
        ).scalar_one()
    assert rows == [
        ("O-1", "T-1", False, replacement_memberships.version_id),
        ("O-1", "T-2", True, replacement_memberships.version_id),
        ("O-2", "T-2", False, replacement_memberships.version_id),
    ]
    assert refreshed_run_source["linkSourceDatasetVersionIds"] == {"OrderTag": replacement_memberships.version_id}


def _upload_csv(foundry: FoundryLite, dataset_ref: str, path: Path, contents: str, ctx: RequestContext) -> None:
    """Write and upload one CSV fixture."""
    foundry.datasets.upload_csv(dataset_ref, _write_csv(path, contents), ctx=ctx)


def _write_csv(path: Path, contents: str) -> Path:
    """Write one UTF-8 CSV fixture and return its path."""
    path.write_text(contents, encoding="utf-8")
    return path


def _write_ontology(path: Path) -> Path:
    """Write the smallest Ontology with a datasource-backed M:N link."""
    path.write_text(
        """
objectTypes:
  - apiName: Order
    displayName: Order
    primaryKey: orderId
    backing:
      dataset: clean.orders
      mode: snapshot
      primaryKeyColumns: [order_id]
    properties:
      - apiName: orderId
        column: order_id
        type: string
        nullable: false
  - apiName: Tag
    displayName: Tag
    primaryKey: tagId
    backing:
      dataset: clean.tags
      mode: snapshot
      primaryKeyColumns: [tag_id]
    properties:
      - apiName: tagId
        column: tag_id
        type: string
        nullable: false
linkTypes:
  - apiName: OrderTag
    displayName: Order has Tag
    from: Order
    to: Tag
    cardinality: many_to_many
    backing:
      dataset: clean.order_tag_memberships
      fromKey: order_id
      toKey: tag_id
""".strip(),
        encoding="utf-8",
    )
    return path
