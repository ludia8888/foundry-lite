import { useFoundryLiteSession } from "@foundry-lite/sdk/react";

import { StatusPill } from "@/components/shared/StatusPill";

/**
 * 마지막 SDK 응답의 request id / error code / retryability를 상시 노출한다.
 * (스펙 구현 원칙: 증거를 숨기지 않는다)
 */
export function RequestTelemetry() {
  const session = useFoundryLiteSession();

  if (!session.lastRequestId) {
    return null;
  }

  const hasError = session.lastErrorCode !== null;

  return (
    <div className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
      <span className="hidden max-w-52 truncate xl:inline">
        req={session.lastRequestId}
      </span>
      {hasError ? (
        <StatusPill intent="danger">{session.lastErrorCode}</StatusPill>
      ) : (
        <StatusPill intent="success">ok</StatusPill>
      )}
      {hasError && session.isLastResponseRetryable ? (
        <StatusPill intent="warning">재시도 가능</StatusPill>
      ) : null}
    </div>
  );
}
