import { FileText, Minus, Plus, ScanLine } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { cn } from "@/lib/utils";

import type { DocumentLabBlock } from "./document-lab-model";

type MediaPreviewKind = "media" | "pdf" | "image";
export type DocumentEvidenceViewMode = "lab" | "verified-citation";
export type CitationVerificationState =
  | "idle"
  | "resolving"
  | "verified"
  | "failed";

interface DocumentPreviewCanvasProps {
  blocks: readonly DocumentLabBlock[];
  sourceUrl: string | null;
  selectedBlockId: string | null;
  onSelectBlock: (blockId: string) => void;
  viewMode?: DocumentEvidenceViewMode;
  citationState?: CitationVerificationState;
}

export function DocumentPreviewCanvas({
  blocks,
  sourceUrl,
  selectedBlockId,
  onSelectBlock,
  viewMode = "lab",
  citationState = "idle",
}: DocumentPreviewCanvasProps) {
  const pages = useMemo(
    () =>
      Array.from(new Set(blocks.map((block) => block.pageNumber))).sort(
        (left, right) => left - right,
      ),
    [blocks],
  );
  const [selectedPage, setSelectedPage] = useState(pages[0] ?? 1);
  const [zoom, setZoom] = useState(86);
  const [previewKind, setPreviewKind] = useState<MediaPreviewKind>("media");
  useEffect(() => {
    if (pages.length > 0 && !pages.includes(selectedPage)) {
      setSelectedPage(pages[0]);
    }
  }, [pages, selectedPage]);
  useEffect(() => {
    setPreviewKind("media");
    if (!sourceUrl) return;
    const controller = new AbortController();
    void fetch(sourceUrl, { method: "HEAD", signal: controller.signal })
      .then((response) => {
        if (response.ok) {
          setPreviewKind(
            mediaPreviewKindFromContentType(
              response.headers.get("content-type"),
            ),
          );
        }
      })
      .catch(() => undefined);
    return () => controller.abort();
  }, [sourceUrl]);
  const visibleBlocks = blocks.filter(
    (block) => block.pageNumber === selectedPage,
  );
  const hasExactBoxes = visibleBlocks.some(
    (block) => normalizedBox(block) !== null,
  );
  const previewCopy = mediaPreviewCopy(
    previewKind,
    selectedPage,
    viewMode,
    citationState,
  );
  const selectedPageUrl = sourceUrl
    ? `${sourceUrl}#page=${selectedPage}&view=FitH&toolbar=0&navpanes=0&scrollbar=0`
    : null;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 bg-[#E8EBEF]">
      <PageRail
        pages={pages.length > 0 ? pages : [1]}
        selectedPage={selectedPage}
        previewKind={previewKind}
        onSelectPage={setSelectedPage}
      />
      <section
        className="relative flex min-h-0 min-w-0 flex-1 flex-col"
        aria-label={previewCopy.regionLabel}
      >
        <div className="flex h-9 shrink-0 items-center justify-between border-b border-[#C8CED6] bg-white px-3">
          <div className="flex items-center gap-2 text-[11px] text-[#5F6B7C]">
            <ScanLine className="size-3.5 text-[#137CBD]" />
            <span>{previewCopy.heading}</span>
            <span className="rounded bg-[#E1F3F8] px-1.5 py-0.5 text-[#0E5A78]">
              {previewCopy.location}
            </span>
          </div>
          <div className="flex items-center rounded-[2px] border border-[#B8C0CC] bg-[#F6F7F9]">
            <button
              type="button"
              aria-label="축소"
              className="flex size-6 items-center justify-center hover:bg-white"
              onClick={() => setZoom((value) => Math.max(55, value - 5))}
            >
              <Minus className="size-3" />
            </button>
            <span className="min-w-12 border-x border-[#D3D8DE] px-2 text-center font-mono text-[10px]">
              {zoom}%
            </span>
            <button
              type="button"
              aria-label="확대"
              className="flex size-6 items-center justify-center hover:bg-white"
              onClick={() => setZoom((value) => Math.min(150, value + 5))}
            >
              <Plus className="size-3" />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-auto p-6">
          <div
            className="relative mx-auto origin-top bg-white shadow-[0_2px_10px_rgba(17,24,32,0.18)] transition-[width]"
            style={{ width: `${Math.round(680 * (zoom / 100))}px` }}
          >
            <div className="relative aspect-[1/1.414] overflow-hidden border border-[#B8C0CC]">
              {selectedPageUrl ? (
                <iframe
                  key={selectedPageUrl}
                  src={selectedPageUrl}
                  title={previewCopy.frameTitle}
                  className="absolute inset-0 size-full border-0 bg-white"
                  onLoad={(event) =>
                    setPreviewKind(mediaPreviewKind(event.currentTarget))
                  }
                />
              ) : (
                <PageSkeleton blocks={visibleBlocks} />
              )}
              {visibleBlocks.map((block, index) => {
                const box = normalizedBox(block);
                if (box === null) return null;
                const isSelected = block.id === selectedBlockId;
                return (
                  <button
                    key={block.id}
                    type="button"
                    aria-label={`문서 block ${index + 1}: ${block.text.slice(0, 48)}`}
                    data-testid={
                      viewMode === "verified-citation"
                        ? "verified-citation-overlay"
                        : undefined
                    }
                    data-page-number={block.pageNumber}
                    className={cn(
                      "absolute border text-left transition-colors",
                      isSelected
                        ? "border-[#D9822B] bg-[#D9822B]/12 ring-1 ring-[#D9822B]"
                        : "border-[#137CBD]/80 bg-[#137CBD]/6 hover:bg-[#137CBD]/12",
                    )}
                    style={{
                      left: `${box.left}%`,
                      top: `${box.top}%`,
                      width: `${box.width}%`,
                      height: `${box.height}%`,
                    }}
                    onClick={() => onSelectBlock(block.id)}
                  >
                    <span
                      className={cn(
                        "absolute -top-4 left-0 rounded-t-[2px] px-1 py-0.5 font-mono text-[8px] text-white",
                        isSelected ? "bg-[#D9822B]" : "bg-[#137CBD]",
                      )}
                    >
                      {blockLabel(block, index)}
                    </span>
                  </button>
                );
              })}
              {visibleBlocks.length > 0 && !hasExactBoxes ? (
                <div className="pointer-events-none absolute top-2 right-2 rounded-[2px] border border-[#D3A442] bg-[#FFF8E5] px-2 py-1 font-mono text-[8px] text-[#775500]">
                  semantic-only · exact bbox unavailable
                </div>
              ) : null}
              {visibleBlocks.length === 0 ? (
                <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 text-[#8A94A3]">
                  <FileText className="size-9 stroke-[1.2]" />
                  <p className="max-w-sm text-center text-[12px]">
                    {previewCopy.emptyMessage}
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        </div>
      </section>
    </div>
  );
}

function PageRail({
  pages,
  selectedPage,
  previewKind,
  onSelectPage,
}: {
  pages: readonly number[];
  selectedPage: number;
  previewKind: MediaPreviewKind;
  onSelectPage: (page: number) => void;
}) {
  const railLabel =
    previewKind === "image"
      ? "이미지 항목"
      : previewKind === "pdf"
        ? "문서 페이지"
        : "미디어 항목";
  return (
    <aside
      className="w-[94px] shrink-0 overflow-y-auto border-r border-[#C8CED6] bg-[#F6F7F9] p-2"
      aria-label={railLabel}
    >
      <div className="space-y-2">
        {pages.map((page) => (
          <button
            key={page}
            type="button"
            aria-label={`${page} ${previewKind === "pdf" ? "페이지" : "항목"}`}
            aria-pressed={page === selectedPage}
            className={cn(
              "w-full rounded-[2px] border bg-white p-1 text-left shadow-sm",
              page === selectedPage
                ? "border-[#137CBD] ring-1 ring-[#137CBD]"
                : "border-[#C8CED6] hover:border-[#8A94A3]",
            )}
            onClick={() => onSelectPage(page)}
          >
            <div className="aspect-[1/1.414] bg-[linear-gradient(#E9EDF2_1px,transparent_1px)] bg-[length:100%_8px]" />
            <span className="mt-1 block text-center font-mono text-[9px] text-[#5F6B7C]">
              {page}
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}

function PageSkeleton({ blocks }: { blocks: readonly DocumentLabBlock[] }) {
  return (
    <div className="absolute inset-[8%_9%] space-y-3 opacity-55">
      <div className="h-3 w-2/3 rounded-sm bg-[#9FA9B7]" />
      <div className="h-1.5 w-1/3 rounded-sm bg-[#C2C8D0]" />
      {blocks.slice(0, 14).map((block, index) => (
        <div key={block.id} className="space-y-1.5">
          {index % 4 === 0 ? (
            <div className="h-2 w-1/2 rounded-sm bg-[#AEB6C1]" />
          ) : null}
          <div
            className="h-1 rounded-sm bg-[#D1D6DC]"
            style={{ width: `${62 + ((index * 13) % 34)}%` }}
          />
          <div
            className="h-1 rounded-sm bg-[#D1D6DC]"
            style={{ width: `${48 + ((index * 17) % 40)}%` }}
          />
        </div>
      ))}
    </div>
  );
}

function normalizedBox(
  block: DocumentLabBlock,
): { left: number; top: number; width: number; height: number } | null {
  const bbox = block.bbox;
  if (bbox) {
    const left = numberFrom(bbox, ["left", "x", "x0"]);
    const top = numberFrom(bbox, ["top", "y", "y0"]);
    const rawWidth =
      numberFrom(bbox, ["width"]) ??
      difference(
        numberFrom(bbox, ["right", "x1"]),
        left,
      );
    const rawHeight =
      numberFrom(bbox, ["height"]) ??
      difference(
        numberFrom(bbox, ["bottom", "y1"]),
        top,
      );
    if (
      left !== null &&
      top !== null &&
      rawWidth !== null &&
      rawHeight !== null
    ) {
      const pageWidth = numberFrom(bbox, ["pageWidth", "canvasWidth"]) ?? 100;
      const pageHeight =
        numberFrom(bbox, ["pageHeight", "canvasHeight"]) ?? 100;
      const scaleX = left <= 1 && rawWidth <= 1 ? 100 : 100 / pageWidth;
      const scaleY = top <= 1 && rawHeight <= 1 ? 100 : 100 / pageHeight;
      return clampBox({
        left: left * scaleX,
        top: top * scaleY,
        width: rawWidth * scaleX,
        height: rawHeight * scaleY,
      });
    }
  }
  return null;
}

function clampBox(box: {
  left: number;
  top: number;
  width: number;
  height: number;
}) {
  const left = Math.max(0, Math.min(96, box.left));
  const top = Math.max(0, Math.min(96, box.top));
  return {
    left,
    top,
    width: Math.max(2, Math.min(100 - left, box.width)),
    height: Math.max(1.5, Math.min(100 - top, box.height)),
  };
}

function numberFrom(
  value: Record<string, unknown>,
  keys: readonly string[],
): number | null {
  for (const key of keys) {
    const candidate = value[key];
    if (typeof candidate === "number" && Number.isFinite(candidate)) {
      return candidate;
    }
  }
  return null;
}

function difference(end: number | null, start: number | null): number | null {
  return end !== null && start !== null ? end - start : null;
}

function blockLabel(block: DocumentLabBlock, index: number): string {
  const role =
    typeof block.structure?.role === "string"
      ? block.structure.role
      : typeof block.structure?.type === "string"
        ? block.structure.type
        : block.raw.unitKind;
  return typeof role === "string" && role ? role : `block ${index + 1}`;
}

function mediaPreviewKind(frame: HTMLIFrameElement): MediaPreviewKind {
  try {
    return mediaPreviewKindFromContentType(
      frame.contentDocument?.contentType ?? null,
    );
  } catch {
    return "media";
  }
}

function mediaPreviewKindFromContentType(
  rawContentType: string | null,
): MediaPreviewKind {
  const contentType = rawContentType?.split(";", 1)[0].trim().toLowerCase() ?? "";
  if (contentType === "application/pdf") return "pdf";
  if (contentType.startsWith("image/")) return "image";
  return "media";
}

function mediaPreviewCopy(
  kind: MediaPreviewKind,
  item: number,
  viewMode: DocumentEvidenceViewMode,
  citationState: CitationVerificationState,
) {
  if (viewMode === "verified-citation") {
    const emptyMessage =
      citationState === "resolving"
        ? "서버가 signed citation과 immutable Content Unit을 다시 검증하고 있습니다."
        : citationState === "failed"
          ? "검증에 실패해 원문과 bounding box를 표시하지 않습니다."
          : "검증된 citation evidence가 없습니다.";
    return {
      heading: "Verified PDF citation ↔ immutable evidence",
      location:
        citationState === "verified" ? `page ${item}` : "verification required",
      frameTitle: `검증된 원본 PDF ${item} 페이지`,
      regionLabel: "검증된 PDF citation과 bounding box",
      emptyMessage,
    };
  }
  if (kind === "pdf") {
    return {
      heading: "PDF ↔ extraction synchronized view",
      location: `page ${item}`,
      frameTitle: `원본 PDF ${item} 페이지`,
      regionLabel: "문서와 bounding box 미리보기",
      emptyMessage:
        "미리보기를 실행하면 원본 media와 evidence가 표시됩니다.",
    };
  }
  if (kind === "image") {
    return {
      heading: "Image ↔ semantic interpretation view",
      location: `image ${item}`,
      frameTitle: `원본 이미지 ${item}`,
      regionLabel: "이미지와 semantic evidence 미리보기",
      emptyMessage:
        "미리보기를 실행하면 원본 media와 evidence가 표시됩니다.",
    };
  }
  return {
    heading: "Media ↔ governed interpretation view",
    location: `item ${item}`,
    frameTitle: `원본 미디어 ${item}`,
    regionLabel: "미디어와 evidence 미리보기",
    emptyMessage:
      "미리보기를 실행하면 원본 media와 evidence가 표시됩니다.",
  };
}
