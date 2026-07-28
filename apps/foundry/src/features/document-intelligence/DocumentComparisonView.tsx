import {
  AlertTriangle,
  BoxSelect,
  CheckCircle2,
  Clock3,
  Cpu,
  FileLock2,
  LoaderCircle,
  ScanText,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  documentComparisonEvidence,
  type DocumentComparisonResult,
} from "./document-lab-comparison-model";

interface DocumentComparisonViewProps {
  results: readonly DocumentComparisonResult[];
  sourceVersionId: string;
  activeRunId: string | null;
  onSelect: (result: DocumentComparisonResult) => void;
}

export function DocumentComparisonView({
  results,
  sourceVersionId,
  activeRunId,
  onSelect,
}: DocumentComparisonViewProps) {
  return (
    <div
      className="flex h-full min-h-0 flex-col bg-[#F4F6F8]"
      aria-label="Document extraction strategy comparison"
    >
      <div className="flex h-8 shrink-0 items-center gap-2 border-b border-[#D3D8DE] bg-white px-3 text-[9px]">
        <FileLock2 className="size-3.5 text-[#137CBD]" />
        <span className="font-semibold">Same committed source</span>
        <span
          className="max-w-[360px] truncate font-mono text-[#5F6B7C]"
          title={sourceVersionId}
        >
          {sourceVersionId || "media version required"}
        </span>
        <span className="ml-auto text-[#738091]">
          카드를 선택하면 위 PDF의 page·bbox overlay가 같은 run으로 전환됩니다.
        </span>
      </div>
      <div className="grid min-h-0 flex-1 grid-cols-4 gap-2 overflow-auto p-2">
        {results.map((result) => (
          <ComparisonCard
            key={result.id}
            result={result}
            isActive={result.run?.id === activeRunId}
            onSelect={() => onSelect(result)}
          />
        ))}
      </div>
    </div>
  );
}

