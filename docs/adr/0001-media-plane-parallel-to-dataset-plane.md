# ADR-0001: Media Plane is parallel to Dataset Plane

- **Status:** Accepted
- **Date:** 2026-06-23
- **Source design:** the unstructured-data / media-processing application design (external review doc "foundry_lite_unstructured_application_plan_ko", baseline commit `9c1104`)
- **Related:** `docs/infra-ratchet.md` (Media M0–M9), `docs/infra-tricky-matrix.json` (media `sourceOfTruthRules`)

## Official Palantir design sources

- [Multimodal Data Plane](https://www.palantir.com/docs/foundry/architecture-center/multimodal-data-plane)
- [Media Set transforms API](https://www.palantir.com/docs/foundry/transforms-python/media-set-transforms-api)

These official pages are the public product-behavior cross-check for keeping table and media
modalities governable without pretending they share one storage contract. The external review
document named above supplied the initial proposal; it is not allowed to override the official
public behavior or the executable Foundry-lite evidence.

## Context

Foundry-lite needs to handle unstructured data — documents, PDF, email, images, audio, video, DICOM, future multimodal. The existing **Dataset Plane** has strong tabular invariants: `row_count`, schema, Parquet, single file/manifest, SQL/Spark transform, Iceberg snapshot. Forcing PDFs and video into a dataset row (or adding `datasets.storage_kind = media`) collapses those invariants and pollutes every service with `if format == media` branches (source doc §0, §2.2).

The current architecture is, however, an excellent foundation for a media platform: clean port/adapter boundaries, a staged → validate → promote → metadata-commit → audit/outbox/lineage commit protocol, runtime evidence (adapter failure taxonomy, durable audit/outbox/lineage), the infra ratchet discipline, the object/action closed loop, and "search is a projection" discipline.

## Decision

Add a **Media/Content Plane** as a new bounded context, parallel and equal to the Dataset Plane, connected to the rest of the system through `MediaReference`, lineage, outbox, Ontology, Action, and materialization — **not** by extending the Dataset layer.

### Seven product invariants (verbatim from source doc §0)

1. **Original media versions are immutable.** Even if a path is overwritten, an existing reference still points at the original bytes.
2. **The DB `COMMITTED` media version is the serving truth.** S3 object existence alone is not success.
3. **A derivative can never have weaker permissions than its source.** OCR text, thumbnails, transcripts, embeddings included.
4. **Processing is pinned to version + processing spec + model version.** Re-running the same input yields one logical result.
5. **The search index is a projection.** It must be fully rebuildable from the original and derived artifacts after deletion.
6. **The Ontology stores no binary or full text.** Object properties hold only an immutable `MediaReference`, summary, and status.
7. **An upload inside an Action is visible only when the Action succeeds.** A cancelled/failed form never exposes orphan media.

### Key decisions

- **Transactional-only v1** (§1.3): keep the project's atomicity/replayability strength; defer transactionless/append-visible modes until a real low-latency need exists, and then surface `partial=true`/`complete=false` explicitly.
- **Reference pins an immutable version, not a path** (§1.2, §6.3): `MediaReference` carries `mediaItemVersionId` + `contentHash`; `logical_path`/head is display + latest-lookup only.
- **Metadata-pointer commit** (§5.1, §6.2): serving truth = DB row where `media_item_versions.status = COMMITTED` and `media_transactions.status = COMMITTED` and blob stat/hash matches. A blob with no DB commit is an invisible orphan; a DB-committed version with a missing/corrupt blob is a hard failure.
- **Security-envelope inheritance** (§1.7, §12.1): every derivative/content-unit/embedding/index entry carries an envelope no weaker than its source; downgrades require an explicit privileged Action.
- **Local-first** (Ratchet M0, §15.1): the first slice is contracts + local filesystem storage; S3, PDF parsing, Temporal, Elasticsearch, OCR/FFmpeg/ASR/embedding are separate later ratchets.
- **Distinct ports, not extended ones:** `MediaStorageAdapter` (binary, range, no row*count) separate from `DatasetStorageAdapter`; `MediaProcessorAdapter` separate from the SQL/Parquet `ComputeAdapter`; `ContentIndexAdapter` separate from the object-level `SearchIndexAdapter`. The S3 client \_helper* may be reused; the port _contracts_ are not shared.

## Consequences

- The Dataset Plane's tabular model stays intact; no `if format == media` branching leaks into dataset services.
- Media transactions **mirror** the dataset commit protocol pattern (OPEN → COMMITTED/ABORTED, audit/outbox atomic with the DB commit) rather than reusing dataset code.
- New domain tables, ports, services, adapters, and Operations run types are added incrementally behind the infra ratchet.
- Media processing becomes the first real product-driven use of the Temporal adapter (today proven only as a Scale-Foundation boundary).

## Non-goals (deferred to later PRs / ratchets)

S3 media storage (PR3/M1), PDF parser (PR4/M2), content units + lexical search (PR5/M3), Ontology `media_reference` property + Action-bound upload (PR6/M4), OCR (M5), FFmpeg image/audio/video (M6), ASR (M7), embedding + hybrid retrieval (M8), access patterns + virtual media sets (M9).

This ADR + the contracts/ports + the local MediaSet transaction core (PR1 + PR2) constitute **Ratchet M0**.
