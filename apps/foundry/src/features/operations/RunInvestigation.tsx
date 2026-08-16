import type {
  FoundryLiteOperationsRunRow,
  FoundryLitePromptArtifactReference,
} from "@foundry-lite/sdk/react";
import {
  useFoundryLiteProvidedOperationsInvestigation,
  useFoundryLiteProvidedPromptArtifact,
} from "@foundry-lite/sdk/react";
import { FileText, Search } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { EvidenceRow, SectionHeader } from "./OperationsPrimitives";
import {
  formatTimestamp,
  readRowString,
  runPhaseIntent,
  runPhaseLabel,
  runTypeLabel,
} from "./operations-format";

const RUN_TYPE_OPTIONS = [
  { value: "all", label: "전체" },
  { value: "source_exploration", label: "Source Explorer" },
  { value: "sync", label: "Sync" },
  { value: "transform", label: "Transform" },
  { value: "index", label: "Index" },
  { value: "action", label: "Action" },
  { value: "action_writeback", label: "Writeback" },
  { value: "materialization", label: "Materialization" },
  { value: "outbox", label: "Outbox" },
  { value: "dead_letter", label: "Dead Letter" },
  { value: "workflow", label: "Workflow" },
  { value: "ai", label: "AIP" },
  { value: "audit", label: "Audit" },
] as const;

const STATUS_OPTIONS = [
  { value: "all", label: "모든 상태" },
  { value: "failed", label: "실패만" },
  { value: "running", label: "실행 중" },
  { value: "succeeded", label: "성공" },
] as const;

type StatusFilter = (typeof STATUS_OPTIONS)[number]["value"];
type InvestigationReference = { key: string; value: string };

function runMatchesStatus(
  run: FoundryLiteOperationsRunRow,
  filter: StatusFilter,
): boolean {
  if (filter === "all") return true;
  if (filter === "failed") return run.isFailure;
  if (filter === "running") return run.isRunning || run.isPending;
  if (filter === "succeeded") return run.isSucceeded;
  return true;
}

