from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Literal, cast
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.content_index import HybridContentQuery
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord
from foundry_lite.application.ports.media_processor import MediaProcessorAdapter, ProcessorSpec
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.indexing import MediaIndexingService
from foundry_lite.application.services.media.processing import MediaProcessingService
from foundry_lite.application.services.media.retrieval import DefaultContentRetrievalService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.asr_processor import AsrProcessorAdapter, _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.local_completion import LocalCompletionAdapter
from foundry_lite.infrastructure.adapters.local_content_index import LocalContentIndexAdapter
from foundry_lite.infrastructure.adapters.local_embedding import LocalEmbeddingAdapter
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.adapters.ocr_processor import OcrProcessorAdapter, _tesseract_ocr_engine
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoProbeProcessorAdapter,
    _ffprobe_video_probe_runner,
)
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaDerivativeRepository, SqlAlchemyMediaRepository
from foundry_lite.security.policy import PolicyService
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = ROOT / "artifacts" / "simulations" / "live_media_byte_proof.json"
DEFAULT_STORAGE_ROOT = ROOT / "artifacts" / "simulations" / "live-media-byte-runtime"
USER_AGENT = "Foundry-lite-live-media-byte-proof/1.0"
READ_CHUNK_BYTES = 1024 * 1024

MediaKind = Literal["image", "audio", "video"]
ProofStatus = Literal["passed", "failed"]


@dataclass(frozen=True)
class LiveMediaAsset:
    name: str
    media_kind: MediaKind
    source_url: str
    mime_type: str
    media_format: str
    processor_spec: ProcessorSpec
    expected_terms: tuple[str, ...]
    max_bytes: int


@dataclass(frozen=True)
class DownloadedAsset:
    body: bytes
    content_type: str
    content_length: int | None


@dataclass(frozen=True)
class MediaAssetProof:
    name: str
    media_kind: MediaKind
    status: ProofStatus
    source_url: str
    byte_size: int
    sha256: str
    content_type: str
    media_item_version_id: str
    media_derivative_id: str
    derivative_kind: str
    content_unit_count: int
    content_text_preview: str
    verified_search_terms: tuple[str, ...]
    media_run_status: str
    elapsed_seconds: float


@dataclass(frozen=True)
class MediaByteConfig:
    output: Path
    storage_root: Path


class _FakeRuntime:
    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None: ...

    def _outbox(
        self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object
    ) -> str | None:
        return event_type


@dataclass(frozen=True)
class _MediaPlane:
    ctx: RequestContext
    engine: Engine
    derivative_repository: SqlAlchemyMediaDerivativeRepository
    catalog: MediaCatalogService
    upload: MediaUploadService
    transaction: MediaTransactionService
    ocr_processing: MediaProcessingService
    asr_processing: MediaProcessingService
    video_processing: MediaProcessingService
    indexing: MediaIndexingService
    retrieval: DefaultContentRetrievalService

    def commit_media(self, asset: LiveMediaAsset, source: bytes) -> str:
        media_set = self.catalog.create_media_set(
            self.ctx,
            MediaSetSpec(
                namespace="live",
                name=f"{asset.name}_set",
                schema_type=asset.media_kind,
                primary_format=asset.media_format,
                allowed_input_formats=(asset.media_format,),
                classification="public",
            ),
        )
        logical_path = f"/internet/{asset.name}.{asset.media_format}"
        transaction_id = self.transaction.open(
            self.ctx, media_set_id=media_set.media_set_id, idempotency_key=f"commit-{asset.name}"
        )
        session = self.upload.initiate(
            self.ctx,
            media_set_id=media_set.media_set_id,
            logical_path=logical_path,
            supplied_mime_type=asset.mime_type,
        )
        staged = self.upload.complete(
            self.ctx,
            inputs=MediaUploadInput(
                media_set_id=media_set.media_set_id,
                media_transaction_id=transaction_id,
                logical_path=logical_path,
                supplied_mime_type=asset.mime_type,
                schema_type=asset.media_kind,
                format=asset.media_format,
                security_envelope={"tenantId": self.ctx.tenant_id, "classification": "public"},
            ),
            upload=session,
            source=io.BytesIO(source),
        )
        self.transaction.commit(self.ctx, media_transaction_id=transaction_id)
        return staged.media_item_version_id

    def content_units(self, derivative_id: str) -> list[ContentUnitRecord]:
        with self.engine.begin() as conn:
            return list(
                self.derivative_repository.get_content_units(
                    transaction=conn, tenant_id=self.ctx.tenant_id, derivative_id=derivative_id
                )
            )


