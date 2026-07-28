import type {
  PipelineGraphV2,
  PipelinePreviewRun,
} from "@foundry-lite/sdk";
import {
  AlertTriangle,
  ArrowRight,
  Ban,
  Braces,
  Database,
  LoaderCircle,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import {
  type KeyboardEvent,
  useEffect,
  useId,
  useMemo,
  useState,
} from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import {
  compactJson,
  previewStatusLabel,
  recordList,
  recordValue,
  textValue,
} from "../pipeline-preview-model";
import {
  useLlmRunTrialEvidence,
  useLlmTrialRows,
  useLlmTrialRunError,
} from "../pipeline-use-llm-trial-model";
import {
  usePipelinePreviewRun,
} from "../use-pipeline-preview-run";

type UseLlmTrialTab = "input" | "output" | "trial" | "errors";

interface UseLlmTrialPanelProps {
  branchId: string | null;
  graph: PipelineGraphV2 | null;
  nodeId: string;
  outputColumn: string;
  inputFields: readonly string[];
  trialCount: number | null;
  isGraphDirty: boolean;
  invalidReason: string | null;
}

const TAB_LABELS: Record<UseLlmTrialTab, string> = {
  input: "Input table",
  output: "Output table",
  trial: "Trial run",
  errors: "Errors",
};

/** Use LLM form draft를 실제 no-commit preview API로 실행하는 전용 trial dock. */
export function UseLlmTrialPanel({
  branchId,
  graph,
  nodeId,
  outputColumn,
  inputFields,
  trialCount,
  isGraphDirty,
  invalidReason,
}: UseLlmTrialPanelProps) {
  const limits = useMemo(
    () => (trialCount ? { tableRows: trialCount } : undefined),
    [trialCount],
  );
  const preview = usePipelinePreviewRun({
    branchId,
    graph,
    targetNodeId: nodeId,
    limits,
  });
  const rows = useMemo(
    () => useLlmTrialRows(preview.run, nodeId, outputColumn, inputFields),
    [inputFields, nodeId, outputColumn, preview.run],
  );
  const [activeTab, setActiveTab] = useState<UseLlmTrialTab>("trial");
  const [selectedRowId, setSelectedRowId] = useState<string | null>(null);
  const tabSetId = useId();
  const selectedRow =
    rows.find((row) => row.id === selectedRowId) ?? rows[0] ?? null;
  const rowErrors = rows.filter((row) => row.error);
  const runError = useLlmTrialRunError(preview.run);
  const runTrialEvidence = useLlmRunTrialEvidence(preview.run);

  useEffect(() => {
    setSelectedRowId(rows[0]?.id ?? null);
  }, [preview.run?.id, rows]);

  useEffect(() => {
    if (preview.error || runError || rowErrors.length > 0) {
      setActiveTab("errors");
    }
  }, [preview.error, rowErrors.length, runError]);

  const handleStart = () => {
    setActiveTab("trial");
    void preview.start();
  };
  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    tab: UseLlmTrialTab,
  ) => {
    const nextTab = nextTrialTab(tab, event.key);
    if (!nextTab) return;
    event.preventDefault();
    setActiveTab(nextTab);
    document.getElementById(`${tabSetId}-${nextTab}`)?.focus();
  };

  return (
    <section
      aria-label="Use LLM live trial"
      className="flex h-[276px] shrink-0 flex-col border-t border-[#AEB6C1] bg-white"
    >
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[#D5DAE0] bg-[#F7F8FA] px-3">
        <Sparkles className="size-3.5 text-[#6550B9]" />
        <span className="text-[11px] font-semibold">Live trial</span>
        <StatusPill intent="warning">
          미리보기 전용 · 출력 버전 생성 없음
        </StatusPill>
        <span className="font-mono text-[9px] text-muted-foreground">
          commitForbidden=true · serving=false
        </span>
        {isGraphDirty ? (
          <StatusPill intent="info">unsaved graph 포함</StatusPill>
        ) : null}
        <StatusPill intent="info">form draft 즉시 반영</StatusPill>
        {preview.run ? (
          <StatusPill intent={statusIntent(preview.run.status)}>
            {previewStatusLabel(preview.run.status)}
          </StatusPill>
        ) : null}
        <div className="ml-auto flex items-center gap-1.5">
          {preview.isRunning ? (
            <Button
              size="sm"
              variant="outline"
              className="h-7 rounded-[2px] text-[10px]"
              disabled={preview.isCancelling}
              onClick={() => void preview.cancel()}
            >
              <Ban className="size-3.5" />
              {preview.isCancelling ? "취소 요청 중" : "Trial 취소"}
            </Button>
          ) : null}
          <Button
            size="sm"
            className="h-7 rounded-[2px] bg-[#6550B9] text-[10px] hover:bg-[#5844A6]"
            aria-label="Run Use LLM trial"
            disabled={
              !preview.canStart ||
              preview.isStarting ||
              preview.isRunning ||
              Boolean(invalidReason)
            }
            onClick={handleStart}
          >
            {preview.isStarting || preview.isRunning ? (
              <LoaderCircle className="size-3.5 animate-spin motion-reduce:animate-none" />
            ) : preview.run ? (
              <RotateCcw className="size-3.5" />
            ) : (
              <Play className="size-3.5" />
            )}
            {preview.isStarting
              ? "요청 중"
              : preview.run
                ? "현재 draft 재실행"
                : `현재 draft ${trialCount ?? "-"}행 실행`}
          </Button>
        </div>
      </div>

      <div
        role="tablist"
        aria-label="Use LLM trial tabs"
        className="flex h-8 shrink-0 items-end border-b border-[#C5CBD3] bg-[#F7F8FA] px-3"
      >
        {(Object.keys(TAB_LABELS) as UseLlmTrialTab[]).map((tab) => (
          <button
            key={tab}
            id={`${tabSetId}-${tab}`}
            type="button"
            role="tab"
            aria-selected={activeTab === tab}
            aria-controls={`${tabSetId}-panel`}
            tabIndex={activeTab === tab ? 0 : -1}
            className={cn(
              "h-8 border-b-2 px-3 text-[11px]",
              activeTab === tab
                ? "border-[#6550B9] bg-white font-semibold text-[#5846A5]"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
            onClick={() => setActiveTab(tab)}
            onKeyDown={(event) => handleTabKeyDown(event, tab)}
          >
            {TAB_LABELS[tab]}
            {tab === "errors" && rowErrors.length > 0
              ? ` · ${rowErrors.length}`
              : ""}
          </button>
        ))}
      </div>

      <div
        id={`${tabSetId}-panel`}
        role="tabpanel"
        className="min-h-0 flex-1 overflow-auto"
        aria-live="polite"
      >
        {invalidReason ? (
          <TrialBlocked reason={invalidReason} />
        ) : preview.error ? (
          <ErrorState
            error={preview.error}
            onRetry={handleStart}
            className="m-3"
          />
        ) : preview.isRunning ? (
          <TrialRunning run={preview.run} />
        ) : !preview.run ? (
          <TrialReady trialCount={trialCount} />
        ) : !isSuccessful(preview.run.status) ? (
          <TrialFailure
            run={preview.run}
            error={runError}
            trialEvidence={runTrialEvidence}
          />
        ) : activeTab === "input" ? (
          <TrialJsonTable
            ariaLabel="Use LLM actual trial inputs"
            rows={rows.map((row) => ({ id: row.id, value: row.input }))}
          />
        ) : activeTab === "output" ? (
          <TrialJsonTable
            ariaLabel="Use LLM actual trial outputs"
            rows={rows.map((row) => ({ id: row.id, value: row.output }))}
          />
        ) : activeTab === "errors" ? (
          <TrialErrors
            rows={rowErrors}
            runError={runError}
            runTrialEvidence={runTrialEvidence}
          />
        ) : (
          <TrialEvidenceLane
            run={preview.run}
            rows={rows}
            selectedRow={selectedRow}
            onSelectRow={setSelectedRowId}
          />
        )}
      </div>
    </section>
  );
}

function TrialReady({ trialCount }: { trialCount: number | null }) {
  return (
    <div className="grid h-full place-items-center p-3">
      <div className="max-w-2xl text-center">
        <Play className="mx-auto size-5 text-[#6550B9]" />
        <p className="mt-2 text-[12px] font-semibold">
          저장하지 않은 프롬프트로 실제 {trialCount ?? "-"}개 행을 실행합니다
        </p>
        <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
          선택한 Use LLM 노드까지의 draft graph만 실행합니다. 결과는 이 trial
          run에만 남고 Dataset·Media serving version은 만들지 않습니다.
        </p>
      </div>
    </div>
  );
}

function TrialBlocked({ reason }: { reason: string }) {
  return (
    <div className="m-3 flex items-start gap-2 border border-[#D9822B] bg-[#FFF4E8] p-3 text-[11px] text-[#7A4314]">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" />
      <div>
        <div className="font-semibold">Trial을 실행하기 전에 설정을 수정하세요</div>
        <div className="mt-1">{reason}</div>
      </div>
    </div>
  );
}

function TrialRunning({ run }: { run: PipelinePreviewRun | null }) {
  return (
    <div className="grid h-full place-items-center">
      <div className="text-center text-[11px] text-muted-foreground">
        <LoaderCircle className="mx-auto size-5 animate-spin text-[#6550B9] motion-reduce:animate-none" />
        <div className="mt-2 font-semibold text-foreground">
          실제 draft graph를 실행 중입니다
        </div>
        <div className="mt-1 font-mono">run={run?.id ?? "requesting"}</div>
      </div>
    </div>
  );
}

function TrialFailure({
  run,
  error,
  trialEvidence,
}: {
  run: PipelinePreviewRun;
  error: Record<string, unknown> | null;
  trialEvidence: Record<string, unknown> | null;
}) {
  return (
    <div className="m-3 border border-destructive/30 bg-destructive/5 p-3">
      <div className="flex items-center gap-2">
        <StatusPill intent="danger">
          {previewStatusLabel(run.status)}
        </StatusPill>
        <span className="font-mono text-[9px] text-muted-foreground">
          run={run.id}
        </span>
      </div>
      <p className="mt-2 text-[11px] font-semibold text-destructive">
        {textValue(error?.message) ??
          textValue(error?.detail) ??
          "Use LLM trial이 결과를 만들지 못했습니다."}
      </p>
      {error ? <JsonBlock value={error} className="mt-2" /> : null}
      {trialEvidence ? (
        <TrialFailureEvidence trialEvidence={trialEvidence} />
      ) : null}
    </div>
  );
}

function TrialJsonTable({
  ariaLabel,
  rows,
}: {
  ariaLabel: string;
  rows: Array<{ id: string; value: unknown }>;
}) {
  if (rows.length === 0) {
    return (
      <EmptyState
        title="반환된 trial 행이 없습니다"
        description="입력 source와 preview 한도를 확인한 뒤 다시 실행하세요."
        className="m-3 border-0"
      />
    );
  }
  return (
    <table aria-label={ariaLabel} className="w-full table-fixed text-left">
      <thead className="sticky top-0 z-10 bg-[#EEF1F4] text-[9px] tracking-[0.06em] text-muted-foreground uppercase">
        <tr>
          <th className="w-40 border-r border-b border-[#C5CBD3] px-3 py-1.5">
            Source row
          </th>
          <th className="border-b border-[#C5CBD3] px-3 py-1.5">
            Actual payload
          </th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.id} className="align-top even:bg-[#FAFBFC]">
            <td className="border-r border-b border-[#E1E5EA] px-3 py-2 font-mono text-[10px] font-semibold">
              {row.id}
            </td>
            <td className="border-b border-[#E1E5EA] px-3 py-2">
              <JsonBlock value={row.value} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function TrialEvidenceLane({
  run,
  rows,
  selectedRow,
  onSelectRow,
}: {
  run: PipelinePreviewRun;
  rows: ReturnType<typeof useLlmTrialRows>;
  selectedRow: ReturnType<typeof useLlmTrialRows>[number] | null;
  onSelectRow: (rowId: string) => void;
}) {
  if (!selectedRow) {
    return (
      <EmptyState
        title="표시할 trial 결과가 없습니다"
        description="실행은 성공했지만 현재 입력과 한도에서 반환된 행이 없습니다."
        className="m-3 border-0"
      />
    );
  }
  return (
    <div className="grid min-h-full grid-cols-[150px_minmax(0,1fr)]">
      <div className="border-r border-[#C5CBD3] bg-[#F7F8FA] p-2">
        <div className="px-1 text-[9px] font-bold tracking-[0.08em] text-muted-foreground uppercase">
          Bounded samples
        </div>
        <div className="mt-2 space-y-1">
          {rows.map((row, index) => (
            <button
              key={row.id}
              type="button"
              aria-pressed={row.id === selectedRow.id}
              className={cn(
                "flex w-full items-center gap-2 border px-2 py-1.5 text-left text-[10px]",
                row.id === selectedRow.id
                  ? "border-[#7961DB] bg-[#F1EEFB] text-[#4F3C9A]"
                  : "border-transparent hover:border-[#C5CBD3] hover:bg-white",
              )}
              onClick={() => onSelectRow(row.id)}
            >
              <span className="font-mono text-[9px]">{index + 1}</span>
              <span className="truncate">{row.id}</span>
              {row.error ? (
                <AlertTriangle className="ml-auto size-3 text-[#C23030]" />
              ) : (
                <ShieldCheck className="ml-auto size-3 text-[#238551]" />
              )}
            </button>
          ))}
        </div>
      </div>
      <div className="grid gap-2 p-2 lg:grid-cols-[minmax(0,1fr)_24px_minmax(0,1fr)_24px_230px]">
        <TrialStage
          icon={Database}
          eyebrow="01 · actual input"
          title={selectedRow.id}
        >
          <JsonBlock value={selectedRow.input} />
        </TrialStage>
        <div className="hidden items-center justify-center lg:flex">
          <ArrowRight className="size-4 text-[#7B8794]" />
        </div>
        <TrialStage
          icon={Braces}
          eyebrow="02 · structured result"
          title={selectedRow.error ? "row error" : "schema-valid output"}
          intent={selectedRow.error ? "danger" : "default"}
        >
          <TrialResultTrace
            output={selectedRow.output}
            error={selectedRow.error}
            trialEvidence={selectedRow.trialEvidence}
          />
        </TrialStage>
        <div className="hidden items-center justify-center lg:flex">
          <ArrowRight className="size-4 text-[#7B8794]" />
        </div>
        <ModelPassport
          run={run}
          evidence={selectedRow.evidence}
          trialEvidence={selectedRow.trialEvidence}
        />
      </div>
    </div>
  );
}

function TrialStage({
  icon: Icon,
  eyebrow,
  title,
  intent = "default",
  children,
}: {
  icon: typeof Database;
  eyebrow: string;
  title: string;
  intent?: "default" | "danger";
  children: React.ReactNode;
}) {
  return (
    <section
      className={cn(
        "min-w-0 border bg-white",
        intent === "danger"
          ? "border-[#E5A8A8]"
          : "border-[#C5CBD3]",
      )}
    >
      <div className="flex items-center gap-2 border-b border-[#D5DAE0] bg-[#F7F8FA] px-2 py-1.5">
        <Icon className="size-3.5 text-[#4C6F8F]" />
        <div className="min-w-0">
          <div className="text-[8px] font-bold tracking-[0.09em] text-muted-foreground uppercase">
            {eyebrow}
          </div>
          <div className="truncate text-[10px] font-semibold">{title}</div>
        </div>
      </div>
      <div className="max-h-32 overflow-auto p-2">{children}</div>
    </section>
  );
}

function TrialResultTrace({
  output,
  error,
  trialEvidence,
}: {
  output: unknown;
  error: Record<string, unknown> | null;
  trialEvidence: Record<string, unknown> | null;
}) {
  const attempts = recordList(trialEvidence?.parseAttempts);
  const lastAttempt = attempts[attempts.length - 1] ?? null;
  const correction = recordValue(trialEvidence?.correction);
  const responseSnapshot = lastAttempt?.responseSnapshot;
  return (
    <div className="space-y-2">
      {responseSnapshot !== undefined ? (
        <div>
          <TraceLabel
            label="Provider response snapshot"
            detail={`redacted · attempt ${String(lastAttempt?.attemptNumber ?? attempts.length)}`}
          />
          <JsonBlock value={responseSnapshot} />
        </div>
      ) : null}
      {correction?.attempted === true ? (
        <div className="border-l-2 border-[#D9822B] bg-[#FFF8E7] px-2 py-1 text-[8px] text-[#725B20]">
          correction {String(correction.strategy ?? "unspecified")} ·{" "}
          {String(correction.attemptCount ?? 0)} attempt(s)
        </div>
      ) : null}
      <div>
        <TraceLabel
          label={error ? "Final typed error" : "Final typed output"}
          detail={textValue(recordValue(trialEvidence?.final)?.status) ?? "returned row"}
        />
        <JsonBlock
          value={error ?? output}
          className={error ? "text-[#B42318]" : undefined}
        />
      </div>
    </div>
  );
}

function TrialFailureEvidence({
  trialEvidence,
}: {
  trialEvidence: Record<string, unknown>;
}) {
  const input = recordValue(trialEvidence.input);
  const attempts = recordList(trialEvidence.parseAttempts);
  const final = recordValue(trialEvidence.final);
  return (
    <div className="mt-2 grid gap-2 border-t border-destructive/20 pt-2 md:grid-cols-3">
      <div>
        <TraceLabel label="Selected input" detail="redacted snapshot" />
        <JsonBlock
          value={input?.rowSnapshot ?? input?.selectedFields ?? null}
        />
      </div>
      <div>
        <TraceLabel
          label="Parse attempts"
          detail={`${attempts.length} recorded`}
        />
        <JsonBlock value={attempts} />
      </div>
      <div>
        <TraceLabel label="Final" detail={textValue(final?.status) ?? "failed"} />
        <JsonBlock value={final} className="text-[#B42318]" />
      </div>
    </div>
  );
}

function TraceLabel({
  label,
  detail,
}: {
  label: string;
  detail: string;
}) {
  return (
    <div className="mb-1 flex items-center gap-1.5 text-[8px] font-bold tracking-[0.06em] text-muted-foreground uppercase">
      <span>{label}</span>
      <span className="font-mono font-normal tracking-normal normal-case">
        {detail}
      </span>
    </div>
  );
}

function ModelPassport({
  run,
  evidence,
  trialEvidence,
}: {
  run: PipelinePreviewRun;
  evidence: Record<string, unknown> | null;
  trialEvidence: Record<string, unknown> | null;
}) {
  const request = recordValue(trialEvidence?.request);
  const correction = recordValue(trialEvidence?.correction);
  return (
    <aside className="min-w-0 border border-[#C9C1EA] bg-[#F7F5FF]">
      <div className="flex items-center gap-2 border-b border-[#C9C1EA] bg-[#F1EEFB] px-2 py-1.5">
        <Sparkles className="size-3.5 text-[#6550B9]" />
        <div>
          <div className="text-[8px] font-bold tracking-[0.09em] text-[#6C5AA7] uppercase">
            03 · model evidence
          </div>
          <div className="text-[10px] font-semibold text-[#4F3C9A]">
            Artifact passport
          </div>
        </div>
      </div>
      <dl className="grid grid-cols-[78px_minmax(0,1fr)] gap-x-2 gap-y-1 p-2 font-mono text-[9px]">
        <Evidence label="run" value={run.id} />
        <Evidence label="provider" value={textValue(evidence?.provider)} />
        <Evidence
          label="model"
          value={joinedModel(evidence)}
        />
        <Evidence
          label="prompt"
          value={textValue(evidence?.promptVersionId)}
        />
        <Evidence
          label="prompt hash"
          value={shortHash(textValue(evidence?.promptHash))}
        />
        <Evidence
          label="tokens"
          value={tokenLabel(evidence)}
        />
        <Evidence
          label="latency"
          value={numericLabel(evidence?.latencyMs, "ms")}
        />
        <Evidence
          label="provider req"
          value={textValue(evidence?.providerRequestId)}
        />
        <Evidence
          label="request"
          value={shortHash(textValue(request?.requestFingerprint))}
        />
        <Evidence
          label="correction"
          value={correctionLabel(correction)}
        />
        <Evidence
          label="class"
          value={
            textValue(evidence?.dataClassification) ??
            textValue(request?.dataClassification)
          }
        />
        <Evidence
          label="cache"
          value={cacheLabel(evidence)}
        />
      </dl>
      <div className="border-t border-[#C9C1EA] px-2 py-1.5 text-[8px] leading-3 text-[#6C5AA7]">
        {trialEvidence
          ? "Provider 응답은 서버가 저장한 bounded redacted snapshot만 표시합니다. 원문 전체나 secret은 노출하지 않습니다."
          : "이 결과는 구버전 evidence 계약입니다. Provider response·parse attempt·correction snapshot은 반환되지 않았습니다."}
      </div>
    </aside>
  );
}

function TrialErrors({
  rows,
  runError,
  runTrialEvidence,
}: {
  rows: ReturnType<typeof useLlmTrialRows>;
  runError: Record<string, unknown> | null;
  runTrialEvidence: Record<string, unknown> | null;
}) {
  if (!runError && rows.length === 0) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="오류가 없습니다"
        description="반환된 모든 trial 행이 typed output 계약을 통과했습니다."
        className="m-3 border-0"
      />
    );
  }
  return (
    <div className="space-y-2 p-3">
      {runError ? (
        <div className="border border-destructive/30 bg-destructive/5 p-2">
          <div className="text-[10px] font-semibold text-destructive">
            Preview run error
          </div>
          <JsonBlock value={runError} className="mt-1" />
          {runTrialEvidence ? (
            <TrialFailureEvidence trialEvidence={runTrialEvidence} />
          ) : null}
        </div>
      ) : null}
      {rows.map((row) => (
        <div
          key={row.id}
          className="grid gap-2 border border-[#E5A8A8] bg-[#FFF7F7] p-2 md:grid-cols-[150px_minmax(0,1fr)]"
        >
          <div className="font-mono text-[10px] font-semibold">{row.id}</div>
          <JsonBlock value={row.error} className="text-[#B42318]" />
        </div>
      ))}
    </div>
  );
}

function JsonBlock({
  value,
  className,
}: {
  value: unknown;
  className?: string;
}) {
  return (
    <pre
      className={cn(
        "whitespace-pre-wrap break-all font-mono text-[9px] leading-4 text-[#293742]",
        className,
      )}
    >
      {prettyJson(value)}
    </pre>
  );
}

function Evidence({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <>
      <dt className="text-[#786AA9]">{label}</dt>
      <dd className="truncate text-[#3E3468]" title={value ?? "-"}>
        {value ?? "-"}
      </dd>
    </>
  );
}

function nextTrialTab(
  current: UseLlmTrialTab,
  key: string,
): UseLlmTrialTab | null {
  const tabs = Object.keys(TAB_LABELS) as UseLlmTrialTab[];
  if (key === "Home") return tabs[0] ?? null;
  if (key === "End") return tabs.at(-1) ?? null;
  if (key !== "ArrowLeft" && key !== "ArrowRight") return null;
  const offset = key === "ArrowRight" ? 1 : -1;
  const index = tabs.indexOf(current);
  return tabs[(index + offset + tabs.length) % tabs.length] ?? null;
}

function statusIntent(status: string): StatusIntent {
  const normalized = status.toUpperCase();
  if (normalized === "SUCCEEDED") return "success";
  if (normalized === "PARTIAL") return "warning";
  if (normalized === "FAILED" || normalized === "CANCELLED") return "danger";
  return "info";
}

function isSuccessful(status: string): boolean {
  const normalized = status.toUpperCase();
  return normalized === "SUCCEEDED" || normalized === "PARTIAL";
}

function prettyJson(value: unknown): string {
  if (value === undefined) return "-";
  try {
    return JSON.stringify(value, null, 2) ?? String(value);
  } catch {
    return compactJson(value);
  }
}

function joinedModel(evidence: Record<string, unknown> | null): string | null {
  const id = textValue(evidence?.resolvedModelId);
  const revision = textValue(evidence?.resolvedModelRevision);
  if (!id) return null;
  return revision ? `${id}@${revision}` : id;
}

function shortHash(value: string | null): string | null {
  if (!value) return null;
  return value.length > 22 ? `${value.slice(0, 19)}...` : value;
}

function tokenLabel(evidence: Record<string, unknown> | null): string | null {
  const input = evidence?.inputTokens;
  const output = evidence?.outputTokens;
  if (typeof input !== "number" && typeof output !== "number") return null;
  return `${typeof input === "number" ? input : "-"} in / ${
    typeof output === "number" ? output : "-"
  } out`;
}

function cacheLabel(evidence: Record<string, unknown> | null): string | null {
  if (!evidence) return null;
  if (evidence.cacheHit === true) return "hit";
  if (evidence.cacheEligible === true) return "eligible · miss";
  return "not eligible";
}

function numericLabel(value: unknown, unit: string): string | null {
  return typeof value === "number" && Number.isFinite(value)
    ? `${value}${unit}`
    : null;
}

function correctionLabel(
  correction: Record<string, unknown> | null,
): string | null {
  if (!correction) return null;
  const attempted = correction.attempted === true;
  const count =
    typeof correction.attemptCount === "number"
      ? correction.attemptCount
      : 0;
  return attempted
    ? `${count} · ${String(correction.strategy ?? "unspecified")}`
    : "not attempted";
}
