import { ShieldAlert } from "lucide-react";

interface PermissionDeniedCardProps {
  operation: string;
  requestId: string | null;
  message?: string | null;
}

/**
 * 직무 분리(separation of duties) PERMISSION_DENIED를 에러가 아닌 안내 카드로 표시한다.
 * 제출자는 자신의 제안을 결정/실행할 수 없다.
 */
export function PermissionDeniedCard({
  operation,
  requestId,
  message,
}: PermissionDeniedCardProps) {
  return (
    <div className="flex items-start gap-2.5 rounded border border-warning/40 bg-warning/5 p-2.5">
      <ShieldAlert className="mt-0.5 size-4 shrink-0 text-warning" />
      <div className="min-w-0 flex-1 space-y-1">
        <div className="text-[12px] font-semibold text-warning">
          직무 분리 정책으로 {operation} 권한이 없습니다
        </div>
        <div className="text-[11px] text-muted-foreground">
          {message ??
            "제안 제출자와 리뷰어는 서로 달라야 합니다. 다른 리뷰어를 지정하거나 관리자에게 문의하세요."}
        </div>
        <div className="font-mono text-[11px] text-muted-foreground">
          code=PERMISSION_DENIED
          {requestId ? ` · request_id=${requestId}` : ""}
        </div>
      </div>
    </div>
  );
}
