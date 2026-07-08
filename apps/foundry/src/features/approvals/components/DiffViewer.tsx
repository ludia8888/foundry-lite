import { FileCode2, GitCompareArrows } from "lucide-react";
import { useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import type { StatusIntent } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import type {
  DiffChangeKind,
  OntologyDiffModel,
  PipelineDiffModel,
} from "../lib/diff-model";
import { EvidenceRow } from "./EvidenceRow";

const CHANGE_INTENT: Record<DiffChangeKind, StatusIntent> = {
  added: "success",
  modified: "info",
  removed: "danger",
  unchanged: "neutral",
};

const CHANGE_LABEL: Record<DiffChangeKind, string> = {
  added: "추가",
  modified: "변경",
  removed: "삭제",
  unchanged: "동일",
};

function compatibilityIntent(value: string | null): StatusIntent {
  if (value === "compatible") return "success";
  if (value === "breaking" || value === "incompatible") return "danger";
  return "neutral";
}

function CodeBlock({ label, text }: { label: string; text: string }) {
  const [isOpen, setIsOpen] = useState(false);
  return (
    <div className="rounded border bg-muted/30">
      <button
        type="button"
        onClick={() => setIsOpen((current) => !current)}
        className="flex w-full items-center gap-1.5 px-2.5 py-1.5 text-[11px] font-medium text-muted-foreground hover:text-foreground"
      >
        <FileCode2 className="size-3.5" />
        {label}
        <span className="ml-auto">{isOpen ? "접기" : "펼치기"}</span>
      </button>
      {isOpen ? (
        <pre className="max-h-72 overflow-auto border-t bg-card px-2.5 py-2 font-mono text-[11px] leading-relaxed text-foreground/90">
          {text}
        </pre>
      ) : null}
    </div>
  );
}

export function OntologyDiffViewer({ diff }: { diff: OntologyDiffModel }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <GitCompareArrows className="size-3.5 text-muted-foreground" />
        <span className="section-label">스키마 변경 diff</span>
        {diff.branchName ? (
          <span className="ml-auto truncate font-mono text-[11px] text-muted-foreground">
            branch:{diff.branchName}
          </span>
        ) : null}
      </div>

      {diff.changes.length > 0 ? (
        <div className="divide-y rounded border">
          {diff.changes.map((change) => (
            <div
              key={`${change.kind}:${change.apiName}`}
              className="flex items-center justify-between gap-2 px-2.5 py-1.5"
            >
              <div className="flex min-w-0 items-center gap-2">
                <span className="section-label shrink-0">{change.kind}</span>
                <span className="truncate font-mono text-[12px]">
                  {change.apiName}
                </span>
              </div>
              <StatusPill intent={CHANGE_INTENT[change.change]}>
                {CHANGE_LABEL[change.change]}
              </StatusPill>
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded border border-dashed px-2.5 py-2 text-[11px] text-muted-foreground">
          migration plan에 리소스 단위 변경 항목이 없습니다. 아래 검증 요약과
          YAML 스냅샷으로 변경 범위를 확인하세요.
        </p>
      )}

      <div>
        <div className="mb-1 section-label">마이그레이션 검증</div>
        <div className="rounded border px-2.5">
          <EvidenceRow label="migration status">
            <StatusPill intent={compatibilityIntent(diff.migrationStatus)}>
              {diff.migrationStatus ?? "-"}
            </StatusPill>
          </EvidenceRow>
          <EvidenceRow label="consumer compatibility">
            <StatusPill
              intent={compatibilityIntent(diff.consumerCompatibility)}
            >
              {diff.consumerCompatibility ?? "-"}
            </StatusPill>
          </EvidenceRow>
          <EvidenceRow label="sdk compatibility">
            <StatusPill intent={compatibilityIntent(diff.sdkCompatibility)}>
              {diff.sdkCompatibility ?? "-"}
            </StatusPill>
          </EvidenceRow>
          <EvidenceRow label="object / link / action">
            {diff.objectTypeCount ?? "-"} / {diff.linkTypeCount ?? "-"} /{" "}
            {diff.actionTypeCount ?? "-"}
          </EvidenceRow>
          <EvidenceRow label="blocked changes">
            <StatusPill intent={diff.hasBlockedChanges ? "danger" : "success"}>
              {diff.hasBlockedChanges ? "있음" : "없음"}
            </StatusPill>
          </EvidenceRow>
        </div>
      </div>

      {diff.yamlText ? (
        <CodeBlock label="제안 YAML 스냅샷" text={diff.yamlText} />
      ) : null}
    </div>
  );
}

export function PipelineDiffViewer({ diff }: { diff: PipelineDiffModel }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <GitCompareArrows className="size-3.5 text-muted-foreground" />
        <span className="section-label">파이프라인 그래프 diff</span>
      </div>

      <div className="divide-y rounded border">
        {diff.nodes.map((node) => (
          <div
            key={node.id}
            className={cn(
              "flex items-center justify-between gap-2 px-2.5 py-1.5",
            )}
          >
            <div className="flex min-w-0 items-center gap-2">
              <StatusPill intent="neutral" className="shrink-0">
                {node.type}
              </StatusPill>
              <span className="truncate text-[12px]">{node.label}</span>
            </div>
            {node.outputRef ? (
              <span className="shrink-0 font-mono text-[11px] text-muted-foreground">
                {node.outputRef}
              </span>
            ) : null}
          </div>
        ))}
      </div>

      <div className="rounded border px-2.5">
        <EvidenceRow label="graph nodes / edges">
          {diff.nodes.length} / {diff.edgeCount}
        </EvidenceRow>
        <EvidenceRow label="graph fingerprint">
          {diff.graphFingerprint
            ? `sha256:${diff.graphFingerprint.slice(0, 16)}…`
            : "-"}
        </EvidenceRow>
        {diff.decision ? (
          <EvidenceRow label="decision" isMono={false}>
            {diff.decision}
            {diff.decisionComment ? ` — ${diff.decisionComment}` : ""}
          </EvidenceRow>
        ) : null}
      </div>
    </div>
  );
}
