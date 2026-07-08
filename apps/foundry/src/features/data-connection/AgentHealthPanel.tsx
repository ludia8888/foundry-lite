import type { SourceAgent } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Activity, HeartPulse, Plus, Server } from "lucide-react";

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

/** 에이전트 탭: 등록된 에이전트 상태/하트비트 모니터링 (메트릭 시계열은 future). */
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
    <div className="grid gap-4 p-4 md:grid-cols-2">
      {agents.map((agent) => (
        <AgentCard
          key={agent.agentId}
          agent={agent}
          onHeartbeat={() => void agentsQuery.reload()}
        />
      ))}
    </div>
  );
}

function AgentCard({
  agent,
  onHeartbeat,
}: {
  agent: SourceAgent;
  onHeartbeat: () => void;
}) {
  const client = useFoundryLiteClient();
  const heartbeat = useFoundryLiteMutation(
    (agentId: string) => client.sources.agents.heartbeat(agentId),
    { lockKey: (agentId) => `sources:agents:heartbeat:${agentId}` },
  );

  const handleHeartbeat = async () => {
    await heartbeat.execute(agent.agentId);
    onHeartbeat();
  };

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
        <AgentStat
          label="기능"
          value={`${Object.keys(agent.capabilities).length}개`}
        />
        <AgentStat label="등록" value={formatTimestamp(agent.createdAt)} />
      </div>
      <div className="flex items-center justify-between border-t px-3 py-2">
        <span className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Activity className="size-3" /> 메모리/디스크/부하 메트릭
          <StatusPill intent="neutral">future</StatusPill>
        </span>
        <Button
          variant="outline"
          size="sm"
          disabled={heartbeat.isRunning}
          onClick={() => void handleHeartbeat()}
        >
          <HeartPulse className="size-3.5" /> 하트비트 기록
        </Button>
      </div>
      {heartbeat.error ? (
        <div className="border-t p-2">
          <ErrorState error={heartbeat.error} />
        </div>
      ) : null}
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
