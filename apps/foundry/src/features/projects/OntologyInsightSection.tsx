import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";

import { useProjectsQuery } from "./use-projects-query";

interface OntologyInsightSectionProps {
  resourceType: "object_type" | "link_type";
  apiName: string;
}

const USAGE_WINDOW_DAYS = 30;

function countRecords(
  record: Record<string, unknown> | null | undefined,
): string {
  if (!record) return "—";
  const total = record["total"] ?? record["count"];
  if (typeof total === "number") return String(total);
  return String(Object.keys(record).length);
}

/**
 * 온톨로지 리소스 영향 evidence: usage(최근 30일)와 dependents를 함께 표시.
 * SDK ontology.resources.usage / ontology.resources.dependents 사용.
 */
export function OntologyInsightSection({
  resourceType,
  apiName,
}: OntologyInsightSectionProps) {
  const client = useFoundryLiteClient();

  const loadUsage = useCallback(
    () =>
      client.ontology.resources.usage(resourceType, apiName, {
        windowDays: USAGE_WINDOW_DAYS,
      }),
    [client, resourceType, apiName],
  );
  const loadDependents = useCallback(
    () => client.ontology.resources.dependents(resourceType, apiName),
    [client, resourceType, apiName],
  );

  const usageQuery = useProjectsQuery(
    ["projects", "ontology-usage", resourceType, apiName],
    loadUsage,
  );
  const dependentsQuery = useProjectsQuery(
    ["projects", "ontology-dependents", resourceType, apiName],
    loadDependents,
  );

  if (usageQuery.isLoading || dependentsQuery.isLoading) {
    return <LoadingState rowCount={4} />;
  }
  const error = usageQuery.error ?? dependentsQuery.error;
  if (error) {
    return (
      <ErrorState
        error={error}
        onRetry={() =>
          void Promise.all([usageQuery.reload(), dependentsQuery.reload()])
        }
      />
    );
  }

  const usage = usageQuery.data;
  const dependents = dependentsQuery.data;
  const notes = [...(usage?.notes ?? []), ...(dependents?.notes ?? [])];

  return (
    <div className="space-y-3">
      <section className="space-y-1.5">
        <div className="section-label">사용량 (최근 {USAGE_WINDOW_DAYS}일)</div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12px]">
          <dt className="text-muted-foreground">액션 실행</dt>
          <dd className="text-right font-mono text-[11px]">
            {countRecords(usage?.actionRuns)}
          </dd>
          <dt className="text-muted-foreground">인덱스 실행</dt>
          <dd className="text-right font-mono text-[11px]">
            {countRecords(usage?.indexRuns)}
          </dd>
          <dt className="text-muted-foreground">감사 이벤트</dt>
          <dd className="text-right font-mono text-[11px]">
            {countRecords(usage?.auditEvents)}
          </dd>
        </dl>
      </section>

      <section className="space-y-1.5">
        <div className="section-label">의존 리소스</div>
        <dl className="space-y-1 text-[12px]">
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">백킹 데이터셋</dt>
            <dd className="truncate font-mono text-[11px]">
              {dependents?.backingDatasetRef ?? "—"}
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">링크 타입</dt>
            <dd className="font-mono text-[11px]">
              {dependents?.linkTypes.length ?? 0}개
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">액션 타입</dt>
            <dd className="font-mono text-[11px]">
              {dependents?.actionTypes.length ?? 0}개
            </dd>
          </div>
          <div className="flex justify-between gap-2">
            <dt className="text-muted-foreground">OSDK 앱</dt>
            <dd className="font-mono text-[11px]">
              {dependents?.osdkApplications.length ?? 0}개
            </dd>
          </div>
        </dl>
      </section>

      {notes.length > 0 ? (
        <section className="space-y-1">
          <div className="section-label">NOTES</div>
          {notes.map((note) => (
            <p key={note} className="text-[11px] text-muted-foreground">
              {note}
            </p>
          ))}
        </section>
      ) : null}
    </div>
  );
}