def main(argv: list[str] | None = None) -> int:
    config = _config(_parser().parse_args(argv))
    evidence = run_media_byte_proof(config)
    output = json.dumps(evidence, indent=2, sort_keys=True)
    config.output.parent.mkdir(parents=True, exist_ok=True)
    config.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0 if evidence["status"] == "passed" else 1


def run_media_byte_proof(config: MediaByteConfig) -> dict[str, object]:
    plane = _build_media_plane(config.storage_root)
    proofs: list[MediaAssetProof] = []
    active_generation = ""
    for asset in _live_media_assets():
        proof = _run_asset_proof(plane, asset, expected_active_generation=active_generation)
        proofs.append(proof)
        active_generation = asset.name
    status = "passed" if all(proof.status == "passed" for proof in proofs) else "failed"
    return {
        "status": status,
        "generatedAt": round(time.time(), 3),
        "operatorIntent": (
            "Download public internet image/audio/video bytes, commit them as immutable media versions, "
            "process them through OCR/ASR/video probes, index derived content, and verify searchable evidence."
        ),
        "mediaAssetProofs": [asdict(proof) for proof in proofs],
    }


def _run_asset_proof(plane: _MediaPlane, asset: LiveMediaAsset, *, expected_active_generation: str) -> MediaAssetProof:
    started = time.monotonic()
    download = _download_asset(asset)
    body_hash = hashlib.sha256(download.body).hexdigest()
    version_id = plane.commit_media(asset, download.body)
    processing = _processing_service(plane, asset.media_kind)
    outcome = processing.process(plane.ctx, media_item_version_id=version_id, spec=asset.processor_spec)
    derivative = processing.resolve_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id)
    units = plane.content_units(outcome.media_derivative_id)
    plane.indexing.index_derivative(plane.ctx, media_derivative_id=outcome.media_derivative_id, generation=asset.name)
    plane.indexing.promote(plane.ctx, expected_active=expected_active_generation, generation=asset.name)
    verified_terms = _verified_search_terms(plane, asset.expected_terms, version_id)
    runs = processing.list_media_runs(plane.ctx, source_media_item_version_id=version_id)
    media_run_status = next(run.status for run in runs if run.media_derivative_id == outcome.media_derivative_id)
    return MediaAssetProof(
        name=asset.name,
        media_kind=asset.media_kind,
        status="passed" if set(verified_terms) == set(asset.expected_terms) else "failed",
        source_url=asset.source_url,
        byte_size=len(download.body),
        sha256=f"sha256:{body_hash}",
        content_type=download.content_type,
        media_item_version_id=version_id,
        media_derivative_id=outcome.media_derivative_id,
        derivative_kind=derivative.derivative_kind,
        content_unit_count=len(units),
        content_text_preview=_content_preview(units),
        verified_search_terms=verified_terms,
        media_run_status=media_run_status,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def _download_asset(asset: LiveMediaAsset) -> DownloadedAsset:
    request = Request(asset.source_url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=45) as response:  # noqa: S310  # nosec B310 - fixed public media URLs.
            content_length = _optional_int(response.headers.get("Content-Length"))
            if content_length is not None and content_length > asset.max_bytes:
                raise ValueError(f"{asset.name} exceeds max_bytes before download")
            body = _bounded_read(cast(BinaryIO, response), asset.max_bytes)
            return DownloadedAsset(
                body=body,
                content_type=str(response.headers.get("Content-Type") or ""),
                content_length=content_length,
            )
    except HTTPError as exc:
        raise RuntimeError(f"{asset.name} download failed with HTTP {exc.code}") from exc
    except URLError as exc:
        raise RuntimeError(f"{asset.name} download failed: {exc.reason}") from exc


def _bounded_read(response: BinaryIO, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = response.read(READ_CHUNK_BYTES)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > max_bytes:
            raise ValueError("download exceeded max_bytes")
        chunks.append(chunk)


def _build_media_plane(storage_root: Path) -> _MediaPlane:
    storage_root.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{storage_root / 'metadata.db'}", future=True)
    db.create_database(engine)
    media_repository = SqlAlchemyMediaRepository(engine)
    derivative_repository = SqlAlchemyMediaDerivativeRepository(engine)
    storage = LocalMediaStorageAdapter(storage_root / "media")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=media_repository)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=media_repository, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=media_repository, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    content_index = LocalContentIndexAdapter()
    embedding = LocalEmbeddingAdapter()
    indexing = MediaIndexingService(
        engine=engine,
        media_derivative_repository=derivative_repository,
        content_index_adapter=content_index,
        embedding_model_adapter=embedding,
    )
    indexing.bind_collaborators({"runtime_service": runtime})
    retrieval = DefaultContentRetrievalService(
        engine=engine,
        media_derivative_repository=derivative_repository,
        content_index_adapter=content_index,
        embedding_model_adapter=embedding,
        completion_model_adapter=LocalCompletionAdapter(),
    )
    return _MediaPlane(
        ctx=RequestContext(tenant_id="tenant-live-media-byte-proof", request_id="req-live-media-byte-proof"),
        engine=engine,
        derivative_repository=derivative_repository,
        catalog=catalog,
        upload=upload,
        transaction=transaction,
        ocr_processing=_processing(
            engine,
            media_repository,
            derivative_repository,
            storage,
            runtime,
            OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine),
        ),
        asr_processing=_processing(
            engine,
            media_repository,
            derivative_repository,
            storage,
            runtime,
            AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine),
        ),
        video_processing=_processing(
            engine,
            media_repository,
            derivative_repository,
            storage,
            runtime,
            VideoProbeProcessorAdapter(probe_runner=_ffprobe_video_probe_runner),
        ),
        indexing=indexing,
        retrieval=retrieval,
    )


