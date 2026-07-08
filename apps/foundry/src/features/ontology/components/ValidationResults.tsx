import type {
  FoundryLiteApiError,
  OntologyValidationResult,
} from "@foundry-lite/sdk";
import type { FoundryLiteOntologyDraftValidationView } from "@foundry-lite/sdk/react";
import { AlertTriangle, CircleCheck, CircleX, RefreshCcw } from "lucide-react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Skeleton } from "@/components/ui/skeleton";

interface ValidationResultsProps {
  view: FoundryLiteOntologyDraftValidationView;
  result: OntologyValidationResult | null;
  error: FoundryLiteApiError | null;
  isValidating: boolean;
  requestId: string | null;
}

/** 검증 실패 details(YAML 파스 오류 위치 line/column 등)를 mono로 노출한다. */
function ValidationErrorDetails({ error }: { error: FoundryLiteApiError }) {
  const entries = Object.entries(error.details ?? {}).filter(
    ([, value]) => value !== null && value !== undefined,
  );
  if (entries.length === 0) return null;
  return (
    <div
      className="space-y-0.5 rounded border border-destructive/30 bg-destructive/5 p-2"
      data-testid="ontology-validation-error-details"
    >
      <div className="text-[11px] font-semibold text-destructive">
        오류 위치 · 상세
      </div>
      {entries.map(([key, value]) => (
        <div key={key} className="font-mono text-[11px] break-words">
          {key}=
          {typeof value === "string" || typeof value === "number"
            ? String(value)
            : JSON.stringify(value)}
        </div>
      ))}
    </div>
  );
}

/** 검증 패널: apply 전 validation failure를 숨기지 않고 그대로 노출한다. */
export function ValidationResults({
  view,
  result,
  error,
  isValidating,
  requestId,
}: ValidationResultsProps) {
  if (isValidating) {
    return (
      <div className="space-y-2">
        <div className="section-label">검증 결과</div>
        <Skeleton className="h-20 w-full" />
      </div>
    );
  }
  if (error) {
    return (
      <div className="space-y-2">
        <div className="section-label">검증 결과</div>
        <ErrorState error={error} />
        <ValidationErrorDetails error={error} />
      </div>
    );
  }
  if (!result) {
    return (
      <div className="space-y-2">
        <div className="section-label">검증 결과</div>
        <p className="text-[11px] text-muted-foreground">
          아직 검증하지 않았습니다. [검증] 버튼으로 드래프트를 활성 온톨로지와
          비교하세요. 검증을 통과해야 적용할 수 있습니다.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="section-label">검증 결과</div>
        {requestId ? (
          <span className="font-mono text-[10px] text-muted-foreground">
            req={requestId}
          </span>
        ) : null}
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        {view.isBlocked ? (
          <StatusPill intent="danger">
            <CircleX className="size-3" />
            차단됨 — 적용 불가
          </StatusPill>
        ) : (
          <StatusPill intent="success">
            <CircleCheck className="size-3" />
            검증 통과
          </StatusPill>
        )}
        {view.counts ? (
          <span className="font-mono text-[11px] text-muted-foreground">
            객체 {view.counts.objectTypes} · 링크 {view.counts.linkTypes} · 액션{" "}
            {view.counts.actionTypes}
          </span>
        ) : null}
        {view.requiresReindex ? (
          <StatusPill intent="info">
            <RefreshCcw className="size-3" />
            리인덱스 {view.reindexOperations.length}건
          </StatusPill>
        ) : null}
        {view.sdkImpact.requiresSdkRegeneration ? (
          <StatusPill intent="warning">SDK 재생성 필요</StatusPill>
        ) : null}
      </div>

      {view.blockedChanges.length > 0 ? (
        <div className="space-y-1 rounded border border-destructive/30 bg-destructive/5 p-2">
          <div className="text-[11px] font-semibold text-destructive">
            차단된 변경 {view.blockedChanges.length}건 — 해결 전에는 적용할 수
            없습니다
          </div>
          {view.blockedChanges.map((change) => (
            <div
              key={`${change.path}-${change.reason}`}
              className="text-[11px]"
            >
              <span className="font-mono">{change.path}</span>
              <span className="text-muted-foreground"> — {change.reason}</span>
            </div>
          ))}
        </div>
      ) : null}

      {view.warnings.length > 0 ? (
        <div className="space-y-1 rounded border border-warning/40 bg-warning/10 p-2">
          <div className="flex items-center gap-1 text-[11px] font-semibold text-warning">
            <AlertTriangle className="size-3" />
            경고 {view.warnings.length}건
          </div>
          {view.warnings.map((change) => (
            <div
              key={`${change.path}-${change.reason}`}
              className="text-[11px]"
            >
              <span className="font-mono">{change.path}</span>
              <span className="text-muted-foreground"> — {change.reason}</span>
            </div>
          ))}
        </div>
      ) : null}

      {view.reindexOperations.length > 0 ? (
        <div className="space-y-1 rounded border p-2">
          <div className="section-label">리인덱스 계획</div>
          {view.reindexOperations.map((operation) => (
            <div key={operation.reindexKey} className="text-[11px]">
              <span className="font-mono">{operation.objectTypeApiName}</span>
              <span className="text-muted-foreground">
                {" "}
                — {operation.reason}
                {operation.changedFields.length > 0
                  ? ` (${operation.changedFields.join(", ")})`
                  : null}
              </span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
