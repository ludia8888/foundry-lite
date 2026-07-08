import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { ScrollText } from "lucide-react";
import { useMemo, useState } from "react";

import { DataTable } from "@/components/shared/DataTable";
import type { DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import { readRowString, useSecurityQuery } from "./use-security-query";

const AUDIT_LIMIT = 100;

interface AuditRowView {
  id: string;
  eventType: string;
  action: string;
  decision: string;
  actor: string;
  resource: string;
  requestId: string | null;
  correlationId: string | null;
  createdAt: string | null;
}

/** 감사 이벤트 원시 row를 화면 모델로 정규화한다. */
function toAuditRow(row: Record<string, unknown>): AuditRowView {
  const resourceType = readRowString(row, "resource_type") ?? "";
  const resourceId = readRowString(row, "resource_id") ?? "";
  return {
    id: readRowString(row, "id") ?? crypto.randomUUID(),
    eventType: readRowString(row, "event_type") ?? "—",
    action: readRowString(row, "action") ?? "—",
    decision: readRowString(row, "decision") ?? "—",
    actor: readRowString(row, "actor_user_id") ?? "—",
    resource:
      resourceType && resourceId
        ? `${resourceType}:${resourceId}`
        : resourceType || resourceId || "—",
    requestId: readRowString(row, "request_id"),
    correlationId: readRowString(row, "correlation_id"),
    createdAt: readRowString(row, "created_at"),
  };
}

function decisionIntent(decision: string): "success" | "danger" | "neutral" {
  if (decision === "allow") return "success";
  if (decision === "deny") return "danger";
  return "neutral";
}

/** 이벤트 타입에서 mutation 성격(생성/수정/삭제/승인 등)만 걸러내는 카테고리. */
const CATEGORY_MATCHERS: Record<string, (eventType: string) => boolean> = {
  all: () => true,
  mutation: (t) =>
    /\.(created|updated|upserted|deleted|promoted|approved|assigned|aborted|failed|deactivated|revoked)$/.test(
      t,
    ),
  oauth: (t) => t.startsWith("osdk.oauth") || t.includes("oauth"),
  osdk: (t) => t.startsWith("osdk."),
  review: (t) => t.startsWith("insight_review") || t.includes("review"),
};

const CATEGORY_OPTIONS = [
  { value: "mutation", label: "변경 이벤트" },
  { value: "oauth", label: "OAuth 세션" },
  { value: "osdk", label: "OSDK 앱" },
  { value: "review", label: "검토/승인" },
  { value: "all", label: "전체" },
] as const;

type Category = (typeof CATEGORY_OPTIONS)[number]["value"];

function formatTimestamp(value: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

/**
 * 감사 관점 탭.
 *
 * operations.runs.list({ runType: "audit" })가 반환하는 auditEvents 중 mutation
 * 성격(생성/수정/삭제/승인/OAuth 등) 이벤트를 필터링해 actor/action/decision/
 * request id evidence와 함께 표시한다. 실존 surface(operations audit)만 사용한다.
 */
export function AuditPanel() {
  const client = useFoundryLiteClient();
  const [category, setCategory] = useState<Category>("mutation");

  const runsQuery = useSecurityQuery(["security", "audit"], () =>
    client.operations.runs.list({ runType: "audit", limit: AUDIT_LIMIT }),
  );

  const allRows = useMemo(
    () => (runsQuery.data?.auditEvents ?? []).map(toAuditRow),
    [runsQuery.data],
  );
  const rows = useMemo(() => {
    const matcher = CATEGORY_MATCHERS[category] ?? CATEGORY_MATCHERS.all;
    return allRows.filter((row) => matcher(row.eventType));
  }, [allRows, category]);

  const columns: readonly DataTableColumn<AuditRowView>[] = [
    {
      key: "time",
      header: "시각",
      className: "w-32",
      render: (row) => (
        <span className="text-[11px] text-muted-foreground">
          {formatTimestamp(row.createdAt)}
        </span>
      ),
    },
    {
      key: "event",
      header: "이벤트",
      render: (row) => (
        <span className="font-mono text-[11px]">{row.eventType}</span>
      ),
    },
    {
      key: "actor",
      header: "Actor",
      render: (row) => (
        <span className="font-mono text-[11px]">{row.actor}</span>
      ),
    },
    {
      key: "resource",
      header: "리소스",
      render: (row) => (
        <span className="block max-w-[220px] truncate font-mono text-[11px] text-muted-foreground">
          {row.resource}
        </span>
      ),
    },
    {
      key: "decision",
      header: "결정",
      className: "w-20",
      render: (row) => (
        <StatusPill intent={decisionIntent(row.decision)}>
          {row.decision}
        </StatusPill>
      ),
    },
    {
      key: "request",
      header: "request id",
      render: (row) => (
        <span className="block max-w-[200px] truncate font-mono text-[11px] text-muted-foreground">
          {row.requestId ?? row.correlationId ?? "—"}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-3">
      <section className="flex flex-wrap items-center justify-between gap-3">
        <div className="space-y-0.5">
          <div className="flex items-center gap-1.5">
            <ScrollText className="size-4 text-primary" />
            <span className="text-[13px] font-semibold">감사 이벤트</span>
          </div>
          <p className="text-[11px] text-muted-foreground">
            Platform Operations의 audit run에서 변경/권한 이벤트를 조회합니다.
            각 이벤트는 actor, decision, request id를 증거로 남깁니다.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Select
            value={category}
            onValueChange={(value) => setCategory(value as Category)}
          >
            <SelectTrigger size="sm" className="h-7 w-36 text-[12px]">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {CATEGORY_OPTIONS.map((option) => (
                <SelectItem key={option.value} value={option.value}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-[11px] text-muted-foreground">
            {rows.length}건
          </span>
        </div>
      </section>

      {runsQuery.isLoading ? (
        <LoadingState rowCount={6} />
      ) : runsQuery.error ? (
        <ErrorState
          error={runsQuery.error}
          onRetry={() => void runsQuery.reload()}
        />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={ScrollText}
          title="해당 카테고리의 감사 이벤트가 없습니다"
          description="다른 카테고리를 선택하거나 플랫폼에서 작업을 수행한 후 다시 조회하세요."
        />
      ) : (
        <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />
      )}

      <section className="flex items-start gap-2 rounded border border-dashed p-2.5">
        <ScrollText className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
        <p className="text-[11px] text-muted-foreground">
          <span className="font-medium text-foreground">
            정책 엔진 / 전체 감사 로그
          </span>
          는 partial입니다. 현재는 operations audit run에 기록된 이벤트만 조회할
          수 있으며, 정책 규칙(row-level policy) 관리와 장기 보존 감사 검색은
          future_gap입니다.
        </p>
      </section>
    </div>
  );
}