function readInvestigationText(
  investigation: Record<string, unknown> | null | undefined,
  key: string,
): string | null {
  const value = investigation?.[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readInvestigationActions(
  investigation: Record<string, unknown> | null | undefined,
): string[] {
  const value = investigation?.suggestedActions;
  if (!Array.isArray(value)) return [];
  return value.filter(
    (item): item is string => typeof item === "string" && item.length > 0,
  );
}

function sourceEvidenceRows(
  evidence: Record<string, unknown> | null | undefined,
): InvestigationReference[] {
  if (!evidence) return [];
  return [
    ["syncName", "sync"],
    ["sourceType", "source type"],
    ["status", "status"],
    ["outputDatasetId", "dataset id"],
    ["transactionId", "transaction"],
    ["committedVersionId", "version"],
    ["datasetTransactionStatus", "tx status"],
    ["operationPath", "operation path"],
  ]
    .map(([key, label]) => evidenceReference(evidence, key, label))
    .filter((row): row is InvestigationReference => row !== null);
}

function evidenceReference(
  record: Record<string, unknown>,
  key: string,
  label: string,
): InvestigationReference | null {
  const value = record[key];
  if (value === null || value === undefined || value === "") return null;
  return {
    key: label,
    value:
      typeof value === "string" || typeof value === "number"
        ? String(value)
        : JSON.stringify(value),
  };
}

function investigationReferences(
  detail: Record<string, unknown> | null | undefined,
): InvestigationReference[] {
  if (!detail) return [];
  const references =
    detail.references && typeof detail.references === "object"
      ? (detail.references as Record<string, unknown>)
      : detail.investigation &&
          typeof detail.investigation === "object" &&
          "references" in detail.investigation &&
          typeof detail.investigation.references === "object"
        ? (detail.investigation.references as Record<string, unknown>)
        : {};
  return Object.entries(references)
    .filter(([, value]) => value !== null && value !== undefined)
    .slice(0, 8)
    .map(([key, value]) => ({
      key,
      value:
        typeof value === "string" || typeof value === "number"
          ? String(value)
          : JSON.stringify(value),
    }));
}

export function RunInvestigation({
  initialRunType,
  initialRunId,
}: {
  initialRunType?: string | null;
  initialRunId?: string | null;
}) {
  const [runTypeFilter, setRunTypeFilter] = useState<string>(
    initialRunType ?? "all",
  );
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [searchTerm, setSearchTerm] = useState(initialRunId ?? "");
  const [selection, setSelection] = useState<{
    runType: string;
    runId: string;
  } | null>(
    initialRunType && initialRunId
      ? { runType: initialRunType, runId: initialRunId }
      : null,
  );

  useEffect(() => {
    if (!initialRunType || !initialRunId) return;
    setRunTypeFilter(initialRunType);
    setSearchTerm(initialRunId);
    setSelection({ runType: initialRunType, runId: initialRunId });
  }, [initialRunType, initialRunId]);

  const investigation = useFoundryLiteProvidedOperationsInvestigation({
    runType: runTypeFilter === "all" ? undefined : runTypeFilter,
    limit: 200,
    selectedRunType: selection?.runType,
    selectedRunId: selection?.runId,
    // prompt artifact는 아래 PromptArtifactPanel에서 필요 시에만 로드한다.
    promptArtifactEnabled: false,
  });

  const list = investigation.list;
  const trimmedSearch = searchTerm.trim().toLowerCase();

  const filteredRuns = useMemo(() => {
    return list.runRows.filter((run) => {
      if (!runMatchesStatus(run, statusFilter)) return false;
      if (!trimmedSearch) return true;
      const haystack = [
        run.runId,
        run.status,
        run.operationPath,
        readRowString(run.row, "correlation_id", "request_id", "sync_name"),
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(trimmedSearch);
    });
  }, [list.runRows, statusFilter, trimmedSearch]);

  const selectedRun =
    filteredRuns.find(
      (run) =>
        run.runId === selection?.runId && run.runType === selection?.runType,
    ) ??
    investigation.selectedRun ??
    filteredRuns[0] ??
    null;

  const handleSelect = (run: FoundryLiteOperationsRunRow) => {
    if (!run.runId) return;
    setSelection({ runType: String(run.runType), runId: run.runId });
  };

  const columns: readonly DataTableColumn<FoundryLiteOperationsRunRow>[] = [
    {
      key: "runType",
      header: "종류",
      render: (run) => (
        <span className="text-[11px] font-medium">
          {runTypeLabel(String(run.runType))}
        </span>
      ),
    },
    {
      key: "runId",
      header: "run id",
      isMono: true,
      className: "max-w-[220px]",
      render: (run) => (
        <span className="block truncate">{run.runId ?? "—"}</span>
      ),
    },
    {
      key: "status",
      header: "상태",
      render: (run) => (
        <StatusPill intent={runPhaseIntent(run.phase)}>
          {run.status ?? runPhaseLabel(run.phase)}
        </StatusPill>
      ),
    },
  ];

  return (
    <div className="flex min-h-0 flex-col gap-3 lg:h-full lg:flex-row">
      {/* 좌: 필터 + 목록 */}
      <div className="flex min-h-0 flex-col gap-2 lg:w-[42%]">
        <div className="flex flex-wrap items-center gap-2">
          <Select value={runTypeFilter} onValueChange={setRunTypeFilter}>
            <SelectTrigger size="sm" className="w-36">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {RUN_TYPE_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={statusFilter}
            onValueChange={(value) => setStatusFilter(value as StatusFilter)}
          >
            <SelectTrigger size="sm" className="w-28">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {STATUS_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            variant="outline"
            size="sm"
            onClick={() => void list.reload()}
            disabled={list.isRefreshing}
          >
            새로고침
          </Button>
        </div>
        <div className="relative">
          <Search className="pointer-events-none absolute top-1/2 left-2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={searchTerm}
            onChange={(event) => setSearchTerm(event.target.value)}
            placeholder="run id / request id / correlation id 검색"
            className="h-8 pl-7 text-[12px]"
          />
        </div>
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>
            {filteredRuns.length}건
            {list.hasFailures ? ` · 실패 ${list.failedRuns.length}건` : ""}
          </span>
          {list.hasMoreRuns ? <span>추가 페이지 있음</span> : null}
        </div>

        {list.isLoading && !list.result ? (
          <LoadingState rowCount={8} />
        ) : list.error ? (
          <ErrorState error={list.error} onRetry={() => void list.reload()} />
        ) : (
          <DataTable
            columns={columns}
            rows={filteredRuns}
            rowKey={(run) => `${run.runType}:${run.runId}`}
            selectedKey={
              selectedRun ? `${selectedRun.runType}:${selectedRun.runId}` : null
            }
            onRowClick={handleSelect}
            emptyMessage="조건에 맞는 run이 없습니다."
            className="min-h-0 flex-1"
          />
        )}
      </div>

      {/* 우: 상세 + 타임라인 + prompt artifact */}
      <div className="min-h-0 flex-1 overflow-auto">
        {selectedRun ? (
          <RunDetailPanel
            run={selectedRun}
            detail={investigation.detail}
            promptArtifactRefs={investigation.detail.promptArtifactRefs}
          />
        ) : (
          <EmptyState
            title="run을 선택하세요"
            description="좌측 목록에서 run을 선택하면 상태·에러·타임라인·prompt artifact evidence를 확인할 수 있습니다."
          />
        )}
      </div>
    </div>
  );
}

function RunDetailPanel({
  run,
  detail,
  promptArtifactRefs,
}: {
  run: FoundryLiteOperationsRunRow;
  detail: ReturnType<
    typeof useFoundryLiteProvidedOperationsInvestigation
  >["detail"];
  promptArtifactRefs: FoundryLitePromptArtifactReference[];
}) {
  const runDetail = detail.detail;
  const errorMessage = runDetail?.errorMessage ?? null;
  const correlationId =
    runDetail?.correlationId ??
    readRowString(run.row, "correlation_id", "transaction_id");
  const investigationSummary = readInvestigationText(
    runDetail?.investigation,
    "summary",
  );
  const suggestedActions = readInvestigationActions(runDetail?.investigation);
  const references = investigationReferences(runDetail);

  return (
    <div className="space-y-3">
      <div className="rounded border bg-card p-3">
        <div className="mb-2 flex items-center gap-2">
          <span className="section-label">선택한 run 상세</span>
          <StatusPill intent={runPhaseIntent(run.phase)}>
            {run.status ?? runPhaseLabel(run.phase)}
          </StatusPill>
          <span className="ml-auto text-[11px] text-muted-foreground">
            {runTypeLabel(String(run.runType))}
          </span>
        </div>
        <dl className="divide-y">
          <EvidenceRow label="run id" value={run.runId} />
          <EvidenceRow label="correlation id" value={correlationId} />
          <EvidenceRow
            label="operation path"
            value={run.operationPath ?? "—"}
          />
          <EvidenceRow
            label="생성"
            value={formatTimestamp(
              readRowString(run.row, "created_at", "started_at", "ingested_at"),
            )}
          />
          <EvidenceRow
            label="완료"
            value={formatTimestamp(
              readRowString(run.row, "completed_at", "finished_at"),
            )}
          />
        </dl>
      </div>

      {detail.isLoading && !runDetail ? (
        <LoadingState rowCount={4} />
      ) : detail.error ? (
        <ErrorState error={detail.error} onRetry={() => void detail.reload()} />
      ) : runDetail ? (
        <>
          {errorMessage ? (
            <div className="rounded border border-destructive/30 bg-destructive/5 p-3">
              <SectionHeader label="에러 상세" />
              <pre className="mt-2 max-h-56 overflow-auto rounded bg-card p-2 font-mono text-[11px] whitespace-pre-wrap text-foreground/90">
                {errorMessage}
              </pre>
            </div>
          ) : null}

          <div className="rounded border bg-card p-3">
            <SectionHeader
              label="조사 요약"
              hint="백엔드 run detail investigation evidence"
            />
            <p className="mt-2 text-[12px] leading-5 text-foreground">
              {investigationSummary ?? "이 run에 대한 별도 조사 요약이 없습니다."}
            </p>
            {references.length > 0 ? (
              <dl className="mt-2 divide-y">
                {references.map((reference) => (
                  <EvidenceRow
                    key={reference.key}
                    label={reference.key}
                    value={reference.value}
                  />
                ))}
              </dl>
            ) : null}
            {suggestedActions.length > 0 ? (
              <div className="mt-2 flex flex-wrap gap-1.5">
                {suggestedActions.map((action) => (
                  <StatusPill key={action} intent="info">
                    {action}
                  </StatusPill>
                ))}
              </div>
            ) : null}
          </div>

          {detail.hasSourceEvidence ? (
            <SourceEvidencePanel evidence={runDetail.sourceEvidence} />
          ) : null}

          <RunTimeline run={run} detail={detail} />

          {/* prompt artifact는 실제 AIP run(또는 AI evidence 보유)에서만 표시한다.
              (SDK의 ref 추출이 run row id를 아티팩트로 오탐할 수 있어 run 종류로 게이트) */}
          {promptArtifactRefs.length > 0 &&
          run.runId &&
          (run.runType === "ai" || detail.hasAiEvidence) ? (
            <PromptArtifactPanel runId={run.runId} refs={promptArtifactRefs} />
          ) : null}
        </>
      ) : (
        <EmptyState
          title="상세 evidence 없음"
          description="이 run 종류는 상세 조회 대상이 아닙니다."
        />
      )}
    </div>
  );
}

function SourceEvidencePanel({
  evidence,
}: {
  evidence: Record<string, unknown> | null | undefined;
}) {
  const rows = sourceEvidenceRows(evidence);
  if (rows.length === 0) return null;
  return (
    <div className="rounded border bg-card p-3">
      <SectionHeader
        label="소스 실행 증거"
        hint="backend sourceEvidence"
      />
      <dl className="mt-2 divide-y">
        {rows.map((row) => (
          <EvidenceRow key={row.key} label={row.key} value={row.value} />
        ))}
      </dl>
    </div>
  );
}

function RunTimeline({
  run,
  detail,
}: {
  run: FoundryLiteOperationsRunRow;
  detail: ReturnType<
    typeof useFoundryLiteProvidedOperationsInvestigation
  >["detail"];
}) {
  const steps: { label: string; value: string; count: number }[] = [
    {
      label: "관련 outbox 이벤트",
      value: "outbox",
      count: detail.relatedOutboxEventCount,
    },
    {
      label: "관련 audit 이벤트",
      value: "audit",
      count: detail.relatedAuditEventCount,
    },
    {
      label: "관련 객체 편집",
      value: "objectEdit",
      count: detail.relatedObjectEditCount,
    },
    {
      label: "관련 writeback",
      value: "writeback",
      count: detail.relatedActionWritebackCount,
    },
    { label: "run 관계", value: "relation", count: detail.runRelationCount },
    { label: "lineage edge", value: "lineage", count: detail.lineageEdgeCount },
  ];

  return (
    <div className="rounded border bg-card p-3">
      <SectionHeader
        label="타임라인 · 관련 증거"
        hint={`run ${run.runId ?? ""}`}
      />
      <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-3">
        {steps.map((step) => (
          <div
            key={step.value}
            className="flex items-center justify-between gap-2 py-0.5"
          >
            <span className="text-[11px] text-muted-foreground">
              {step.label}
            </span>
            <span
              className={
                step.count > 0
                  ? "font-mono text-[12px] font-medium tabular-nums text-foreground"
                  : "font-mono text-[12px] tabular-nums text-muted-foreground"
              }
            >
              {step.count}
            </span>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {detail.hasQualityEvidence ? (
          <StatusPill intent="info">quality evidence</StatusPill>
        ) : null}
        {detail.hasAiEvidence ? (
          <StatusPill intent="info">AIP evidence</StatusPill>
        ) : null}
      </div>
    </div>
  );
}

function PromptArtifactPanel({
  runId,
  refs,
}: {
  runId: string;
  refs: FoundryLitePromptArtifactReference[];
}) {
  const [selectedArtifactId, setSelectedArtifactId] = useState<string>(
    refs[0]?.artifactId ?? "",
  );
  const artifact = useFoundryLiteProvidedPromptArtifact({
    runId,
    artifactId: selectedArtifactId || null,
    enabled: Boolean(selectedArtifactId),
  });

  return (
    <div className="rounded border bg-card p-3">
      <SectionHeader
        label="Prompt artifact"
        hint="AIP run 프롬프트 증거 (content hash / export marking)"
        actions={
          refs.length > 1 ? (
            <Select
              value={selectedArtifactId}
              onValueChange={setSelectedArtifactId}
            >
              <SelectTrigger size="sm" className="w-40">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {refs.map((ref) => (
                  <SelectItem key={ref.artifactId} value={ref.artifactId}>
                    {ref.label ?? ref.artifactId}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : null
        }
      />
      <dl className="mt-2 divide-y">
        <EvidenceRow label="artifact id" value={selectedArtifactId} />
        <EvidenceRow label="content hash" value={artifact.contentHash ?? "—"} />
        <EvidenceRow
          label="export marking"
          value={
            artifact.exportMarking ? (
              <StatusPill intent="warning">{artifact.exportMarking}</StatusPill>
            ) : (
              "—"
            )
          }
          mono={false}
        />
      </dl>
      {artifact.isLoading && !artifact.artifact ? (
        <LoadingState rowCount={3} className="mt-2" />
      ) : artifact.error ? (
        <ErrorState
          error={artifact.error}
          onRetry={() => void artifact.reload()}
          className="mt-2"
        />
      ) : artifact.plaintext ? (
        <pre className="mt-2 max-h-56 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[11px] whitespace-pre-wrap text-foreground/90">
          {artifact.plaintext}
        </pre>
      ) : (
        <div className="mt-2 flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <FileText className="size-3.5" /> 프롬프트 본문이 없습니다.
        </div>
      )}
    </div>
  );
}
