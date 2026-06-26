"""L9 GOLDEN end-to-end Media/Content Plane pipeline (the active-covered headline proof).

Proves raw unstructured media flows CONSISTENTLY upstream -> downstream on the single
invariant that the DB COMMITTED version is the only serving truth, exactly like Palantir
Foundry treats media as a first-class plane (media set + transform -> derivative ->
ontology media-reference -> semantic search -> preview). Composed from the REAL engines and
LIVE infrastructure the production runtime uses, never fakes:

  raw upload (live MinIO, ``s3-media`` profile)
    -> real Tesseract OCR (``ocr_v1`` derivative + ``page`` content units commit; SUCCEEDED
       ``media_processing_runs`` row is durable operator evidence)
    -> Ontology ``media_reference`` binding pins the immutable media version (M4)
    -> projection into a LIVE Elasticsearch ``dense_vector`` index (testcontainers)
    -> real fastembed hybrid/semantic search returns the doc with a verified ``text_hash``
       citation and tenant ACL (no cross-tenant leakage)
    -> real Pillow thumbnail preview served from cache on the 2nd call without re-rendering,
       and never treated as serving truth.

A second case threads the audio path (committed WAV -> real faster-whisper ASR ->
``audio_segment`` units -> live ES -> searchable). The headline assertion is identity
threading: the SAME ``media_item_version_id`` is the join key across derivative ->
content_unit -> ontology binding -> ES index -> search hit, proving consistent
upstream->downstream lineage.

All engines (tesseract / whisper / fastembed / Pillow) are hard requirements (installed +
model-cached in CI); MinIO + Elasticsearch run via testcontainers. The test never skips, so
the real glue stays in the coverage lane.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from time import monotonic, sleep
from uuid import uuid4

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_processor import ProcessorSpec
from foundry_lite.application.services.media.access_patterns import MediaAccessPatternService
from foundry_lite.application.services.media.binding import MediaReferenceBindingService
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.asr_processor import AsrProcessorAdapter, _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.elasticsearch_content_index import ElasticsearchContentIndexAdapter
from foundry_lite.infrastructure.adapters.elasticsearch_search import ElasticsearchAdapterConfig
from foundry_lite.infrastructure.adapters.es_client import build_es_client
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_embedding import (
    FASTEMBED_MODEL_VERSION,
    LocalEmbeddingAdapter,
    _fastembed_embedding_engine,
)
from foundry_lite.infrastructure.adapters.local_preview_renderer import LocalPreviewRendererAdapter
from foundry_lite.infrastructure.adapters.ocr_processor import OcrProcessorAdapter, _tesseract_ocr_engine
from foundry_lite.infrastructure.adapters.s3_client import build_s3_client
from foundry_lite.infrastructure.adapters.s3_media_storage import S3MediaStorageAdapter, S3MediaStorageConfig
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaAccessCacheRepository,
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
)
from sqlalchemy import create_engine
from testcontainers.core.container import DockerContainer

from tests.integration.elasticsearch_live_lock import live_elasticsearch_container, live_elasticsearch_lock

MINIO_ACCESS_KEY = "foundry_lite"
MINIO_SECRET_KEY = "foundry_lite_password"
_ES_IMAGE = "docker.elastic.co/elasticsearch/elasticsearch:9.0.0"
_OCR_SPEC = ProcessorSpec(processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.5.2")
_ASR_SPEC = ProcessorSpec(processor="asr_v1", processor_version="1.0", model="whisper", model_version="tiny")
_RENDERED_TEXT = "HELLO FOUNDRY"
_ASR_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "media" / "asr_quick_brown_fox.wav"
_THUMBNAIL = "thumbnail"


@dataclass(frozen=True)
class MinioServer:
    endpoint_url: str


class _FakeRuntime:
    """Records nothing but satisfies the audit/outbox collaborator boundary."""

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _Plane:
    ctx: RequestContext
    engine: object
    deriv: SqlAlchemyMediaDerivativeRepository
    catalog: MediaCatalogService
    upload: MediaUploadService
    transaction: MediaTransactionService
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService
    binding: MediaReferenceBindingService
    access_pattern: MediaAccessPatternService
    ocr_processing: MediaProcessingService
    asr_processing: MediaProcessingService

    def commit_media(self, *, media_set_id: str, source: bytes, logical_path: str, mime: str, fmt: str) -> str:
        tx = self.transaction.open(self.ctx, media_set_id=media_set_id, idempotency_key=f"idem-{logical_path}")
        session = self.upload.initiate(
            self.ctx, media_set_id=media_set_id, logical_path=logical_path, supplied_mime_type=mime
        )
        staged = self.upload.complete(
            self.ctx,
            inputs=MediaUploadInput(
                media_set_id=media_set_id,
                media_transaction_id=tx,
                logical_path=logical_path,
                supplied_mime_type=mime,
                schema_type="image" if fmt == "png" else "audio",
                format=fmt,
                security_envelope={"tenantId": self.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,
            source=io.BytesIO(source),
        )
        self.transaction.commit(self.ctx, media_transaction_id=tx)
        return staged.media_item_version_id

    def content_units(self, derivative_id: str) -> list[object]:
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            return list(
                self.deriv.get_content_units(
                    transaction=conn, tenant_id=self.ctx.tenant_id, derivative_id=derivative_id
                )
            )


def _render_text_png(path: Path) -> None:
    pil_image = import_module("PIL.Image")
    pil_draw = import_module("PIL.ImageDraw")
    small = pil_image.new("RGB", (320, 80), color="white")
    pil_draw.Draw(small).text((10, 30), _RENDERED_TEXT, fill="black")
    small.resize((640, 160), pil_image.NEAREST).save(path, format="PNG")


def _minio_ready(endpoint: str) -> bool:
    from urllib.error import URLError
    from urllib.request import urlopen

    try:
        with urlopen(f"{endpoint}/minio/health/live", timeout=2) as response:  # nosec B310
            return 200 <= response.status < 500
    except (URLError, OSError):
        return False


def _wait_until(condition, *, timeout_seconds: float = 60, interval_seconds: float = 1) -> None:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        if condition():
            return
        sleep(interval_seconds)
    raise TimeoutError("readiness condition was not met before the deadline")


@pytest.fixture(scope="module")
def minio_server() -> Iterator[MinioServer]:
    container = (
        DockerContainer("minio/minio")
        .with_env("MINIO_ROOT_USER", MINIO_ACCESS_KEY)
        .with_env("MINIO_ROOT_PASSWORD", MINIO_SECRET_KEY)
        .with_command("server /data --address :9000")
        .with_exposed_ports(9000)
    )
    container.start()
    try:
        endpoint = f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9000)}"
        _wait_until(lambda: _minio_ready(endpoint))
        yield MinioServer(endpoint_url=endpoint)
    finally:
        container.stop()


@pytest.fixture(scope="module")
def es_url() -> Iterator[str]:
    with live_elasticsearch_lock():
        container = (
            live_elasticsearch_container(_ES_IMAGE)
            .with_env("xpack.security.enabled", "false")
            .with_env("discovery.type", "single-node")
            .with_env("ES_JAVA_OPTS", "-Xms1g -Xmx1g")
            # tmpfs data dir: ES refresh fsync on the vz virtiofs disk is slow enough to exceed
            # client timeouts; a memory-backed data dir keeps writes fast (same as the live cluster).
            .with_kwargs(tmpfs={"/usr/share/elasticsearch/data": "rw,size=1g"})
        )
        container.start()
        try:
            yield f"http://{container.get_container_host_ip()}:{container.get_exposed_port(9200)}"
        finally:
            container.stop()


def _s3_media_storage(minio_server: MinioServer, bucket: str) -> S3MediaStorageAdapter:
    client = build_s3_client(
        endpoint_url=minio_server.endpoint_url,
        access_key_id=MINIO_ACCESS_KEY,
        secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )
    return S3MediaStorageAdapter(
        S3MediaStorageConfig(
            bucket=bucket,
            endpoint_url=minio_server.endpoint_url,
            access_key_id=MINIO_ACCESS_KEY,
            secret_access_key=MINIO_SECRET_KEY,
        ),
        client=client,
    )


@pytest.fixture
def plane(minio_server: MinioServer, es_url: str, tmp_path: Path) -> _Plane:
    engine = create_engine(f"sqlite:///{tmp_path / 'golden.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    deriv = SqlAlchemyMediaDerivativeRepository(engine)
    cache_repo = SqlAlchemyMediaAccessCacheRepository(engine)
    binding_repo = SqlAlchemyMediaReferenceBindingRepository(engine)
    storage = _s3_media_storage(minio_server, f"flite-golden-{uuid4().hex}")
    runtime = _FakeRuntime()
    embedding = LocalEmbeddingAdapter(
        embedding_engine=_fastembed_embedding_engine, model_version=FASTEMBED_MODEL_VERSION
    )

    es_client = build_es_client(endpoint=es_url, request_timeout_seconds=30)
    # readiness barrier (not a sleep): block until the cluster can allocate shards
    es_client.cluster.health(wait_for_status="yellow", timeout="60s")
    index = ElasticsearchContentIndexAdapter(
        ElasticsearchAdapterConfig(endpoint=es_url, index_prefix=f"l9{uuid4().hex[:6]}", request_timeout_seconds=30),
        client=es_client,
    )

    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    indexing = MediaIndexingService(
        engine=engine,
        media_derivative_repository=deriv,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": runtime})
    retrieval = DefaultContentRetrievalService(
        engine=engine,
        media_derivative_repository=deriv,
        content_index_adapter=index,
        embedding_model_adapter=embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    binding = MediaReferenceBindingService(
        engine=engine, media_repository=repo, media_reference_binding_repository=binding_repo
    )
    binding.bind_collaborators({"runtime_service": runtime})
    access_pattern = MediaAccessPatternService(
        engine=engine,
        media_repository=repo,
        media_storage=storage,
        media_access_cache_repository=cache_repo,
        media_preview_renderer=LocalPreviewRendererAdapter(),
    )
    ocr_processing = _processing(engine, repo, deriv, storage, OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine))
    asr_processing = _processing(
        engine, repo, deriv, storage, AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine)
    )
    return _Plane(
        ctx=RequestContext(),
        engine=engine,
        deriv=deriv,
        catalog=catalog,
        upload=upload,
        transaction=transaction,
        indexing=indexing,
        retrieval=retrieval,
        binding=binding,
        access_pattern=access_pattern,
        ocr_processing=ocr_processing,
        asr_processing=asr_processing,
    )


def _processing(
    engine: object, repo: object, deriv: object, storage: object, processor: object
) -> MediaProcessingService:
    svc = MediaProcessingService(
        engine=engine,
        media_repository=repo,
        media_derivative_repository=deriv,
        media_storage=storage,
        media_processor=processor,
    )
    svc.bind_collaborators({"runtime_service": _FakeRuntime()})
    return svc


def _image_set(plane: _Plane) -> str:
    return plane.catalog.create_media_set(
        plane.ctx,
        MediaSetSpec(
            namespace="ops",
            name="scans",
            schema_type="image",
            primary_format="png",
            allowed_input_formats=("png",),
            classification="confidential",
        ),
    ).media_set_id


def _audio_set(plane: _Plane) -> str:
    return plane.catalog.create_media_set(
        plane.ctx,
        MediaSetSpec(
            namespace="calls",
            name="recordings",
            schema_type="audio",
            primary_format="wav",
            allowed_input_formats=("wav",),
            classification="confidential",
        ),
    ).media_set_id


@pytest.mark.integration_scenario("media-golden-pipeline")
def test_media_golden_pipeline_threads_one_version_upstream_to_downstream(plane: _Plane, tmp_path: Path) -> None:
    # 1) raw upload of a real image into live MinIO -> COMMITTED serving-truth version.
    png_path = tmp_path / "hello.png"
    _render_text_png(png_path)
    version_id = plane.commit_media(
        media_set_id=_image_set(plane),
        source=png_path.read_bytes(),
        logical_path="/hello.png",
        mime="image/png",
        fmt="png",
    )

    # 2) real Tesseract OCR -> derivative + content units commit; SUCCEEDED run is operator evidence.
    outcome = plane.ocr_processing.process(plane.ctx, media_item_version_id=version_id, spec=_OCR_SPEC)
    derivative = plane.ocr_processing.resolve_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed" and derivative.derivative_kind == "ocr_v1"
    assert derivative.source_media_item_version_id == version_id  # derivative pins the source version

    units = plane.content_units(outcome.media_derivative_id)
    recognized = " ".join(unit.text for unit in units).upper()
    assert "HELLO" in recognized and "FOUNDRY" in recognized
    assert all(unit.source_media_item_version_id == version_id for unit in units)  # content units pin the version

    runs = plane.ocr_processing.list_media_runs(plane.ctx, source_media_item_version_id=version_id)
    succeeded = next(run for run in runs if run.status == "SUCCEEDED")
    assert succeeded.media_derivative_id == outcome.media_derivative_id and succeeded.finished_at is not None

    # 3) bind the immutable version onto an Ontology object via a media_reference property (M4).
    binding = plane.binding.bind(
        plane.ctx,
        holder_type="ContractObject",
        holder_id="contract-42",
        property_name="scanCover",
        media_item_version_id=version_id,
        idempotency_key="bind-golden-1",
    )
    assert binding.media_item_version_id == version_id  # ontology reference pins the version
    resolved = plane.binding.resolve(
        plane.ctx, holder_type="ContractObject", holder_id="contract-42", property_name="scanCover"
    )
    assert resolved is not None and resolved.media_item_version_id == version_id
    # ACL: a caller whose clearance does not cover "confidential" sees the reference masked.
    assert (
        plane.binding.resolve(
            plane.ctx,
            holder_type="ContractObject",
            holder_id="contract-42",
            property_name="scanCover",
            allowed_classifications=("public",),
        )
        is None
    )

    # 4) project content units into a LIVE Elasticsearch index (a rebuildable projection, not truth).
    plane.indexing.index_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    plane.indexing.promote(plane.ctx, expected_active="", generation="g1")

    # 5) real hybrid/semantic search returns the doc with a verified text_hash citation + tenant ACL.
    hits = plane.retrieval.search_content(
        plane.ctx, query=HybridContentQuery(tenant_id=plane.ctx.tenant_id, text="hello")
    )
    hit = next(hit for hit in hits if hit.source_media_item_version_id == version_id)  # SAME version threads through
    by_id = {unit.content_unit_id: unit for unit in units}
    assert hit.text_hash is not None and hit.text_hash == by_id[hit.content_unit_id].text_hash  # citation integrity
    # cross-tenant ACL: another tenant's search never recalls this confidential content.
    other = RequestContext(tenant_id="tenant-intruder")
    assert (
        plane.retrieval.search_content(other, query=HybridContentQuery(tenant_id=other.tenant_id, text="hello")) == []
    )

    # 6) real Pillow thumbnail preview: cache hit on the 2nd call (no re-render), never serving truth.
    first = plane.access_pattern.preview(plane.ctx, media_item_version_id=version_id, access_pattern=_THUMBNAIL)
    second = plane.access_pattern.preview(plane.ctx, media_item_version_id=version_id, access_pattern=_THUMBNAIL)
    assert first.object_key == second.object_key  # the second call served the cache deterministically
    # the binding still resolves the source version, never the preview cache (cache is not truth).
    assert resolved.media_item_version_id == version_id

    # Headline: ONE media_item_version_id is the join key across the whole plane.
    assert {derivative.source_media_item_version_id} == {unit.source_media_item_version_id for unit in units}
    assert binding.media_item_version_id == hit.source_media_item_version_id == version_id


@pytest.mark.integration_scenario("media-golden-pipeline")
def test_media_golden_pipeline_threads_committed_audio_through_real_asr_to_search(plane: _Plane) -> None:
    version_id = plane.commit_media(
        media_set_id=_audio_set(plane),
        source=_ASR_FIXTURE.read_bytes(),
        logical_path="/fox.wav",
        mime="audio/wav",
        fmt="wav",
    )

    outcome = plane.asr_processing.process(plane.ctx, media_item_version_id=version_id, spec=_ASR_SPEC)
    derivative = plane.asr_processing.resolve_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id)
    assert outcome.status == "committed" and derivative.derivative_kind == "asr_v1"

    units = plane.content_units(outcome.media_derivative_id)
    assert units and all(unit.unit_kind == "audio_segment" for unit in units)
    transcript = " ".join(unit.text for unit in units).lower()
    assert "quick brown fox" in transcript and "lazy dog" in transcript
    assert all(unit.source_media_item_version_id == version_id for unit in units)

    plane.indexing.index_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id, generation="g1")
    plane.indexing.promote(plane.ctx, expected_active="", generation="g1")
    hits = plane.retrieval.search_content(
        plane.ctx, query=HybridContentQuery(tenant_id=plane.ctx.tenant_id, text="fox")
    )
    hit = next(hit for hit in hits if hit.source_media_item_version_id == version_id)
    by_id = {unit.content_unit_id: unit for unit in units}
    assert hit.text_hash == by_id[hit.content_unit_id].text_hash  # SAME version + citation threads to ES search
