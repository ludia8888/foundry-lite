import type { SourceEgressAttempt } from "@foundry-lite/sdk";
import { ArrowDownToLine, ArrowUpFromLine, Network } from "lucide-react";
import type { ReactNode } from "react";

import { StatusPill } from "@/components/shared/StatusPill";

import {
  formatBytes,
  formatTimestamp,
  statusIntent,
  statusLabel,
} from "../source-model";
import { SettingsCard } from "./SourceSettingsUi";

interface SourceEgressAttemptHistoryProps {
  attempts: readonly SourceEgressAttempt[];
}

/** Foundry network observability-style TCP egress evidence, newest first. */
export function SourceEgressAttemptHistory({
  attempts,
}: SourceEgressAttemptHistoryProps) {
  return (
    <SettingsCard
      title="Egress 로그"
      description="연결 진단이 남긴 TCP 경로 증거입니다. Egress 성공은 TLS·인증 성공과 별개입니다."
      actions={
        <span className="text-[10px] text-muted-foreground">
          최근 {attempts.length}건
        </span>
      }
    >
      {attempts.length === 0 ? (
        <div className="flex items-center gap-2 px-4 py-5 text-xs text-muted-foreground">
          <Network aria-hidden className="size-4" /> 아직 egress 시도가
          없습니다.
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[880px] text-left text-[11px]">
            <thead className="border-b bg-muted/35 text-[10px] text-muted-foreground uppercase">
              <tr>
                <HeaderCell>시각 / 결과</HeaderCell>
                <HeaderCell>경로</HeaderCell>
                <HeaderCell>Destination</HeaderCell>
                <HeaderCell>전송량</HeaderCell>
                <HeaderCell>Duration</HeaderCell>
                <HeaderCell>Response flags</HeaderCell>
                <HeaderCell>Connection ID</HeaderCell>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/60">
              {attempts.map((attempt) => (
                <EgressRow key={attempt.connectionTestId} attempt={attempt} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SettingsCard>
  );
}

function EgressRow({ attempt }: { attempt: SourceEgressAttempt }) {
  const resources = attempt.networkResources;
  const policy = textValue(resources["networkPolicy"]);
  const agent = textValue(resources["agentId"]);
  return (
    <tr className="align-top hover:bg-muted/20">
      <BodyCell>
        <div className="flex items-center gap-2">
          <StatusPill intent={statusIntent(attempt.status)}>
            {statusLabel(attempt.status)}
          </StatusPill>
          <span>{formatTimestamp(attempt.completedAt ?? attempt.startedAt)}</span>
        </div>
        <div className="mt-1 font-mono text-[10px] text-muted-foreground">
          request {attempt.requestId ?? "—"}
        </div>
      </BodyCell>
      <BodyCell>
        <div className="font-medium">
          {attempt.origin === "agent-proxy" ? "Agent proxy" : "Direct egress"}
        </div>
        <div className="mt-1 font-mono text-[10px] text-muted-foreground">
          {policy ?? "no policy"}
          {agent ? ` · ${agent}` : ""}
        </div>
      </BodyCell>
      <BodyCell>
        <span className="font-mono">TCP :{attempt.destinationPort || "—"}</span>
      </BodyCell>
      <BodyCell>
        <div className="flex items-center gap-1 font-mono">
          <ArrowUpFromLine aria-hidden className="size-3 text-muted-foreground" />
          {formatBytes(attempt.bytesSent)}
        </div>
        <div className="mt-1 flex items-center gap-1 font-mono">
          <ArrowDownToLine aria-hidden className="size-3 text-muted-foreground" />
          {formatBytes(attempt.bytesReceived)}
        </div>
      </BodyCell>
      <BodyCell>
        <span className="font-mono">{attempt.durationMs} ms</span>
      </BodyCell>
      <BodyCell>
        <StatusPill
          intent={attempt.responseFlags === "NONE" ? "success" : "danger"}
        >
          {attempt.responseFlags}
        </StatusPill>
      </BodyCell>
      <BodyCell>
        <span className="block max-w-52 truncate font-mono text-[10px] text-muted-foreground">
          {attempt.connectionId}
        </span>
      </BodyCell>
    </tr>
  );
}

function HeaderCell({ children }: { children: ReactNode }) {
  return <th className="px-3 py-2 font-medium">{children}</th>;
}

function BodyCell({ children }: { children: ReactNode }) {
  return <td className="px-3 py-3">{children}</td>;
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