function ComparisonCard({
  result,
  isActive,
  onSelect,
}: {
  result: DocumentComparisonResult;
  isActive: boolean;
  onSelect: () => void;
}) {
  const evidence = documentComparisonEvidence(result);
  const status = comparisonStatus(result);
  const canSelect =
    result.run !== null && ["SUCCEEDED", "PARTIAL"].includes(status);
  return (
    <article
      data-testid={`document-comparison-card-${result.id}`}
      className={cn(
        "flex min-w-[220px] flex-col border bg-white",
        isActive
          ? "border-[#137CBD] ring-1 ring-[#137CBD]"
          : "border-[#C8CED6]",
      )}
    >
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[#E2E6EA] px-2">
        <span
          className={cn(
            "h-5 w-1 shrink-0",
            result.id === "raw"
              ? "bg-[#5C7080]"
              : result.id === "ocr"
                ? "bg-[#8F3985]"
                : result.id === "layout"
                  ? "bg-[#137CBD]"
                  : "bg-[#D9822B]",
          )}
        />
        <div className="min-w-0">
          <h3 className="truncate text-[10px] font-semibold">{result.label}</h3>
          <p className="truncate text-[8px] text-[#738091]">{result.detail}</p>
        </div>
        <ComparisonStatus status={status} />
      </div>

      <div className="grid grid-cols-2 border-b border-[#E2E6EA] bg-[#F8F9FA]">
        <EvidenceCell
          icon={<ScanText className="size-3" />}
          label="Pages"
          value={
            evidence.pageNumbers.length > 0
              ? evidence.pageNumbers.join(", ")
              : "—"
          }
        />
        <EvidenceCell
          icon={<BoxSelect className="size-3" />}
          label="Exact bbox"
          value={`${evidence.bboxCount}/${evidence.blockCount}`}
        />
        <EvidenceCell
          icon={<Clock3 className="size-3" />}
          label="Latency"
          value={
            evidence.latencyMs === null
              ? "not emitted"
              : formatDuration(evidence.latencyMs)
          }
        />
        <EvidenceCell
          icon={<Cpu className="size-3" />}
          label="Tokens / cost"
          value={tokenCostLabel(
            evidence.inputTokens,
            evidence.outputTokens,
            evidence.estimatedCostUsd,
          )}
        />
      </div>

      <div className="min-h-0 flex-1 space-y-2 p-2">
        <div>
          <div className="text-[8px] font-semibold tracking-[0.04em] text-[#738091] uppercase">
            Model evidence
          </div>
          <div className="mt-1 truncate font-mono text-[8px] text-[#303742]">
            {evidence.model
              ? `${evidence.provider ?? "provider"} · ${evidence.model}`
              : "No model call"}
          </div>
          {evidence.promptVersionId ? (
            <div className="truncate font-mono text-[8px] text-[#738091]">
              prompt={evidence.promptVersionId}
            </div>
          ) : null}
        </div>
        <div>
          <div className="text-[8px] font-semibold tracking-[0.04em] text-[#738091] uppercase">
            First result
          </div>
          <p className="mt-1 line-clamp-3 text-[9px] leading-3.5 text-[#5F6B7C]">
            {comparisonMessage(result, evidence.sample)}
          </p>
        </div>
      </div>

      <div className="border-t border-[#E2E6EA] p-2">
        <Button
          type="button"
          variant={isActive ? "default" : "outline"}
          disabled={!canSelect}
          aria-pressed={isActive}
          className="h-7 w-full rounded-[2px] text-[9px]"
          onClick={onSelect}
        >
          {isActive ? "Shown on canvas" : "Show result on canvas"}
        </Button>
      </div>
    </article>
  );
}

function EvidenceCell({
  icon,
  label,
  value,
}: {
  icon: React.ReactNode;
  label: string;
  value: string;
}) {
  return (
    <div className="min-w-0 border-r border-b border-[#E2E6EA] p-1.5">
      <div className="flex items-center gap-1 text-[8px] text-[#738091]">
        {icon}
        {label}
      </div>
      <div className="mt-0.5 truncate font-mono text-[9px]" title={value}>
        {value}
      </div>
    </div>
  );
}

function ComparisonStatus({ status }: { status: string }) {
  const isSuccess = status === "SUCCEEDED" || status === "PARTIAL";
  const isFailure = status === "FAILED" || status === "UNAVAILABLE";
  return (
    <span
      className={cn(
        "ml-auto flex items-center gap-1 rounded px-1 py-0.5 font-mono text-[8px]",
        isSuccess
          ? "bg-[#E7F6EC] text-[#0F6B3E]"
          : isFailure
            ? "bg-[#FDECEC] text-[#A82A2A]"
            : "bg-[#E1F3F8] text-[#0E5A78]",
      )}
    >
      {isSuccess ? (
        <CheckCircle2 className="size-2.5" />
      ) : isFailure ? (
        <AlertTriangle className="size-2.5" />
      ) : (
        <LoaderCircle className="size-2.5 animate-spin" />
      )}
      {status}
    </span>
  );
}

function comparisonStatus(result: DocumentComparisonResult): string {
  if (result.unavailableReason) return "UNAVAILABLE";
  if (result.error) return "FAILED";
  return String(result.run?.status ?? "QUEUED").toUpperCase();
}

function comparisonMessage(
  result: DocumentComparisonResult,
  sample: string,
): string {
  if (result.unavailableReason) return result.unavailableReason;
  if (result.error) return result.error;
  if (sample) return sample;
  const runError = previewErrorMessage(result.run?.error);
  if (runError) return runError;
  return result.run ? "Run completed without preview rows." : "Preview queued.";
}

function previewErrorMessage(value: unknown): string | null {
  if (typeof value === "string" && value) return value;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const error = value as Record<string, unknown>;
  const details = error.details;
  if (typeof details === "object" && details !== null && !Array.isArray(details)) {
    const adapterFailure = (details as Record<string, unknown>).adapterFailure;
    if (
      typeof adapterFailure === "object" &&
      adapterFailure !== null &&
      !Array.isArray(adapterFailure)
    ) {
      const failureDetails = (adapterFailure as Record<string, unknown>).details;
      if (
        typeof failureDetails === "object" &&
        failureDetails !== null &&
        !Array.isArray(failureDetails)
      ) {
        const reason = (failureDetails as Record<string, unknown>).reason;
        if (typeof reason === "string" && reason) return reason;
      }
    }
  }
  return typeof error.message === "string" ? error.message : null;
}

function tokenCostLabel(
  inputTokens: number,
  outputTokens: number,
  estimatedCostUsd: number | null,
): string {
  const tokens =
    inputTokens || outputTokens ? `${inputTokens} in / ${outputTokens} out` : "—";
  return estimatedCostUsd === null
    ? `${tokens} · cost n/a`
    : `${tokens} · $${estimatedCostUsd.toFixed(4)}`;
}

function formatDuration(milliseconds: number): string {
  if (milliseconds < 1000) return `${milliseconds} ms`;
  return `${(milliseconds / 1000).toFixed(2)} s`;
}
