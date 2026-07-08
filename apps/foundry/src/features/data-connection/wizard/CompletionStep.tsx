import { Compass, RefreshCw } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";

import type { WizardCompletion } from "./CsvUploadFlow";
import type { ConnectionMode } from "./ConnectionMethodStep";
import { EvidenceList, EvidenceRow } from "./WizardEvidence";

interface CompletionStepProps {
  completion: WizardCompletion;
  templateDisplayName: string;
  connectionMode: ConnectionMode;
  agentId: string | null;
  projectName: string | null;
  onGoToSyncSetup: () => void;
  onGoToSourceExplore: () => void;
}

/**
 * 완료 단계: 생성 evidence 요약 + 동기화 설정/소스 탐색 이동 액션
 * (Palantir set-up-source "저장 및 계속하기" 이후 구조).
 */
export function CompletionStep({
  completion,
  templateDisplayName,
  connectionMode,
  agentId,
  projectName,
  onGoToSyncSetup,
  onGoToSourceExplore,
}: CompletionStepProps) {
  const hasSync = completion.syncName !== null;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">소스 설정 완료</h2>
        <StatusPill intent="success">생성 완료</StatusPill>
      </div>
      <p className="text-xs text-muted-foreground">
        소스가 완전히 설정되었습니다. 이제 Foundry로 데이터를 가져오기 위한
        동기화(Sync)를 설정하거나 소스를 탐색할 수 있습니다.
      </p>
      <EvidenceList title="생성 증거">
        <EvidenceRow label="소스" value={completion.sourceName} />
        <EvidenceRow label="동기화" value={completion.syncName} />
        <EvidenceRow label="소스 유형" value={templateDisplayName} />
        <EvidenceRow
          label="연결 방식"
          value={
            connectionMode === "agent_proxy"
              ? `에이전트를 통해 (${agentId ?? "—"})`
              : "직접 연결"
          }
        />
        <EvidenceRow label="프로젝트" value={projectName} />
      </EvidenceList>
      <div className="flex flex-wrap items-center gap-2">
        {hasSync ? (
          <Button size="sm" onClick={onGoToSyncSetup}>
            <RefreshCw className="size-3.5" /> 동기화 설정으로 이동
          </Button>
        ) : null}
        <Button
          size="sm"
          variant={hasSync ? "outline" : "default"}
          onClick={onGoToSourceExplore}
        >
          <Compass className="size-3.5" /> 소스 상세로 이동
        </Button>
      </div>
    </div>
  );
}
