"""Image, audio, and video processor composition for the default registry."""

from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.application.ports.vision_embedding_model import VisionEmbeddingModelAdapter
from foundry_lite.infrastructure.adapters.asr_processor import (
    AsrProcessorAdapter,
    _faster_whisper_asr_engine,
)
from foundry_lite.infrastructure.adapters.image_processor import ImageProcessorAdapter
from foundry_lite.infrastructure.adapters.ocr_processor import (
    OcrProcessorAdapter,
    _tesseract_ocr_engine,
)
from foundry_lite.infrastructure.adapters.video_probe_processor import (
    VideoProbeProcessorAdapter,
    VideoSceneFrameProcessorAdapter,
    VideoSceneVisionProcessorAdapter,
    _ffmpeg_scene_frame_extractor,
    _ffmpeg_scene_frame_paths,
    _ffprobe_video_probe_runner,
)


def default_av_processor_adapters(
    vision_embedding_model: VisionEmbeddingModelAdapter,
) -> tuple[MediaProcessorAdapter, ...]:
    return (
        OcrProcessorAdapter(ocr_engine=_tesseract_ocr_engine),
        ImageProcessorAdapter(),
        AsrProcessorAdapter(asr_engine=_faster_whisper_asr_engine),
        VideoProbeProcessorAdapter(probe_runner=_ffprobe_video_probe_runner),
        VideoSceneFrameProcessorAdapter(scene_frame_extractor=_ffmpeg_scene_frame_extractor),
        VideoSceneVisionProcessorAdapter(
            vision_embedding_model,
            scene_frame_path_extractor=_ffmpeg_scene_frame_paths,
        ),
    )
