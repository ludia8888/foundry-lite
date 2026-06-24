# Media Multimodal Demo

This demo runs the **real** engines that ship in the Foundry-lite Media/Content
Plane against real inputs and prints their real output — no fakes, no simulation:

| #   | Modality        | Real engine       | Proves                                      |
| --- | --------------- | ----------------- | ------------------------------------------- |
| 1   | OCR             | Tesseract         | image pixels → text                         |
| 2   | ASR             | faster-whisper    | audio → time-coded subtitles                |
| 3   | Video           | ffprobe + whisper | container metadata + audio-track transcript |
| 4   | Semantic search | fastembed         | meaning-based ranking (no keyword overlap)  |

Run it with:

```bash
pnpm demo:media-multimodal
```

(equivalently `PYTHONPATH=.:libs uv run python examples/media-multimodal-demo/run.py`).

## Requirements

- System binaries the media plane uses: `tesseract` and `ffmpeg`/`ffprobe`.
- First run downloads the small Whisper (`tiny`) and embedding (`bge-small-en-v1.5`)
  models to `~/.cache/huggingface`, exactly as the `asr-whisper` / fastembed
  profiles do in production. They are pure-wheel (ONNX / ctranslate2 — no torch).

## Inputs

`data/quick_brown_fox.wav` (spoken audio) and `data/quick_brown_fox.mp4`
(a tiny H.264 + AAC clip narrating the same pangram). The OCR input is rendered
at runtime with Pillow.

## How this relates to CI

The demo calls the real engine functions directly. The CI **golden pipeline**
(`tests/integration/test_media_golden_pipeline_live.py`, run by
`pnpm quality:media-active-covered`) exercises the same engines end-to-end
through the composed runtime — raw upload → processing → Ontology
`media_reference` binding → live Elasticsearch index → hybrid search (with
`text_hash` citation + tenant ACL) → preview cache — asserting that the same
`media_item_version_id` threads consistently from upstream to downstream, with
the DB COMMITTED version as the only serving truth.
