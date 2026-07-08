import {
  classifyFoundryLiteError,
  normalizeFoundryLiteError,
} from "@foundry-lite/sdk";
import { AlertCircle, Lock, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const KIND_TITLES: Record<string, string> = {
  permission_denied: "권한이 없습니다",
  not_found: "리소스를 찾을 수 없습니다",
  validation: "요청 검증에 실패했습니다",
  stale_object_version: "객체 버전이 변경되었습니다",
  conflict: "요청이 충돌했습니다",
  retryable: "일시적인 오류가 발생했습니다",
  unknown: "요청이 실패했습니다",
};

interface ErrorStateProps {
  error: unknown;
  onRetry?: () => void;
  className?: string;
}

/**
 * FoundryLiteApiError 표준 에러 표시.
 * request id / error code / retryability를 숨기지 않는다 (스펙 구현 원칙).
 */
export function ErrorState({ error, onRetry, className }: ErrorStateProps) {
  const normalized = normalizeFoundryLiteError(error);
  const classification = classifyFoundryLiteError(normalized);
  const isPermissionDenied = classification.kind === "permission_denied";
  const Icon = isPermissionDenied ? Lock : AlertCircle;

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded border border-destructive/30 bg-destructive/5 p-3",
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="text-[13px] font-semibold text-destructive">
          {KIND_TITLES[classification.kind] ?? KIND_TITLES.unknown}
        </div>
        <div className="text-xs break-words text-foreground/80">
          {normalized.message}
        </div>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-muted-foreground">
          <span>code={normalized.code}</span>
          {normalized.requestId ? (
            <span>request_id={normalized.requestId}</span>
          ) : null}
          <span>{normalized.retryable ? "재시도 가능" : "재시도 불가"}</span>
        </div>
        {isPermissionDenied ? (
          <div className="text-xs text-muted-foreground">
            프로젝트 grant 권한이 필요합니다. 관리자에게 문의하세요.
          </div>
        ) : null}
      </div>
      {onRetry && normalized.retryable ? (
        <Button size="sm" variant="outline" onClick={onRetry}>
          <RotateCw />
          재시도
        </Button>
      ) : null}
    </div>
  );
}
