import type { SourceAgent } from "@foundry-lite/sdk";
import { Activity, Plus, RefreshCw, Server, TerminalSquare } from "lucide-react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import { formatTimestamp, statusIntent, statusLabel } from "./source-model";
import { useSourceAgents } from "./use-source-queries";

interface AgentHealthPanelProps {
  onCreateSource: () => void;
}

/** 에이전트 탭: 실제 daemon heartbeat와 CONNECT endpoint 상태를 모니터링한다. */
export function AgentHealthPanel({ onCreateSource }: AgentHealthPanelProps) {
  const agentsQuery = useSourceAgents();

  if (agentsQuery.error) {
    return (
      <div className="p-4">
        <ErrorState
          error={agentsQuery.error}
          onRetry={() => void agentsQuery.reload()}
        />
      </div>
    );
  }
  if (agentsQuery.isLoading && !agentsQuery.data) {
    return <LoadingState rowCount={4} className="p-4" />;
  }

  const agents = agentsQuery.data ?? [];
  if (agents.length === 0) {
    return (
      <div className="p-4">
        <EmptyState
          icon={Server}
          title="등록된 에이전트가 없습니다"
          description="새 소스 위저드에서 '에이전트 경유' 연결을 선택하면 에이전트가 등록되고 여기에서 상태를 모니터링할 수 있습니다."
          action={
            <Button size="sm" onClick={onCreateSource}>
              <Plus className="size-3.5" /> 새 소스
            </Button>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-start justify-between gap-3 rounded border bg-muted/20 p-3">
        <div className="flex gap-2">
          <TerminalSquare className="mt-0.5 size-4 text-primary" />
          <div>
            <div className="text-xs font-semibold">Source Agent daemon</div>
            <p className="mt-1 text-[11px] text-muted-foreground">
              Agent 프로세스가 스스로 등록하고 heartbeat를 전송합니다. 화면에서
              상태를 임의로 online으로 바꾸지 않습니다.
            </p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void agentsQuery.reload()}
        >
          <RefreshCw className="size-3.5" /> 새로고침
        </Button>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {agents.map((agent) => (
          <AgentCard key={agent.agentId} agent={agent} />
        ))}
      </div>
    </div>
  );
}

function AgentCard({ agent }: { agent: SourceAgent }) {
  const proxyUrl = textValue(agent.networkSummary["proxyUrl"]);
  const heartbeatMode = textValue(agent.networkSummary["heartbeatMode"]);
  return (
    <div className="rounded border bg-card">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="flex size-7 items-center justify-center rounded bg-primary/10">
          <Server className="size-3.5 text-primary" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold">
            {agent.displayName}
          </div>
          <div className="truncate font-mono text-[11px] text-muted-foreground">
            {agent.agentId}
          </div>
        </div>
        <StatusPill intent={statusIntent(agent.status)}>
          {statusLabel(agent.status)}
        </StatusPill>
      </div>
      <div className="grid grid-cols-2 gap-2 p-3">
        <AgentStat label="모드" value={agent.mode} />
        <AgentStat
          label="마지막 하트비트"
          value={formatTimestamp(agent.lastHeartbeatAt)}
        />
        <AgentStat label="CONNECT endpoint" value={proxyUrl ?? "미등록"} />
        <AgentStat label="Heartbeat" value={heartbeatMode ?? "미등록"} />
      </div>
      <div className="flex items-center gap-1.5 border-t px-3 py-2 text-[11px] text-muted-foreground">
        <Activity className="size-3" />
        {agent.status.toLowerCase() === "online"
          ? "daemon heartbeat가 90초 freshness 기준 안에 있습니다."
          : "Agent daemon 또는 control-plane 연결을 확인하세요."}
      </div>
      <div className="flex items-center justify-between border-t px-3 py-2">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          기능 {Object.keys(agent.capabilities).length}개 · 등록 {formatTimestamp(agent.createdAt)}
        </span>
        <StatusPill intent={proxyUrl ? "success" : "danger"}>
          {proxyUrl ? "터널 구성됨" : "proxy URL 없음"}
        </StatusPill>
      </div>
    </div>
  );
}

function AgentStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded border bg-muted/30 p-2">
      <div className="text-[10px] text-muted-foreground uppercase">{label}</div>
      <div className="mt-0.5 truncate font-mono text-[11px]">{value}</div>
    </div>
  );
}

function textValue(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}
