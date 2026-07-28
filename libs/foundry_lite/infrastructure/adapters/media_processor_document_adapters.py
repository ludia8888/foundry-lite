"""Document processor composition for the default media registry."""

from foundry_lite.application.ports.media_processor import MediaProcessorAdapter
from foundry_lite.infrastructure.adapters.pdf_layout_processor import PdfLayoutProcessorAdapter
from foundry_lite.infrastructure.adapters.pdf_ocr_processor import (
    PdfOcrProcessorAdapter,
    pdf_ocr_model_version,
)
from foundry_lite.infrastructure.adapters.pdf_text_processor import PdfTextProcessorAdapter


def default_document_processor_adapters() -> tuple[MediaProcessorAdapter, ...]:
    return (
        PdfTextProcessorAdapter(),
        PdfLayoutProcessorAdapter(),
        PdfOcrProcessorAdapter(),
    )


def default_pdf_ocr_model_version() -> str:
    return pdf_ocr_model_version()
