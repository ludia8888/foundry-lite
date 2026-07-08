import { useMemo, useState } from "react";
import { FlaskConical, Rocket } from "lucide-react";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { RELEASE_CHANNELS, asText, phaseTone, shortHash } from "../aip-model";
import type { AipWorkspace } from "../use-aip-workspace";
import { EvidenceRow, MonoChip, SectionLabel } from "./Evidence";

type EvalCaseRow = {
  case_api_name: string;
  axis: string;
  sample_index: number;
  score: number;
  is_passed: boolean;
  result_hash: string;
  reason: string | null;
};

const CASE_COLUMNS: readonly DataTableColumn<EvalCaseRow>[] = [
  {
    key: "case",
    header: "case",
    render: (row) => (
      <span className="font-mono text-[11px]">{row.case_api_name}</span>
    ),
  },
  {
    key: "axis",
    header: "axis",
    render: (row) => <StatusPill intent="neutral">{row.axis}</StatusPill>,
  },
  {
    key: "score",
    header: "score",
    className: "text-right",
    render: (row) => (
      <span className="font-mono text-[11px]">{row.score.toFixed(2)}</span>
    ),
  },
  {
    key: "passed",
    header: "result",
    render: (row) => (
      <StatusPill intent={row.is_passed ? "success" : "danger"}>
        {row.is_passed ? "passed" : "failed"}
      </StatusPill>
    ),
  },
  {
    key: "hash",
    header: "result hash",
    isMono: true,
    render: (row) => shortHash(row.result_hash),
  },
];

