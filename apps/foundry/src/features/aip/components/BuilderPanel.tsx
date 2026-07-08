import type {
  AipBuilderIssue,
  AipBuilderValidateRequest,
} from "@foundry-lite/sdk";
import { CheckCircle2, Play, ShieldCheck, XCircle } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { operationsRunHref } from "@/lib/operations-links";

import {
  DEFAULT_BUILDER_GRAPH_JSON,
  INVALID_BUILDER_GRAPH_JSON,
  parseJsonObject,
  phaseTone,
  shortHash,
} from "../aip-model";
import type { AipWorkspace } from "../use-aip-workspace";
import { EvidenceRow, MonoChip, SectionLabel } from "./Evidence";

function IssueList({
  title,
  issues,
  intent,
}: {
  title: string;
  issues: AipBuilderIssue[];
  intent: "danger" | "warning";
}) {
  if (issues.length === 0) return null;
  return (
    <div>
      <SectionLabel right={<MonoChip>{`${issues.length}`}</MonoChip>}>
        {title}
      </SectionLabel>
      <ul className="mt-1.5 space-y-1">
        {issues.map((issue, index) => (
          <li
            key={issue.code + index}
            className="flex items-start gap-2 rounded border border-dashed bg-muted/30 p-2"
          >
            <StatusPill intent={intent}>{issue.section}</StatusPill>
            <div className="min-w-0 flex-1">
              <div className="text-[12px] text-foreground">{issue.message}</div>
              <div className="font-mono text-[10px] text-muted-foreground">
                code={issue.code} · severity={issue.severity}
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** 빌더 탭: 그래프 정의 JSON 검증/실행 + validation 결과 evidence. */
export function BuilderPanel({ workspace }: { workspace: AipWorkspace }) {
  const {
    validateBuilder,
    validate,
    validateIdempotencyKey,
    runBuilder,
    executeBuilder,
    builderRunIdempotencyKey,
  } = workspace;

  const [graphText, setGraphText] = useState(DEFAULT_BUILDER_GRAPH_JSON);
  const [parseError, setParseError] = useState<string | null>(null);
  // 마지막으로 수행한 액션을 추적해 검증 결과가 그 액션의 그래프를 가리키게 한다.
  const [lastAction, setLastAction] = useState<"validate" | "run" | null>(null);

  const builderRun = runBuilder.result;
  // 실행이 마지막 액션이면 실행이 통과한 검증 결과를 근거로 보여준다.
  // (독립 검증과 실행 검증이 서로 다른 그래프를 가리키는 stale evidence 방지)
  const validation =
    lastAction === "run"
      ? (builderRun?.validation ?? validateBuilder.result)
      : (validateBuilder.result ?? builderRun?.validation);

  const parsePayload = (): AipBuilderValidateRequest | null => {
    const parsed = parseJsonObject<AipBuilderValidateRequest>(graphText);
    if (!parsed.ok) {
      setParseError(parsed.error);
      return null;
    }
    setParseError(null);
    return parsed.value;
  };

  const handleValidate = () => {
    const payload = parsePayload();
    if (payload) {
      setLastAction("validate");
      void validate(payload);
    }
  };

  const handleRun = () => {
    const payload = parsePayload();
    if (payload) {
      setLastAction("run");
      void executeBuilder({ validate: payload });
    }
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      {/* 왼쪽: 그래프 정의 편집기 */}
      <div className="space-y-3">
        <div className="rounded border bg-card p-3">
          <SectionLabel
            right={
              <>
                <button
                  type="button"
                  onClick={() => {
                    setGraphText(DEFAULT_BUILDER_GRAPH_JSON);
                    setParseError(null);
                  }}
                  className="rounded border border-dashed px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                >
                  유효 예시
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setGraphText(INVALID_BUILDER_GRAPH_JSON);
                    setParseError(null);
                  }}
                  className="rounded border border-dashed px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                >
                  실패 예시
                </button>
              </>
            }
          >
            그래프 정의 (JSON)
          </SectionLabel>
          <Textarea
            value={graphText}
            onChange={(event) => setGraphText(event.target.value)}
            spellCheck={false}
            className="mt-2 h-[420px] resize-none font-mono text-[11px] leading-relaxed"
          />
          {parseError ? (
            <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px] text-destructive">
              {parseError}
            </div>
          ) : null}
          <div className="mt-2 flex items-center justify-between gap-2">
            <span className="min-w-0 truncate font-mono text-[10px] text-muted-foreground">
              {builderRunIdempotencyKey
                ? `idem=${builderRunIdempotencyKey}`
                : validateIdempotencyKey
                  ? `idem=${validateIdempotencyKey}`
                  : "멱등키는 실행 시 발급됩니다."}
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={handleValidate}
                disabled={validateBuilder.isRunning}
              >
                <ShieldCheck />
                검증
              </Button>
              <Button
                size="sm"
                onClick={handleRun}
                disabled={runBuilder.isRunning}
              >
                <Play />
                실행
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* 오른쪽: 검증/실행 결과 */}
      <div className="space-y-3">
        {validateBuilder.error ? (
          <ErrorState error={validateBuilder.error} onRetry={handleValidate} />
        ) : null}

        {validation ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(validation.validationStatus)}>
                  {validation.validationStatus}
                </StatusPill>
              }
            >
              검증 결과
            </SectionLabel>
            <div className="mt-2 flex items-center gap-2 text-[12px]">
              {validation.releaseReady ? (
                <CheckCircle2 className="size-4 text-success" />
              ) : (
                <XCircle className="size-4 text-destructive" />
              )}
              <span className="font-medium">
                {validation.releaseReady
                  ? "릴리스 준비 완료 (release ready)"
                  : "릴리스 불가 — blocking issue를 해소하세요"}
              </span>
            </div>
            <div className="mt-2 grid grid-cols-2 gap-x-4 sm:grid-cols-4">
              <Metric label="node" value={validation.nodeCount} />
              <Metric label="context" value={validation.contextSourceCount} />
              <Metric label="tool" value={validation.toolCount} />
              <Metric label="eval axis" value={validation.evalAxisCount} />
            </div>
            <div className="mt-2 space-y-0">
              <EvidenceRow
                label="draft hash"
                value={<MonoChip>{shortHash(validation.draftHash)}</MonoChip>}
              />
              <EvidenceRow
                label="graph hash"
                value={<MonoChip>{shortHash(validation.graphHash)}</MonoChip>}
              />
            </div>
            <div className="mt-3 space-y-3">
              <IssueList
                title="blocking issues"
                issues={validation.blockingIssues}
                intent="danger"
              />
              <IssueList
                title="warnings"
                issues={validation.warnings}
                intent="warning"
              />
            </div>
            {validation.safeBoundaries.length > 0 ? (
              <div className="mt-3">
                <SectionLabel>안전 경계 (safe boundaries)</SectionLabel>
                <ul className="mt-1.5 flex flex-wrap gap-1">
                  {validation.safeBoundaries.map((boundary) => (
                    <li key={boundary}>
                      <StatusPill intent="success">
                        <ShieldCheck className="size-3" />
                        {boundary}
                      </StatusPill>
                    </li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}

        {runBuilder.error ? (
          <ErrorState error={runBuilder.error} onRetry={handleRun} />
        ) : null}

        {builderRun ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(builderRun.runStatus)}>
                  {builderRun.runStatus}
                </StatusPill>
              }
            >
              그래프 실행 결과
            </SectionLabel>
            <div className="mt-2 space-y-0">
              <EvidenceRow
                label="logic run id"
                value={<MonoChip>{builderRun.logicRunId}</MonoChip>}
              />
              {builderRun.operations ? (
                <EvidenceRow
                  label="operations run"
                  value={
                    <Link
                      to={operationsRunHref(
                        builderRun.operations.runId,
                        builderRun.operations.runType,
                      )}
                      title={builderRun.operations.detailPath}
                      className="inline-block max-w-full hover:underline"
                    >
                      <MonoChip>{builderRun.operations.runId}</MonoChip>
                    </Link>
                  }
                />
              ) : null}
            </div>
            {builderRun.error ? (
              <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 p-2">
                <div className="section-label mb-1 text-destructive">
                  실행 실패 사유
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-foreground/80">
                  <span>reason={String(builderRun.error.reason ?? "-")}</span>
                  <span>detail={String(builderRun.error.detail ?? "-")}</span>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="py-1">
      <div className="text-[10px] tracking-[0.3px] text-muted-foreground uppercase">
        {label}
      </div>
      <div className="font-mono text-[15px] font-semibold text-foreground">
        {value}
      </div>
    </div>
  );
}
