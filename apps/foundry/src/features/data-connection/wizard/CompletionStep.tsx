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
  onGoToSyncDetail: () => void;
  onGoToSourceExplore: () => void;
}

/**
 * 완료 단계: 실제 생성 evidence 요약 + 동기화 상세/소스 상세 이동 액션
 * (Palantir set-up-source "저장 및 계속하기" 이후 구조).
 */
export function CompletionStep({
  completion,
  templateDisplayName,
  connectionMode,
  agentId,
  projectName,
  onGoToSyncDetail,
  onGoToSourceExplore,
}: CompletionStepProps) {
  const hasSync = completion.syncName !== null;
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <h2 className="text-base font-semibold">
          {hasSync ? "소스 및 동기화 준비 완료" : "소스 설정 완료"}
        </h2>
        <StatusPill intent="success">
          {hasSync ? "실행 준비 완료" : "생성 완료"}
        </StatusPill>
      </div>
      <p className="text-xs text-muted-foreground">
        {hasSync
          ? "Source 연결, 관리형 동기화와 첫 실행 증거가 모두 준비되었습니다. 동기화 상세에서 실행 이력을 확인하거나 Source 상세에서 연결을 탐색할 수 있습니다."
          : "Source가 생성되었습니다. Source 상세에서 연결을 확인하고 동기화를 추가할 수 있습니다."}
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
          <Button size="sm" onClick={onGoToSyncDetail}>
            <RefreshCw className="size-3.5" /> 동기화 상세 보기
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
