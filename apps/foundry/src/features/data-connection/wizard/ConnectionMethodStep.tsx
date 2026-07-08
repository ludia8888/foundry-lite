import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { Cloud, Globe, Server, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import {
  isValidIdentifier,
  sanitizeIdentifier,
  statusIntent,
  statusLabel,
} from "../source-model";
import { useSourceAgents } from "../use-source-queries";
import { WizardField, WizardStepFooter } from "./WizardFields";

export type ConnectionMode = "direct" | "agent_proxy";

interface ConnectionMethodStepProps {
  templateDisplayName: string;
  supportsAgent: boolean;
  connectionMode: ConnectionMode;
  onConnectionModeChange: (mode: ConnectionMode) => void;
  selectedAgentId: string | null;
  onSelectedAgentIdChange: (agentId: string | null) => void;
  onContinue: () => void;
}

/**
 * 2단계 연결 방식: "에이전트를 통해" vs "직접 연결" 라디오 카드
 * (Palantir set-up-source / set-up-direct-connection 구조).
 * 에이전트 모드는 sources.agents.register 등록/선택과 연계된다.
 */
export function ConnectionMethodStep({
  templateDisplayName,
  supportsAgent,
  connectionMode,
  onConnectionModeChange,
  selectedAgentId,
  onSelectedAgentIdChange,
  onContinue,
}: ConnectionMethodStepProps) {
  const canContinue = connectionMode === "direct" || selectedAgentId !== null;

  return (
    <div className="space-y-4">
      <div>
        <h2 className="text-base font-semibold">
          데이터 소스 연결 방식을 선택하세요
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {templateDisplayName} 소스가 Foundry에 도달하는 네트워크 경로를
          선택합니다.
        </p>
      </div>
      <div className="grid gap-3 md:grid-cols-2">
        <ConnectionModeCard
          isSelected={connectionMode === "agent_proxy"}
          isDisabled={!supportsAgent}
          onSelect={() => onConnectionModeChange("agent_proxy")}
          title="에이전트를 통해"
          description="사설 네트워크에 설치된 중계 에이전트를 통해 소스에 접속합니다."
          note={
            supportsAgent
              ? "에이전트 호스트→소스, 에이전트 호스트→Foundry 두 개의 연결이 필요합니다. 폐쇄망 소스에 적합합니다."
              : "이 소스 유형은 직접 연결만 지원합니다."
          }
          icons={
            <>
              <Cloud className="size-6" />
              <DottedLine />
              <Server className="size-6" />
              <DottedLine />
              <Globe className="size-6" />
            </>
          }
        />
        <ConnectionModeCard
          isSelected={connectionMode === "direct"}
          onSelect={() => onConnectionModeChange("direct")}
          title="직접 연결"
          description="Foundry에서 인터넷을 통해 데이터 소스에 직접 접속합니다."
          note="Foundry→소스 단일 네트워크 연결만 필요합니다. 공용 네트워크의 소스에 권장합니다."
          icons={
            <>
              <Cloud className="size-6" />
              <DottedLine />
              <Globe className="size-6" />
            </>
          }
        />
      </div>
      {connectionMode === "agent_proxy" ? (
        <AgentSection
          selectedAgentId={selectedAgentId}
          onSelectedAgentIdChange={onSelectedAgentIdChange}
        />
      ) : null}
      <WizardStepFooter
        right={
          <Button size="sm" disabled={!canContinue} onClick={onContinue}>
            계속
          </Button>
        }
      />
    </div>
  );
}

function DottedLine() {
  return <span className="w-8 border-t-2 border-dotted border-border" />;
}

function ConnectionModeCard({
  isSelected,
  isDisabled = false,
  onSelect,
  title,
  description,
  note,
  icons,
}: {
  isSelected: boolean;
  isDisabled?: boolean;
  onSelect: () => void;
  title: string;
  description: string;
  note: string;
  icons: ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={isDisabled}
      onClick={onSelect}
      className={cn(
        "rounded border bg-card text-left transition-colors",
        isDisabled && "cursor-not-allowed opacity-60",
        !isDisabled && isSelected && "border-primary bg-primary/5",
        !isDisabled && !isSelected && "hover:border-primary/40",
      )}
    >
      <div className="p-4">
        <span
          className={cn(
            "inline-flex size-4 items-center justify-center rounded-full border-2",
            isSelected ? "border-primary" : "border-muted-foreground/40",
          )}
        >
          {isSelected ? (
            <span className="size-2 rounded-full bg-primary" />
          ) : null}
        </span>
        <div className="my-4 flex items-center justify-center gap-1 text-muted-foreground">
          {icons}
        </div>
        <div className="text-center text-[13px] font-semibold">{title}</div>
        <p className="mt-1 text-center text-xs text-muted-foreground">
          {description}
        </p>
      </div>
      <div className="flex items-start gap-2 border-t p-3">
        <Globe className="mt-0.5 size-3.5 shrink-0 text-success" />
        <span className="text-[11px] text-muted-foreground">{note}</span>
      </div>
    </button>
  );
}

/** 에이전트 선택 또는 신규 등록 (sources.agents.register). */
function AgentSection({
  selectedAgentId,
  onSelectedAgentIdChange,
}: {
  selectedAgentId: string | null;
  onSelectedAgentIdChange: (agentId: string | null) => void;
}) {
  const client = useFoundryLiteClient();
  const agentsQuery = useSourceAgents();
  const [agentIdInput, setAgentIdInput] = useState("");
  const [agentNameInput, setAgentNameInput] = useState("");
  const [isRegistering, setIsRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<unknown>(null);

  const agents = agentsQuery.data ?? [];
  const agentIdError =
    agentIdInput && !isValidIdentifier(agentIdInput)
      ? "영문/숫자/밑줄만 사용할 수 있습니다 (숫자로 시작 불가)."
      : null;
  const canRegister =
    agentIdInput.trim().length > 0 &&
    !agentIdError &&
    agentNameInput.trim().length > 0 &&
    !isRegistering;

  const handleRegisterAgent = async () => {
    setIsRegistering(true);
    setRegisterError(null);
    try {
      const agent = await client.sources.agents.register(
        {
          agentId: agentIdInput.trim(),
          displayName: agentNameInput.trim(),
          mode: "agent_proxy",
        },
        { idempotencyKey: idempotencyKey("source_agent", agentIdInput.trim()) },
      );
      onSelectedAgentIdChange(agent.agentId);
      await agentsQuery.reload();
    } catch (error) {
      setRegisterError(error);
    } finally {
      setIsRegistering(false);
    }
  };

  return (
    <section className="space-y-3 rounded border bg-card p-4">
      <div className="flex items-center gap-2 text-xs font-semibold">
        <ShieldCheck className="size-3.5 text-success" /> 에이전트 연결
      </div>
      <p className="text-[11px] text-muted-foreground">
        관리형 run 데이터 플레인은 직접 연결로 실행됩니다. 선택한 에이전트는
        네트워크 정책에 연결되어 등록·heartbeat 증거로 사용됩니다.
      </p>
      {agentsQuery.error ? (
        <ErrorState
          error={agentsQuery.error}
          onRetry={() => void agentsQuery.reload()}
        />
      ) : agents.length > 0 ? (
        <WizardField
          label="사용할 에이전트"
          helper="등록된 에이전트 중에서 선택합니다."
        >
          <Select
            value={selectedAgentId ?? ""}
            onValueChange={(value) => onSelectedAgentIdChange(value || null)}
          >
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue placeholder="에이전트 선택" />
            </SelectTrigger>
            <SelectContent>
              {agents.map((agent) => (
                <SelectItem key={agent.agentId} value={agent.agentId}>
                  <span className="font-mono">{agent.agentId}</span>
                  <StatusPill intent={statusIntent(agent.status)}>
                    {statusLabel(agent.status)}
                  </StatusPill>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </WizardField>
      ) : (
        <div className="rounded border border-dashed p-3 text-[11px] text-muted-foreground">
          등록된 에이전트가 없습니다. 아래에서 에이전트를 등록하거나 직접 연결을
          선택하세요.
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
        <WizardField label="에이전트 ID" error={agentIdError}>
          <Input
            value={agentIdInput}
            onChange={(event) =>
              setAgentIdInput(
                sanitizeIdentifier(event.target.value) || event.target.value,
              )
            }
            placeholder="onprem_agent_01"
            className="h-8 font-mono text-xs"
          />
        </WizardField>
        <WizardField label="에이전트 표시 이름">
          <Input
            value={agentNameInput}
            onChange={(event) => setAgentNameInput(event.target.value)}
            placeholder="온프레미스 에이전트"
            className="h-8 text-xs"
          />
        </WizardField>
        <div className="flex items-end">
          <Button
            variant="outline"
            size="sm"
            disabled={!canRegister}
            onClick={() => void handleRegisterAgent()}
          >
            에이전트 등록
          </Button>
        </div>
      </div>
      {registerError ? <ErrorState error={registerError} /> : null}
      {selectedAgentId ? (
        <div className="font-mono text-[11px] text-muted-foreground">
          선택된 에이전트: {selectedAgentId}
        </div>
      ) : null}
    </section>
  );
}
