import { ArrowDownToLine, ArrowUpFromLine, Network } from "lucide-react";
import type { ReactNode } from "react";

import { StatusPill } from "@/components/shared/StatusPill";

import { formatBytes, readNumberField, readTextField } from "../source-model";

interface SyncNetworkEvidenceCardProps {
  evidence: Record<string, unknown>;
}

/** Dataset commit transaction에서 다시 읽은 실제 sync egress 경로 증거. */
export function SyncNetworkEvidenceCard({
  evidence,
}: SyncNetworkEvidenceCardProps) {
  const origin = readTextField(evidence, "origin");
  const networkType = readTextField(evidence, "networkType");
  const responseFlags = readTextField(evidence, "responseFlags") ?? "UNKNOWN";
  const resources = recordValue(evidence.networkResources);
  const policy = readTextField(resources, "networkPolicy");
  const agent = readTextField(resources, "agentId");
  const isAgentProxy = origin === "agent-proxy";
  const pageCount = readNumberField(evidence, "pageCount");
  const connectionCount = readNumberField(evidence, "connectionCount");

  return (
    <section
      data-testid="sync-network-evidence"
      className="border-t bg-primary/[0.025] p-3"
      aria-label="동기화 네트워크 증거"
    >
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-1.5 text-xs font-semibold">
            <Network aria-hidden className="size-3.5 text-primary" />
            실제 빌드 네트워크 경로
          </div>
          <p className="mt-1 text-[10px] text-muted-foreground">
            Dataset commit 트랜잭션에 저장된 전송 결과입니다. 설정값이 아니라 이 빌드가 실제로 사용한 경로입니다.
          </p>
        </div>
        <StatusPill intent={responseFlags === "NONE" ? "success" : "danger"}>
          {responseFlags === "NONE" ? "전송 성공" : responseFlags}
        </StatusPill>
      </div>

      <dl className="grid gap-x-5 gap-y-2 text-[11px] sm:grid-cols-2 lg:grid-cols-5">
        <EvidenceItem
          label="경로"
          value={isAgentProxy ? "Agent proxy" : networkType === "direct" ? "Direct egress" : origin ?? "—"}
          detail={[policy, agent].filter(Boolean).join(" · ") || undefined}
        />
        <EvidenceItem
          label="Destination"
          value={`TCP :${readNumberField(evidence, "destinationPort") ?? "—"}`}
          detail={`${readNumberField(evidence, "durationMs") ?? "—"} ms`}
        />
        <EvidenceItem
          label="전송량"
          value={
            <span className="flex items-center gap-1">
              <ArrowUpFromLine aria-hidden className="size-3 text-muted-foreground" />
              {formatBytes(readNumberField(evidence, "bytesSent"))}
              <ArrowDownToLine aria-hidden className="ml-1 size-3 text-muted-foreground" />
              {formatBytes(readNumberField(evidence, "bytesReceived"))}
            </span>
          }
        />
        <EvidenceItem
          label="페이지 / 연결"
          value={`${pageCount ?? 1} pages`}
          detail={`${connectionCount ?? pageCount ?? 1} connections`}
        />
        <EvidenceItem
          label="Connection ID"
          value={readTextField(evidence, "connectionId") ?? "—"}
          isCode
        />
      </dl>
    </section>
  );
}

function EvidenceItem({
  label,
  value,
  detail,
  isCode = false,
}: {
  label: string;
  value: ReactNode;
  detail?: string;
  isCode?: boolean;
}) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] text-muted-foreground">{label}</dt>
      <dd className={`mt-0.5 break-all ${isCode ? "font-mono text-[10px]" : "font-medium"}`}>
        {value}
      </dd>
      {detail ? <dd className="mt-0.5 font-mono text-[10px] text-muted-foreground">{detail}</dd> : null}
    </div>
  );
}

function recordValue(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object"
    ? (value as Record<string, unknown>)
    : {};
}