/** 평가/릴리스 탭: eval run → release promote를 운영 evidence와 연결. */
export function EvalReleasePanel({ workspace }: { workspace: AipWorkspace }) {
  const {
    runEval,
    executeEval,
    evalIdempotencyKey,
    candidateAgentVersionId,
    promoteRelease,
    promote,
    promoteIdempotencyKey,
  } = workspace;

  // 채널을 먼저 고르고 평가를 실행한다. candidate 채널과 대상 채널이 항상 일치한다.
  const [channel, setChannel] = useState<string>("canary");
  // 평가를 실제로 실행한 채널 (승격 대상과 일치해야 백엔드가 허용).
  const [evaluatedChannel, setEvaluatedChannel] = useState<string | null>(null);
  const evalResult = runEval.result;
  const promotion = promoteRelease.result;

  const handleRunEval = () => {
    setEvaluatedChannel(channel);
    void executeEval(channel);
  };

  const axes = useMemo(() => {
    const summary = evalResult?.summary_json as
      | {
          axes?: Record<
            string,
            { passed?: boolean; score?: number; caseCount?: number }
          >;
        }
      | undefined;
    return Object.entries(summary?.axes ?? {});
  }, [evalResult]);

  const canPromote =
    evalResult !== null && evalResult.is_passed && evaluatedChannel === channel;

  const handlePromote = () => {
    if (!evalResult || !evaluatedChannel) return;
    void promote({
      agentVersionId: evalResult.agent_version_id,
      targetReleaseChannel: evaluatedChannel,
      evalRunId: evalResult.eval_run_id,
      policyVersion: "policy-v1",
    });
  };

  return (
    <div className="grid gap-3 lg:grid-cols-2">
      {/* 평가 */}
      <div className="space-y-3">
        <div className="rounded border bg-card p-3">
          <SectionLabel
            right={
              <div className="flex items-center gap-1.5">
                <Select value={channel} onValueChange={setChannel}>
                  <SelectTrigger size="sm" className="w-28 text-[12px]">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {RELEASE_CHANNELS.map((option) => (
                      <SelectItem
                        key={option}
                        value={option}
                        className="text-[12px]"
                      >
                        {option}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={handleRunEval}
                  disabled={runEval.isRunning}
                >
                  <FlaskConical />
                  평가 실행
                </Button>
              </div>
            }
          >
            평가 스위트 (eval)
          </SectionLabel>
          <div className="mt-2 grid grid-cols-1 gap-0.5">
            <EvidenceRow label="suite" value="order-copilot-suite@v1" mono />
            <EvidenceRow
              label="candidate version"
              value={candidateAgentVersionId}
              mono
              title={candidateAgentVersionId}
            />
            <EvidenceRow label="required axes" value="answer, retrieval" />
            <EvidenceRow label="min score" value="0.70" mono />
            <EvidenceRow
              label="candidate channel"
              value={<StatusPill intent="info">{channel}</StatusPill>}
            />
          </div>
          {evalIdempotencyKey ? (
            <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
              idem={evalIdempotencyKey}
            </div>
          ) : null}
        </div>

        {runEval.error ? (
          <ErrorState error={runEval.error} onRetry={handleRunEval} />
        ) : null}

        {evalResult ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(evalResult.status)}>
                  {evalResult.status}
                </StatusPill>
              }
            >
              평가 결과
            </SectionLabel>
            <div className="mt-2 flex items-baseline gap-4">
              <div>
                <div className="text-[10px] tracking-[0.3px] text-muted-foreground uppercase">
                  score
                </div>
                <div className="font-mono text-2xl font-semibold text-foreground">
                  {evalResult.score.toFixed(2)}
                </div>
              </div>
              <StatusPill intent={evalResult.is_passed ? "success" : "danger"}>
                {evalResult.is_passed ? "통과" : "미달"}
              </StatusPill>
            </div>
            <div className="mt-2 space-y-0">
              <EvidenceRow
                label="eval run id"
                value={<MonoChip>{evalResult.eval_run_id}</MonoChip>}
              />
              <EvidenceRow
                label="suite id"
                value={<MonoChip>{evalResult.suite_id}</MonoChip>}
              />
            </div>
            {axes.length > 0 ? (
              <div className="mt-2">
                <SectionLabel>축별 결과 (axis)</SectionLabel>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {axes.map(([axis, info]) => (
                    <StatusPill
                      key={axis}
                      intent={info.passed ? "success" : "danger"}
                    >
                      {axis} · {(info.score ?? 0).toFixed(2)} (
                      {info.caseCount ?? 0})
                    </StatusPill>
                  ))}
                </div>
              </div>
            ) : null}
            <div className="mt-2">
              <SectionLabel>
                케이스 결과 ({evalResult.case_results.length})
              </SectionLabel>
              <DataTable
                className="mt-1.5"
                columns={CASE_COLUMNS}
                rows={evalResult.case_results as EvalCaseRow[]}
                rowKey={(row) => `${row.case_api_name}-${row.sample_index}`}
              />
            </div>
          </div>
        ) : (
          <EmptyState
            icon={FlaskConical}
            title="평가 결과가 없습니다"
            description="평가 스위트를 실행하면 축별·케이스별 점수와 통과 여부가 표시되고, 통과 시 릴리스 승격이 활성화됩니다."
          />
        )}
      </div>

      {/* 릴리스 */}
      <div className="space-y-3">
        <div className="rounded border bg-card p-3">
          <SectionLabel
            right={
              <Button
                size="sm"
                onClick={handlePromote}
                disabled={!canPromote || promoteRelease.isRunning}
              >
                <Rocket />
                승격
              </Button>
            }
          >
            릴리스 승격 (release)
          </SectionLabel>
          <p className="mt-1 text-[11px] text-muted-foreground">
            승격은 통과한 평가 실행(eval run)을 근거로만 가능합니다. 평가 채널과
            대상 채널이 일치해야 하므로, 평가에서 선택한 채널로 승격됩니다.
          </p>
          <div className="mt-2 grid grid-cols-1 gap-0.5">
            <EvidenceRow
              label="eval run id"
              value={
                evalResult ? (
                  <MonoChip>{evalResult.eval_run_id}</MonoChip>
                ) : (
                  <span className="text-muted-foreground">
                    평가 실행 후 연결됩니다
                  </span>
                )
              }
            />
            <EvidenceRow
              label="대상 채널"
              value={
                evaluatedChannel ? (
                  <StatusPill intent="info">{evaluatedChannel}</StatusPill>
                ) : (
                  <span className="text-muted-foreground">
                    평가 실행 후 결정
                  </span>
                )
              }
            />
          </div>
          {promoteIdempotencyKey ? (
            <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground">
              idem={promoteIdempotencyKey}
            </div>
          ) : null}
          {!canPromote ? (
            <div className="mt-2 text-[11px] text-muted-foreground">
              {evalResult && evaluatedChannel !== channel
                ? "채널을 변경했습니다. 선택한 채널로 평가를 다시 실행하세요."
                : "통과한 평가 실행이 있어야 승격할 수 있습니다."}
            </div>
          ) : null}
        </div>

        {promoteRelease.error ? (
          <ErrorState error={promoteRelease.error} onRetry={handlePromote} />
        ) : null}

        {promotion ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(promotion.status)}>
                  {promotion.status}
                </StatusPill>
              }
            >
              릴리스 결과
            </SectionLabel>
            <div className="mt-2 space-y-0">
              <EvidenceRow
                label="release id"
                value={<MonoChip>{promotion.release_id}</MonoChip>}
              />
              <EvidenceRow
                label="agent version"
                value={asText(promotion.agent_version_id)}
                mono
              />
              <EvidenceRow
                label="release channel"
                value={
                  <StatusPill intent="info">
                    {promotion.release_channel}
                  </StatusPill>
                }
              />
              <EvidenceRow
                label="eval run id"
                value={<MonoChip>{promotion.eval_run_id}</MonoChip>}
              />
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}
