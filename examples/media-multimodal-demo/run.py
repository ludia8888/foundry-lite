"""Live multimodal demo for the Foundry-lite Media/Content Plane.

Runs the REAL engines that ship in the media plane against real inputs and
prints their real output — no fakes, no simulation:

  1. OCR    — real Tesseract              (image pixels -> text)
  2. ASR    — real faster-whisper          (audio -> time-coded subtitles)
  3. Video  — real ffprobe + real whisper  (container metadata + audio-track transcript)
  4. Search — real fastembed embeddings    (semantic ranking, not keyword match)

Run with ``pnpm demo:media-multimodal`` (or
``PYTHONPATH=.:libs uv run python examples/media-multimodal-demo/run.py``).

Requires the system binaries the media plane uses (``tesseract``, ``ffmpeg``)
and downloads the small Whisper/embedding models on first run, exactly as the
``ocr-tesseract`` / ``asr-whisper`` / ``ffprobe`` / fastembed profiles do in
production. The CI golden pipeline (``tests/integration/test_media_golden_pipeline_live.py``)
exercises the same engines end-to-end through the composed runtime (storage ->
processing -> ontology binding -> Elasticsearch -> search -> preview).
"""

from __future__ import annotations

import math
import tempfile
from collections.abc import Sequence
from importlib import import_module
from pathlib import Path

from foundry_lite.application.ports.embedding_model import EmbeddingVector
from foundry_lite.infrastructure.adapters.asr_processor import _faster_whisper_asr_engine
from foundry_lite.infrastructure.adapters.local_embedding import _fastembed_embedding_engine
from foundry_lite.infrastructure.adapters.ocr_processor import _tesseract_ocr_engine
from foundry_lite.infrastructure.adapters.video_probe_processor import _ffprobe_video_probe_runner

DATA = Path(__file__).parent / "data"
_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial.ttf",
)


def _banner(title: str) -> None:
    print("\n" + "=" * 72 + f"\n  {title}\n" + "=" * 72)


def _load_font(pil_image_font: object, size: int) -> object:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return pil_image_font.truetype(path, size)  # type: ignore[attr-defined]
    return pil_image_font.load_default()  # type: ignore[attr-defined]


def demo_ocr() -> None:
    _banner("1) OCR  -  real Tesseract  (image pixels -> text)")
    pil_image = import_module("PIL.Image")
    pil_draw = import_module("PIL.ImageDraw")
    pil_font = import_module("PIL.ImageFont")
    image = pil_image.new("RGB", (560, 120), "white")
    pil_draw.Draw(image).text((20, 35), "INVOICE 42  TOTAL 1234", fill="black", font=_load_font(pil_font, 40))
    path = Path(tempfile.mkstemp(suffix=".png")[1])
    image.save(path)
    print("   input : a 560x120 PNG rendered with 'INVOICE 42  TOTAL 1234'")
    recognized = " ".join(_tesseract_ocr_engine(str(path))).strip()
    print(f"   OUTPUT: {recognized!r}")


def demo_asr() -> None:
    _banner("2) ASR  -  real faster-whisper  (audio -> time-coded subtitles)")
    audio = DATA / "quick_brown_fox.wav"
    print(f"   input : {audio.name}  (spoken audio)")
    for segment in _faster_whisper_asr_engine(str(audio)):
        print(f"   OUTPUT: [{segment.start_ms:>5}ms - {segment.end_ms:>5}ms]  {segment.text.strip()!r}")


def demo_video() -> None:
    _banner("3) VIDEO  -  real ffprobe + real whisper on the audio track")
    video = DATA / "quick_brown_fox.mp4"
    print(f"   input : {video.name}")
    probe = _ffprobe_video_probe_runner(str(video))
    print(
        f"   OUTPUT (ffprobe): codec={probe.video_codec} {probe.width}x{probe.height} "
        f"dur={probe.duration_seconds:.2f}s has_audio={probe.has_audio}"
    )
    transcript = " ".join(segment.text for segment in _faster_whisper_asr_engine(str(video))).strip()
    print(f"   OUTPUT (video -> subtitles): {transcript!r}")


def _cosine(left: EmbeddingVector, right: EmbeddingVector) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norm


def demo_semantic_search() -> None:
    _banner("4) SEMANTIC SEARCH  -  real fastembed  (meaning, not keywords)")
    docs: Sequence[str] = (
        "A golden retriever is a friendly dog breed.",
        "The sedan has a powerful petrol engine.",
        "Photosynthesis converts sunlight into energy.",
    )
    query = "puppy and canine pets"
    doc_vectors = _fastembed_embedding_engine(list(docs))
    query_vector = _fastembed_embedding_engine([query])[0]
    print(f"   query : {query!r}   (shares NO words with the dog sentence)")
    ranked = sorted(
        ((_cosine(query_vector, vector), doc) for vector, doc in zip(doc_vectors, docs, strict=True)), reverse=True
    )
    for rank, (score, doc) in enumerate(ranked, start=1):
        marker = "   <-- top hit (pure semantic)" if rank == 1 else ""
        print(f"   OUTPUT rank {rank}: cos={score:.3f}  {doc!r}{marker}")


def main() -> None:
    demo_ocr()
    demo_asr()
    demo_video()
    demo_semantic_search()
    _banner("ALL FOUR MODALITIES RAN ON REAL ENGINES")


if __name__ == "__main__":
    main()
