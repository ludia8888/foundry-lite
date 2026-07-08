import type { ActionValidationResponse } from "@foundry-lite/sdk";

import { StatusPill } from "@/components/shared/StatusPill";

interface ValidationResultPanelProps {
  result: ActionValidationResponse;
  requestId?: string | null;
}

/** 사전 검증 결과: target/parameter/submissionCriteria issue를 apply와 분리해 표시. */
export function ValidationResultPanel({
  result,
  requestId,
}: ValidationResultPanelProps) {
  const isValid = result.result === "VALID";
  const issues = [
    ...result.target.issues,
    ...result.submissionCriteria,
    ...Object.values(result.parameters).flatMap(
      (parameter) => parameter.issues,
    ),
  ];
  return (
    <div className="space-y-1 rounded border bg-muted/40 p-2">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill intent={isValid ? "success" : "danger"}>
          검증 {isValid ? "통과" : "실패"}
        </StatusPill>
        <span className="font-mono text-[10px] text-muted-foreground">
          expected=v{result.target.expectedObjectVersion} / current=
          {result.target.currentObjectVersion === null
            ? "?"
            : `v${result.target.currentObjectVersion}`}
        </span>
        {requestId ? (
          <span className="font-mono text-[10px] text-muted-foreground">
            request_id={requestId}
          </span>
        ) : null}
      </div>
      {issues.map((issue, index) => (
        <div
          key={`${issue.code}-${index}`}
          className="text-[11px] text-destructive"
        >
          <span className="font-mono">[{issue.code}]</span> {issue.message}
        </div>
      ))}
      {isValid && issues.length === 0 ? (
        <div className="text-[11px] text-muted-foreground">
          대상 상태·파라미터·제출 기준을 모두 통과했습니다.
        </div>
      ) : null}
    </div>
  );
}