def _processing(
    engine: Engine,
    media_repository: SqlAlchemyMediaRepository,
    derivative_repository: SqlAlchemyMediaDerivativeRepository,
    storage: LocalMediaStorageAdapter,
    runtime: _FakeRuntime,
    processor: MediaProcessorAdapter,
) -> MediaProcessingService:
    service = MediaProcessingService(
        engine=engine,
        policy=PolicyService(),
        media_repository=media_repository,
        media_derivative_repository=derivative_repository,
        media_storage=storage,
        media_processor=processor,
    )
    service.bind_collaborators({"runtime_service": runtime})
    return service


def _processing_service(plane: _MediaPlane, media_kind: MediaKind) -> MediaProcessingService:
    if media_kind == "image":
        return plane.ocr_processing
    if media_kind == "audio":
        return plane.asr_processing
    return plane.video_processing


def _verified_search_terms(plane: _MediaPlane, terms: Sequence[str], version_id: str) -> tuple[str, ...]:
    verified: list[str] = []
    for term in terms:
        hits = plane.retrieval.search_content(
            plane.ctx, query=HybridContentQuery(tenant_id=plane.ctx.tenant_id, text=term)
        )
        if any(hit.source_media_item_version_id == version_id for hit in hits):
            verified.append(term)
    return tuple(verified)


def _content_preview(units: Sequence[ContentUnitRecord]) -> str:
    text = " ".join(unit.text for unit in units).strip()
    return text[:500]


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _live_media_assets() -> tuple[LiveMediaAsset, ...]:
    return (
        LiveMediaAsset(
            name="commonsQuickBrownFoxPng",
            media_kind="image",
            source_url="https://upload.wikimedia.org/wikipedia/commons/f/f6/The_quick_brown_fox_jumps_over_the_lazy_dog.png",
            mime_type="image/png",
            media_format="png",
            processor_spec=ProcessorSpec(
                processor="ocr_v1", processor_version="1.0", model="tesseract", model_version="5.5.2"
            ),
            expected_terms=("quick", "fox"),
            max_bytes=1_000_000,
        ),
        LiveMediaAsset(
            name="commonsQuickBrownFoxOgg",
            media_kind="audio",
            source_url="https://upload.wikimedia.org/wikipedia/commons/a/a1/Audio_Sample_-_The_Quick_Brown_Fox_Jumps_Over_The_Lazy_Dog.ogg",
            mime_type="audio/ogg",
            media_format="ogg",
            processor_spec=ProcessorSpec(
                processor="asr_v1", processor_version="1.0", model="whisper", model_version="tiny"
            ),
            expected_terms=("quick", "fox"),
            max_bytes=2_000_000,
        ),
        LiveMediaAsset(
            name="mdnFlowerMp4",
            media_kind="video",
            source_url="https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
            mime_type="video/mp4",
            media_format="mp4",
            processor_spec=ProcessorSpec(
                processor="video_probe_v1", processor_version="1.0", model="ffprobe", model_version="8.0"
            ),
            expected_terms=("codec", "width"),
            max_bytes=3_000_000,
        ),
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download public media bytes and run them through media processors.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--storage-root", type=Path, default=DEFAULT_STORAGE_ROOT)
    return parser


def _config(args: argparse.Namespace) -> MediaByteConfig:
    return MediaByteConfig(output=cast(Path, args.output), storage_root=cast(Path, args.storage_root))


if __name__ == "__main__":
    raise SystemExit(main())
