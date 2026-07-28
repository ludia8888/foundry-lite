import type { PipelinePreviewRun } from "@foundry-lite/sdk";
import {
  AlertTriangle,
  Braces,
  CheckCircle2,
  Clock3,
  FileJson2,
  GitCompareArrows,
  ListTree,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";

import {
  documentLabMetrics,
  type DocumentLabBlock,
} from "./document-lab-model";
import type { DocumentComparisonResult } from "./document-lab-comparison-model";
import type { CitationNavigationResolution } from "./citation-evidence-model";
import { DocumentComparisonView } from "./DocumentComparisonView";
import type {
  CitationVerificationState,
  DocumentEvidenceViewMode,
} from "./DocumentPreviewCanvas";

interface DocumentResultDockProps {
  run: PipelinePreviewRun | null;
  blocks: readonly DocumentLabBlock[];
  selectedBlockId: string | null;
  onSelectBlock: (blockId: string) => void;
  viewMode?: DocumentEvidenceViewMode;
  citationState?: CitationVerificationState;
  citationResolution?: CitationNavigationResolution | null;
  comparisonResults?: readonly DocumentComparisonResult[];
  comparisonSourceVersionId?: string;
  activeComparisonRunId?: string | null;
  comparisonFocusToken?: number;
  onSelectComparison?: (result: DocumentComparisonResult) => void;
}

export function DocumentResultDock({
  run,
  blocks,
  selectedBlockId,
  onSelectBlock,
  viewMode = "lab",
  citationState = "idle",
  citationResolution = null,
  comparisonResults = [],
  comparisonSourceVersionId = "",
  activeComparisonRunId = null,
  comparisonFocusToken = 0,
  onSelectComparison = () => undefined,
}: DocumentResultDockProps) {
  const [activeTab, setActiveTab] = useState("result");
  const metrics = useMemo(() => documentLabMetrics(blocks), [blocks]);
  const selected =
    blocks.find((block) => block.id === selectedBlockId) ?? blocks[0] ?? null;
  const isCitation = viewMode === "verified-citation";
  const status = isCitation
    ? citationStateLabel(citationState)
    : String(run?.status ?? "READY").toUpperCase();
  const isSuccess = status === "SUCCEEDED" || status === "PARTIAL";
  const isVerified = status === "VERIFIED";
  useEffect(() => {
    if (comparisonResults.length > 0) setActiveTab("comparison");
  }, [comparisonFocusToken, comparisonResults.length]);

  return (
    <section
      className={cn(
        "flex min-h-[170px] shrink-0 flex-col border-t border-[#B8C0CC] bg-white",
        !isCitation && comparisonResults.length > 0
          ? "h-[286px]"
          : "h-[230px]",
      )}
    >
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="flex min-h-0 flex-1 flex-col"
      >
        <div className="flex h-9 shrink-0 items-center border-b border-[#D3D8DE] px-2">
          <TabsList className="h-8 rounded-none bg-transparent p-0">
            <DockTab value="result" icon={<ListTree className="size-3" />}>
              {isCitation ? "Verified location" : "Extraction result"}
            </DockTab>
            {!isCitation ? (
              <DockTab value="markdown" icon={<FileJson2 className="size-3" />}>
                Markdown
              </DockTab>
            ) : null}
            <DockTab value="raw" icon={<Braces className="size-3" />}>
              Raw
            </DockTab>
            {!isCitation ? (
            <DockTab value="metrics" icon={<Clock3 className="size-3" />}>
              Quality & cost
            </DockTab>
            ) : null}
            {!isCitation && comparisonResults.length > 0 ? (
              <DockTab
                value="comparison"
                icon={<GitCompareArrows className="size-3" />}
              >
                Compare {comparisonResults.length}
              </DockTab>
            ) : null}
          </TabsList>
          <div className="ml-auto flex items-center gap-2 text-[9px]">
            <span
              className={cn(
                "flex items-center gap-1 rounded px-1.5 py-0.5 font-medium",
                isSuccess || isVerified
                  ? "bg-[#E7F6EC] text-[#0F6B3E]"
                  : status === "FAILED"
                    ? "bg-[#FDECEC] text-[#A82A2A]"
                    : "bg-[#EDEFF2] text-[#5F6B7C]",
              )}
            >
              {isSuccess || isVerified ? (
                <CheckCircle2 className="size-3" />
              ) : status === "FAILED" ? (
                <AlertTriangle className="size-3" />
              ) : null}
              {status}
            </span>
            {isCitation ? (
              <>
                <span className="font-mono text-[#5F6B7C]">
                  source=committed
                </span>
                <span className="font-mono text-[#5F6B7C]">
                  coordinates=server-verified
                </span>
              </>
            ) : (
              <>
                <span className="font-mono text-[#5F6B7C]">
                  commitForbidden={String(run?.commitForbidden ?? true)}
                </span>
                <span className="font-mono text-[#5F6B7C]">
                  serving={String(run?.servingVersionCreated ?? false)}
                </span>
              </>
            )}
          </div>
        </div>

        <TabsContent value="result" className="mt-0 min-h-0 flex-1">
          <div className="grid h-full grid-cols-[320px_minmax(0,1fr)]">
            <div className="overflow-y-auto border-r border-[#D3D8DE]">
              {blocks.length === 0 ? (
                <EmptyDockMessage
                  text={
                    isCitation
                      ? "서버 검증이 끝나면 정확한 page와 bbox가 표시됩니다."
                      : "미리보기를 실행하면 추출 block이 표시됩니다."
                  }
                />
              ) : (
                blocks.map((block, index) => (
                  <button
                    key={block.id}
                    type="button"
                    className={cn(
                      "flex w-full items-start gap-2 border-b border-[#E5E8EB] px-3 py-2 text-left hover:bg-[#F6F7F9]",
                      block.id === selectedBlockId && "bg-[#E1F3F8]",
                    )}
                    onClick={() => onSelectBlock(block.id)}
                  >
                    <span className="mt-0.5 rounded bg-[#EDEFF2] px-1 font-mono text-[8px] text-[#5F6B7C]">
                      p{block.pageNumber}
                    </span>
                    <span className="min-w-0">
                      <span className="block text-[10px] font-medium">
                        {blockRole(block, index)}
                      </span>
                      <span className="mt-0.5 line-clamp-2 block text-[9px] leading-3.5 text-[#738091]">
                        {block.text}
                      </span>
                    </span>
                  </button>
                ))
              )}
            </div>
            <div className="min-w-0 overflow-auto p-3">
              {selected ? (
                <SelectedBlock block={selected} isCitation={isCitation} />
              ) : (
                <EmptyDockMessage
                  text={
                    isCitation
                      ? "검증된 Content Unit을 선택하면 source locator와 bbox를 함께 봅니다."
                      : "block을 선택하면 text, structure, bbox, semantic output을 함께 봅니다."
                  }
                />
              )}
            </div>
          </div>
        </TabsContent>

        {!isCitation ? (
          <TabsContent value="markdown" className="mt-0 min-h-0 flex-1 overflow-auto p-4">
            {blocks.length > 0 ? (
              <pre className="whitespace-pre-wrap font-sans text-[11px] leading-5 text-[#303742]">
                {blocks.map(markdownForBlock).join("\n\n")}
              </pre>
            ) : (
              <EmptyDockMessage text="아직 Markdown 결과가 없습니다." />
            )}
          </TabsContent>
        ) : null}

        <TabsContent value="raw" className="mt-0 min-h-0 flex-1 overflow-auto bg-[#111820] p-3">
          <pre className="font-mono text-[9px] leading-4 text-[#D8E1EA]">
            {JSON.stringify(
              isCitation
                ? citationResolution ?? { status: citationStateLabel(citationState) }
                : run ?? { status: "READY" },
              null,
              2,
            )}
          </pre>
        </TabsContent>

        {!isCitation ? (
          <TabsContent value="metrics" className="mt-0 min-h-0 flex-1 overflow-auto p-4">
            <div className="grid grid-cols-6 gap-2">
              <MetricCard label="Blocks" value={metrics.itemCount} />
              <MetricCard label="Pages" value={metrics.pageCount} />
              <MetricCard label="Model calls" value={metrics.modelCalls} />
              <MetricCard label="Input tokens" value={metrics.inputTokens} />
              <MetricCard label="Output tokens" value={metrics.outputTokens} />
              <MetricCard label="Row errors" value={metrics.errorCount} />
            </div>
            <div className="mt-3 grid grid-cols-3 gap-2 text-[9px]">
              <EvidenceCard
                label="Quality"
                value={
                  blocks.length > 0
                    ? "Evidence-linked block output"
                    : "Run required"
                }
              />
              <EvidenceCard
                label="Elapsed time"
                value="runtime timing not emitted"
              />
              <EvidenceCard
                label="Estimated cost"
                value="provider pricing not emitted"
              />
            </div>
          </TabsContent>
        ) : null}

        {!isCitation && comparisonResults.length > 0 ? (
          <TabsContent value="comparison" className="mt-0 min-h-0 flex-1">
            <DocumentComparisonView
              results={comparisonResults}
              sourceVersionId={comparisonSourceVersionId}
              activeRunId={activeComparisonRunId}
              onSelect={onSelectComparison}
            />
          </TabsContent>
        ) : null}
      </Tabs>
    </section>
  );
}

function citationStateLabel(state: CitationVerificationState): string {
  switch (state) {
    case "resolving":
      return "VERIFYING";
    case "verified":
      return "VERIFIED";
    case "failed":
      return "FAILED";
    default:
      return "READY";
  }
}

function DockTab({
  value,
  icon,
  children,
}: {
  value: string;
  icon: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <TabsTrigger
      value={value}
      className="h-8 rounded-none border-b-2 border-transparent px-3 text-[10px] data-[state=active]:border-[#137CBD] data-[state=active]:bg-transparent data-[state=active]:shadow-none"
    >
      {icon}
      {children}
    </TabsTrigger>
  );
}

function SelectedBlock({
  block,
  isCitation,
}: {
  block: DocumentLabBlock;
  isCitation: boolean;
}) {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <div>
        <div className="mb-1 text-[9px] font-semibold tracking-[0.5px] text-[#738091] uppercase">
          {isCitation ? "Evidence label" : "Extracted text"}
        </div>
        <p className="whitespace-pre-wrap text-[11px] leading-5">{block.text}</p>
      </div>
      <div className="grid grid-cols-2 gap-2">
        <JsonCard label="Structure" value={block.structure} />
        <JsonCard label="Bounding box" value={block.bbox} />
        <JsonCard label="Source locator" value={block.sourceLocator} />
        <JsonCard label="Semantic output" value={block.interpretation} />
      </div>
    </div>
  );
}

