import { Bot, Play } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { OntologyRequiredState } from "@/components/shared/OntologyRequiredState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

import { phaseTone, toCitationViews } from "../aip-model";
import type { AipWorkspace } from "../use-aip-workspace";
import { AnswerWithCitations } from "./AnswerWithCitations";
import { CitationCard } from "./CitationCard";
import { MonoChip, SectionLabel } from "./Evidence";
import { RunDetailEvidence } from "./RunDetailEvidence";

const SAMPLE_PROMPTS = [
  "Explain Order O-1001 for the operator.",
  "Summarize the expedited payment terms.",
  "Order O-1001의 상태와 근거를 요약해줘.",
] as const;

/** 에이전트 실행 콘솔: 프롬프트 → 실행 → 응답 + inline citation + 운영 근거. */
export function AgentRunConsole({ workspace }: { workspace: AipWorkspace }) {
  const { agent, runAgent, agentIdempotencyKey, loadPromptArtifact } =
    workspace;
  const [message, setMessage] = useState<string>(SAMPLE_PROMPTS[0]);
  const [selectedOrder, setSelectedOrder] = useState<number | null>(null);
  const cardRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  const citations = useMemo(
    () => toCitationViews(agent.aipResult?.citations ?? []),
    [agent.aipResult],
  );
  const answer = agent.aipResult?.answer ?? null;
  const isOntologyMissingResult = agent.aipResult?.error
    ? isOntologyContextUnavailable(agent.aipResult.error)
    : false;

  const handleRun = () => {
    if (!message.trim() || agent.isRunning) return;
    setSelectedOrder(null);
    void runAgent(message.trim());
  };

  const handleSelectCitation = (order: number) => {
    setSelectedOrder(order);
    const node = cardRefs.current.get(order);
    if (node) {
      node.scrollIntoView({ block: "nearest" });
      node.focus({ preventScroll: true });
    }
  };

  const hasRun = agent.aipResult !== null || agent.error !== null;

  return (
    <div className="grid gap-3 lg:grid-cols-[minmax(0,420px)_minmax(0,1fr)]">
      {/* 왼쪽: 프롬프트 입력 */}
      <div className="space-y-3">
        <div className="rounded border bg-card p-3">
          <SectionLabel
            right={
              <StatusPill intent={phaseTone(agent.phase)}>
                {agent.phase}
              </StatusPill>
            }
          >
            에이전트 프롬프트
          </SectionLabel>
          <div className="mt-2 space-y-2">
            <div className="grid grid-cols-1 gap-1.5 text-[11px]">
              <KV label="agent version" value="agent.order-ops.v1" />
              <KV label="prompt version" value="prompt-order-copilot@v1" />
              <KV label="partition" value="tenant-demo:internal" />
            </div>
            <Textarea
              value={message}
              onChange={(event) => setMessage(event.target.value)}
              rows={5}
              placeholder="에이전트에게 보낼 메시지를 입력하세요."
              className="resize-none font-mono text-[12px]"
            />
            <div className="flex flex-wrap gap-1">
              {SAMPLE_PROMPTS.map((sample) => (
                <button
                  key={sample}
                  type="button"
                  onClick={() => setMessage(sample)}
                  className="rounded border border-dashed px-1.5 py-0.5 text-[10px] text-muted-foreground hover:bg-muted"
                >
                  {sample.length > 28 ? `${sample.slice(0, 28)}…` : sample}
                </button>
              ))}
            </div>
            <div className="flex items-center justify-between gap-2">
              {agentIdempotencyKey ? (
                <span
                  className="min-w-0 truncate font-mono text-[10px] text-muted-foreground"
                  title={agentIdempotencyKey}
                >
                  idem={agentIdempotencyKey}
                </span>
              ) : (
                <span className="text-[10px] text-muted-foreground">
                  멱등키는 실행 시 발급됩니다.
                </span>
              )}
              <Button
                size="sm"
                onClick={handleRun}
                disabled={agent.isRunning || !message.trim()}
              >
                <Play />
                실행
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* 오른쪽: 응답 + 근거 */}
      <div className="space-y-3">
        {!hasRun ? (
          <EmptyState
            icon={Bot}
            title="아직 실행 결과가 없습니다"
            description="왼쪽에서 프롬프트를 입력하고 실행하면 AI 응답과 근거 artifact, 연결된 운영 실행 evidence가 함께 표시됩니다."
          />
        ) : null}

        {agent.error ? (
          <ErrorState
            error={agent.error}
            onRetry={() => void runAgent(message.trim())}
          />
        ) : null}

        {agent.aipResult ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(agent.aipResult.runStatus)}>
                  {agent.aipResult.runStatus}
                </StatusPill>
              }
            >
              AI 응답
            </SectionLabel>
            <div className="mt-2">
              {isOntologyMissingResult ? (
                <OntologyRequiredState className="border-0 p-0" />
              ) : answer ? (
                <AnswerWithCitations
                  answer={answer}
                  citations={citations}
                  onSelectCitation={handleSelectCitation}
                />
              ) : (
                <div className="rounded border border-dashed bg-muted/40 p-3 text-[12px] text-muted-foreground">
                  이 실행은 응답 본문을 생성하지 않았습니다. 아래 운영 근거에서
                  실패 사유와 request id, prompt artifact를 확인하세요.
                </div>
              )}
            </div>
            {agent.aipResult.error && !isOntologyMissingResult ? (
              <div className="mt-2 rounded border border-destructive/30 bg-destructive/5 p-2">
                <div className="section-label mb-1 text-destructive">
                  실행 실패 사유
                </div>
                <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[11px] text-foreground/80">
                  <span>
                    reason={String(agent.aipResult.error.reason ?? "-")}
                  </span>
                  <span>
                    detail={String(agent.aipResult.error.detail ?? "-")}
                  </span>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {citations.length > 0 ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={<MonoChip>{`${citations.length} refs`}</MonoChip>}
            >
              근거 citation
            </SectionLabel>
            <div className="mt-2 space-y-1.5">
              {citations.map((citation) => (
                <CitationCard
                  key={citation.order}
                  citation={citation}
                  isSelected={selectedOrder === citation.order}
                  ref={(node) => {
                    if (node) cardRefs.current.set(citation.order, node);
                    else cardRefs.current.delete(citation.order);
                  }}
                />
              ))}
            </div>
          </div>
        ) : null}

        {agent.operationsDetail ? (
          <RunDetailEvidence
            detail={agent.operationsDetail}
            loadPromptArtifact={loadPromptArtifact}
          />
        ) : null}
      </div>
    </div>
  );
}

function isOntologyContextUnavailable(error: {
  reason?: string;
  detail?: string;
}): boolean {
  const reason = String(error.reason ?? "").toLowerCase();
  const detail = String(error.detail ?? "").toLowerCase();
  return (
    detail.includes("active ontology not found") ||
    (reason === "context_source_unavailable" && detail.includes("ontology"))
  );
}

function KV({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="min-w-0 truncate font-mono text-foreground/90">
        {value}
      </span>
    </div>
  );
}
