import type {
  DatasetQualityContractCheck,
  DatasetQualityResultHistoryItem,
  DatasetQualityResultSummary,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { ShieldCheck } from "lucide-react";
import { useCallback } from "react";
import { Link } from "react-router";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill, type StatusIntent } from "@/components/shared/StatusPill";
import { operationsRunHref } from "@/lib/operations-links";

import { formatTimestamp } from "./dataset-schema";
import { useScreenQuery } from "./use-screen-query";

interface QualityPanelProps {
  namespace: string;
  name: string;
  qualitySummary: DatasetQualityResultSummary | null;
}

function resultStatusIntent(status: string): StatusIntent {
  if (status === "PASS") return "success";
  if (status === "WARN") return "warning";
  if (status === "FAIL") return "danger";
  return "neutral";
}

function severityIntent(severity: string): StatusIntent {
  if (severity === "error") return "danger";
  if (severity === "warning") return "warning";
  return "neutral";
}

const CHECK_COLUMNS: readonly DataTableColumn<DatasetQualityContractCheck>[] = [
  {
    key: "checkType",
    header: "체크 유형",
    isMono: true,
    render: (check) => check.checkType,
  },
  {
    key: "config",
    header: "설정",
    isMono: true,
    className: "max-w-72 truncate",
    render: (check) => (
      <span title={JSON.stringify(check.config)}>
        {JSON.stringify(check.config)}
      </span>
    ),
  },
  {
    key: "severity",
    header: "심각도",
    render: (check) => (
      <StatusPill intent={severityIntent(check.severity)}>
        {check.severity}
      </StatusPill>
    ),
  },
  {
    key: "enabled",
    header: "활성",
    render: (check) => (
      <StatusPill intent={check.enabled ? "success" : "neutral"}>
        {check.enabled ? "on" : "off"}
      </StatusPill>
    ),
  },
  {
    key: "id",
    header: "체크 ID",
    isMono: true,
    className: "max-w-56 truncate",
    render: (check) => <span title={check.id}>{check.id}</span>,
  },
];

const RESULT_COLUMNS: readonly DataTableColumn<DatasetQualityResultHistoryItem>[] =
  [
    {
      key: "checkType",
      header: "체크 유형",
      isMono: true,
      render: (result) => result.checkType,
    },
    {
      key: "status",
      header: "결과",
      render: (result) => (
        <StatusPill intent={resultStatusIntent(result.status)}>
          {result.status}
        </StatusPill>
      ),
    },
    {
      key: "severity",
      header: "심각도",
      render: (result) => (
        <StatusPill intent={severityIntent(result.severity)}>
          {result.severity}
        </StatusPill>
      ),
    },
    {
      key: "runId",
      header: "Run ID",
      isMono: true,
      className: "max-w-56 truncate",
      render: (result) => (
        <Link
          to={operationsRunHref(result.runId)}
          title={result.runId}
          className="block truncate text-primary hover:underline"
        >
          {result.runId}
        </Link>
      ),
    },
    {
      key: "transactionId",
      header: "트랜잭션",
      isMono: true,
      className: "max-w-56 truncate",
      render: (result) => (
        <span title={result.transactionId}>{result.transactionId}</span>
      ),
    },
    {
      key: "schemaVersion",
      header: "검증 스키마",
      isMono: true,
      render: (result) => `schema_v${result.validatedAgainstSchemaVersion}`,
    },
    {
      key: "createdAt",
      header: "실행 시각",
      isMono: true,
      render: (result) => formatTimestamp(result.createdAt),
    },
  ];

/** 품질 패널: quality check 목록 + 결과 요약 + 최근 결과 evidence (run id/transaction/manifest 노출). */
export function QualityPanel({
  namespace,
  name,
  qualitySummary,
}: QualityPanelProps) {
  const client = useFoundryLiteClient();
  const loadChecks = useCallback(
    () => client.datasets.qualityChecks.list(namespace, name),
    [client, name, namespace],
  );
  const checksQuery = useScreenQuery(
    ["datasets", "quality-checks", namespace, name],
    loadChecks,
  );

  const checks = checksQuery.data?.checks ?? [];
  const hasNoQualityData =
    !checksQuery.isLoading &&
    checks.length === 0 &&
    (qualitySummary?.totalResults ?? 0) === 0;

  if (hasNoQualityData) {
    return (
      <EmptyState
        icon={ShieldCheck}
        title="품질 체크가 없습니다"
        description="quality contract에 체크를 등록하면 트랜잭션 커밋 시점마다 검증 결과가 기록됩니다."
      />
    );
  }

  return (
    <div className="space-y-3">
      {qualitySummary ? <QualitySummaryStrip summary={qualitySummary} /> : null}

      <section className="space-y-1.5">
        <div className="section-label">품질 체크 ({checks.length})</div>
        {checksQuery.isLoading ? (
          <LoadingState rowCount={3} />
        ) : checksQuery.error ? (
          <ErrorState error={checksQuery.error} onRetry={checksQuery.reload} />
        ) : (
          <DataTable
            columns={CHECK_COLUMNS}
            rows={checks}
            rowKey={(check) => check.id}
            emptyMessage="등록된 체크가 없습니다."
          />
        )}
      </section>

      <section className="space-y-1.5">
        <div className="section-label">
          최근 검증 결과 ({qualitySummary?.latestResults.length ?? 0})
        </div>
        <DataTable
          columns={RESULT_COLUMNS}
          rows={qualitySummary?.latestResults ?? []}
          rowKey={(result) => result.id}
          emptyMessage="아직 검증 결과가 없습니다."
        />
      </section>
    </div>
  );
}

function QualitySummaryStrip({
  summary,
}: {
  summary: DatasetQualityResultSummary;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 rounded border bg-card p-3">
      <span className="section-label">결과 요약</span>
      <span className="font-mono text-[11px] text-muted-foreground">
        총 {summary.totalResults}건
      </span>
      {summary.statusCounts.map((statusCount) => (
        <StatusPill
          key={statusCount.status}
          intent={resultStatusIntent(statusCount.status)}
        >
          {statusCount.status} {statusCount.count}
        </StatusPill>
      ))}
      <span className="ml-auto font-mono text-[11px] text-muted-foreground">
        {summary.checkTypeStatusCounts
          .map(
            (typeCount) =>
              `${typeCount.checkType}:${typeCount.status}=${typeCount.count}`,
          )
          .join(" · ")}
      </span>
    </div>
  );
}