function JsonCard({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="min-w-0 rounded-[2px] border border-[#D3D8DE] bg-[#F6F7F9] p-2">
      <div className="mb-1 text-[8px] font-semibold tracking-[0.4px] text-[#738091] uppercase">
        {label}
      </div>
      <pre className="max-h-20 overflow-auto whitespace-pre-wrap font-mono text-[8px] leading-3 text-[#303742]">
        {value == null ? "—" : JSON.stringify(value, null, 2)}
      </pre>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-[2px] border border-[#D3D8DE] bg-[#F6F7F9] p-2">
      <div className="text-[8px] font-semibold tracking-[0.4px] text-[#738091] uppercase">
        {label}
      </div>
      <div className="mt-1 font-mono text-[18px] font-semibold">{value}</div>
    </div>
  );
}

function EvidenceCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[2px] border border-[#D3D8DE] p-2">
      <span className="font-semibold">{label}</span>
      <span className="ml-2 text-[#738091]">{value}</span>
    </div>
  );
}

function EmptyDockMessage({ text }: { text: string }) {
  return (
    <div className="flex h-full items-center justify-center p-4 text-center text-[10px] text-[#8A94A3]">
      {text}
    </div>
  );
}

function blockRole(block: DocumentLabBlock, index: number): string {
  const role = block.structure?.role ?? block.structure?.type;
  return typeof role === "string" ? role : `Block ${index + 1}`;
}

function markdownForBlock(block: DocumentLabBlock, index: number): string {
  const role = blockRole(block, index).toLowerCase();
  const prefix =
    role.includes("heading_1") || role === "h1" || role === "title"
      ? "# "
      : role.includes("heading_2") || role === "h2"
        ? "## "
        : role.includes("table")
          ? "**Table**\n\n"
          : "";
  const interpretation =
    block.interpretation == null
      ? ""
      : `\n\n\`\`\`json\n${JSON.stringify(block.interpretation, null, 2)}\n\`\`\``;
  return `${prefix}${block.text}${interpretation}`;
}
