import type {
  PromptArtifactReadResult,
  RuntimeRunDetail,
} from "@foundry-lite/sdk";
import { FileText, Loader2 } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { operationsRunHref } from "@/lib/operations-links";

import { asText, phaseTone, shortHash } from "../aip-model";
import { EvidenceRow, MonoChip, SectionLabel } from "./Evidence";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Dict)
    : null;
}

function list(value: unknown): Dict[] {
  return Array.isArray(value)
    ? value.filter((item): item is Dict => dict(item) !== null)
    : [];
}

/**
 * 에이전트/빌더 실행에 연결된 operations run detail evidence.
 * run id / trace id / prompt hash / context item / prompt artifact를 숨기지 않는다.
 */
export function RunDetailEvidence({
  detail,
  loadPromptArtifact,
}: {
  detail: RuntimeRunDetail;
  loadPromptArtifact: (
    aiRunId: string,
    artifactId: string,
  ) => Promise<PromptArtifactReadResult>;
}) {
  const ai = dict(detail.ai);
  const run = dict(ai?.run);
  const summary = dict(ai?.summary);
  const contextItems = list(ai?.contextItems);
  const events = list(ai?.events);
  const promptArtifacts = list(ai?.promptArtifacts);

  const aiRunId = asText(run?.id) !== "-" ? String(run?.id) : detail.runId;
  const requestId = asText(run?.request_id);
  const traceId = asText(run?.trace_id ?? detail.correlationId);
  const status = asText(run?.status ?? detail.status);
  const operationsHref = operationsRunHref(detail.runId, detail.runType);
  const apiDetailPath = `/api/operations/runs/${detail.runType}/${detail.runId}`;

  return (
    <div className="space-y-3">
      <div className="rounded border bg-card p-3">
        <SectionLabel
          right={<StatusPill intent={phaseTone(status)}>{status}</StatusPill>}
        >
          연결된 운영 실행 (operations run)
        </SectionLabel>
        <div className="mt-2 space-y-0">
          <EvidenceRow
            label="run id"
            value={
              <Link
                to={operationsHref}
                title={detail.runId}
                className="inline-block max-w-full hover:underline"
              >
                <MonoChip>{aiRunId}</MonoChip>
              </Link>
            }
          />
          <EvidenceRow
            label="request id"
            value={<MonoChip>{requestId}</MonoChip>}
          />
          <EvidenceRow
            label="trace id"
            value={<MonoChip>{traceId}</MonoChip>}
          />
          <EvidenceRow
            label="detail path"
            value={
              <Link
                to={operationsHref}
                title={apiDetailPath}
                className="inline-block max-w-full hover:underline"
              >
                <MonoChip>{apiDetailPath}</MonoChip>
              </Link>
            }
          />
          <EvidenceRow
            label="resolved model"
            value={asText(run?.resolved_model_id)}
            mono
          />
          <EvidenceRow
            label="prompt version"
            value={asText(run?.prompt_version_id)}
            mono
          />
          <EvidenceRow
            label="compiled prompt"
            value={<MonoChip>{shortHash(run?.compiled_prompt_hash)}</MonoChip>}
          />
        </div>
      </div>

      {summary ? (
        <div className="rounded border bg-card p-3">
          <SectionLabel>실행 요약</SectionLabel>
          <div className="mt-2 grid grid-cols-2 gap-x-4 sm:grid-cols-4">
            <Metric label="컨텍스트" value={asText(summary.contextItemCount)} />
            <Metric label="모델 호출" value={asText(summary.modelCallCount)} />
            <Metric label="툴 호출" value={asText(summary.toolCallCount)} />
            <Metric label="인용" value={asText(summary.citationCount)} />
            <Metric label="입력 토큰" value={asText(summary.inputTokens)} />
            <Metric label="출력 토큰" value={asText(summary.outputTokens)} />
            <Metric
              label="prompt artifact"
              value={asText(summary.promptArtifactCount)}
            />
            <Metric label="지연(ms)" value={asText(summary.latencyMs)} />
          </div>
        </div>
      ) : null}

      {contextItems.length > 0 ? (
        <div className="rounded border bg-card p-3">
          <SectionLabel>검색된 컨텍스트 ({contextItems.length})</SectionLabel>
          <div className="mt-2 space-y-1.5">
            {contextItems.map((item, index) => (
              <div
                key={asText(item.id) + index}
                className="rounded border border-dashed bg-muted/30 p-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <MonoChip>{asText(item.source_resource_id)}</MonoChip>
                  <StatusPill intent="neutral">{asText(item.kind)}</StatusPill>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
                  <span>method={asText(item.retrieval_method)}</span>
                  <span>ver={asText(item.source_version)}</span>
                  <span>hash={shortHash(item.content_hash)}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      <PromptArtifactPanel
        aiRunId={aiRunId}
        artifacts={promptArtifacts}
        loadPromptArtifact={loadPromptArtifact}
      />

      {events.length > 0 ? (
        <div className="rounded border bg-card p-3">
          <SectionLabel>실행 이벤트 타임라인 ({events.length})</SectionLabel>
          <ol className="mt-2 space-y-1">
            {events.map((event, index) => (
              <li
                key={asText(event.id) + index}
                className="flex items-center gap-2 text-[11px]"
              >
                <span className="w-5 shrink-0 text-right font-mono text-muted-foreground">
                  {asText(event.sequence)}
                </span>
                <StatusPill intent="info">
                  {asText(event.event_type)}
                </StatusPill>
                <span className="truncate font-mono text-[10px] text-muted-foreground">
                  {shortHash(event.payload_hash)}
                </span>
              </li>
            ))}
          </ol>
        </div>
      ) : null}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className="py-1">
      <div className="text-[10px] tracking-[0.3px] text-muted-foreground uppercase">
        {label}
      </div>
      <div className="font-mono text-[13px] font-semibold text-foreground">
        {value}
      </div>
    </div>
  );
}

function PromptArtifactPanel({
  aiRunId,
  artifacts,
  loadPromptArtifact,
}: {
  aiRunId: string;
  artifacts: Dict[];
  loadPromptArtifact: (
    aiRunId: string,
    artifactId: string,
  ) => Promise<PromptArtifactReadResult>;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  const [content, setContent] = useState<PromptArtifactReadResult | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [isLoading, setIsLoading] = useState(false);

  const handleOpen = async (artifactId: string) => {
    setOpenId(artifactId);
    setContent(null);
    setError(null);
    setIsLoading(true);
    try {
      const result = await loadPromptArtifact(aiRunId, artifactId);
      setContent(result);
    } catch (caught) {
      setError(caught);
    } finally {
      setIsLoading(false);
    }
  };

  if (artifacts.length === 0) {
    return (
      <div className="rounded border bg-card p-3">
        <SectionLabel>prompt artifact</SectionLabel>
        <p className="mt-2 text-[12px] text-muted-foreground">
          이 실행에는 저장된 컴파일 프롬프트 artifact가 없습니다. 실패가 모델
          호출 이전 단계에서 발생하면 artifact가 기록되지 않을 수 있습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="rounded border bg-card p-3">
      <SectionLabel>prompt artifact ({artifacts.length})</SectionLabel>
      <div className="mt-2 space-y-1.5">
        {artifacts.map((artifact, index) => {
          const artifactId = asText(artifact.id);
          const isOpen = openId === artifactId;
          return (
            <div
              key={artifactId + index}
              className="rounded border border-dashed bg-muted/30 p-2"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <FileText className="size-3.5 shrink-0 text-muted-foreground" />
                  <MonoChip>{shortHash(artifact.content_hash)}</MonoChip>
                </div>
                <Button
                  size="xs"
                  variant="outline"
                  disabled={isLoading && isOpen}
                  onClick={() => void handleOpen(artifactId)}
                >
                  {isLoading && isOpen ? (
                    <Loader2 className="animate-spin" />
                  ) : null}
                  프롬프트 열람
                </Button>
              </div>
              {isOpen ? (
                <div className="mt-2">
                  {error ? (
                    <ErrorState error={error} />
                  ) : isLoading ? (
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <Loader2 className="size-3 animate-spin" />
                      artifact 복호화 중…
                    </div>
                  ) : content ? (
                    <div className="space-y-1">
                      <div className="flex flex-wrap gap-x-3 gap-y-0.5 font-mono text-[10px] text-muted-foreground">
                        <span>artifact={content.artifactId}</span>
                        <span>marking={content.exportMarking}</span>
                        <span>hash={shortHash(content.contentHash)}</span>
                      </div>
                      <pre className="max-h-64 overflow-auto rounded bg-foreground/5 p-2 font-mono text-[10px] leading-relaxed whitespace-pre-wrap text-foreground/80">
                        {content.plaintext}
                      </pre>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}
