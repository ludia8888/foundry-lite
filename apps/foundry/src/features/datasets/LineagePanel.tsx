import type { LineageEdge } from "@foundry-lite/sdk";
import { GitBranch } from "lucide-react";
import { Link } from "react-router";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { operationsRunHref } from "@/lib/operations-links";

import { formatTimestamp } from "./dataset-schema";

interface LineagePanelProps {
  lineage: readonly LineageEdge[];
  datasetRef: string;
}

const LINEAGE_COLUMNS: readonly DataTableColumn<LineageEdge>[] = [
  {
    key: "from",
    header: "From",
    isMono: true,
    className: "max-w-72 truncate",
    render: (edge) => (
      <span title={edge.from_resource_id}>
        {edge.from_resource_type}:{edge.from_resource_id}
      </span>
    ),
  },
  {
    key: "relation",
    header: "관계",
    render: (edge) => <StatusPill intent="info">{edge.relation}</StatusPill>,
  },
  {
    key: "to",
    header: "To",
    isMono: true,
    className: "max-w-72 truncate",
    render: (edge) => (
      <span title={edge.to_resource_id}>
        {edge.to_resource_type}:{edge.to_resource_id}
      </span>
    ),
  },
  {
    key: "runId",
    header: "생성 Run ID",
    isMono: true,
    className: "max-w-56 truncate",
    render: (edge) => (
      <Link
        to={operationsRunHref(edge.created_by_run_id)}
        title={edge.created_by_run_id}
        className="block truncate text-primary hover:underline"
      >
        {edge.created_by_run_id}
      </Link>
    ),
  },
  {
    key: "createdAt",
    header: "생성 시각",
    isMono: true,
    render: (edge) => formatTimestamp(edge.created_at),
  },
];

/** 리니지 evidence 테이블 + Data Lineage 화면 이동 링크. */
export function LineagePanel({ lineage, datasetRef }: LineagePanelProps) {
  const lineageHref = `/lineage?ref=${encodeURIComponent(datasetRef)}`;

  if (lineage.length === 0) {
    return (
      <EmptyState
        icon={GitBranch}
        title="리니지 edge가 없습니다"
        description={`${datasetRef} 리소스로 기록된 리니지가 아직 없습니다. Pipeline 실행이 커밋되면 edge가 생성됩니다.`}
        action={
          <Button size="sm" variant="outline" asChild>
            <Link to={lineageHref}>
              <GitBranch />
              Data Lineage 열기
            </Link>
          </Button>
        }
      />
    );
  }

  return (
    <div className="space-y-2">
      <DataTable
        columns={LINEAGE_COLUMNS}
        rows={lineage}
        rowKey={(edge) => edge.id}
      />
      <div className="flex items-center justify-between">
        <span className="font-mono text-[11px] text-muted-foreground">
          resource={datasetRef} · {lineage.length}개 edge
        </span>
        <Button size="sm" variant="outline" asChild>
          <Link to={lineageHref}>
            <GitBranch />
            전체 그래프 보기
          </Link>
        </Button>
      </div>
    </div>
  );
}
