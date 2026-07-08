import type { PipelineNode } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { MousePointerClick } from "lucide-react";
import { useCallback, useMemo } from "react";

import { DataTable, type DataTableColumn } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";

import { nodeDatasetRef, nodeLabel, splitDatasetRef } from "../pipeline-model";
import { useSafeQuery } from "../use-safe-query";

const PREVIEW_LIMIT = 50;

interface PreviewPanelProps {
  node: PipelineNode | null;
}

/**
 * 하단 데이터 미리보기 탭.
 * 데이터셋 노드는 datasets.preview로 실제 행을 보여주고,
 * 변환 노드는 실행 전이므로 스키마 근거만 노출한다 (noCommit 설계).
 */
export function PreviewPanel({ node }: PreviewPanelProps) {
  if (!node) {
    return (
      <EmptyState
        icon={MousePointerClick}
        title="노드를 선택하세요"
        description="그래프에서 노드를 선택하면 해당 노드의 데이터 미리보기가 표시됩니다."
        className="m-3 border-0"
      />
    );
  }
  if (node.type === "dataset") {
    return <DatasetRowsPreview node={node} />;
  }
  return <TransformSchemaPreview node={node} />;
}

function DatasetRowsPreview({ node }: { node: PipelineNode }) {
  const client = useFoundryLiteClient();
  const datasetRef = nodeDatasetRef(node);
  const [namespace, name] = splitDatasetRef(datasetRef);

  const loadRows = useCallback(
    () => client.datasets.preview(namespace, name, { limit: PREVIEW_LIMIT }),
    [client, namespace, name],
  );
  const rowsQuery = useSafeQuery(
    ["pipelines", "dataset-preview", datasetRef],
    loadRows,
    { enabled: Boolean(namespace && name) },
  );

  const rows = useMemo(() => rowsQuery.data ?? [], [rowsQuery.data]);
  const columns = useMemo<DataTableColumn<Record<string, unknown>>[]>(() => {
    const keys = rows[0] ? Object.keys(rows[0]) : [];
    return keys.map((key) => ({
      key,
      header: key,
      isMono: true,
      render: (row) => formatCell(row[key]),
    }));
  }, [rows]);

  if (rowsQuery.isLoading) return <LoadingState rowCount={4} className="p-3" />;
  if (rowsQuery.error) {
    return (
      <ErrorState
        error={rowsQuery.error}
        onRetry={() => void rowsQuery.reload()}
        className="m-3"
      />
    );
  }

  return (
    <div className="space-y-2 p-3">
      <div className="flex items-center gap-2">
        <span className="section-label">{nodeLabel(node)} · 미리보기 행</span>
        <StatusPill intent="info">
          표시 {rows.length}행 / limit {PREVIEW_LIMIT}
        </StatusPill>
        {rowsQuery.requestId ? (
          <span className="ml-auto font-mono text-[10px] text-muted-foreground">
            req={rowsQuery.requestId}
          </span>
        ) : null}
      </div>
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(row) => JSON.stringify(row)}
        emptyMessage="미리볼 행이 없습니다."
      />
    </div>
  );
}

function TransformSchemaPreview({ node }: { node: PipelineNode }) {
  const schema = node.schema ?? [];
  const columns = useMemo<
    DataTableColumn<{ name: string; type: string; nullable?: boolean }>[]
  >(
    () => [
      { key: "name", header: "컬럼", isMono: true, render: (row) => row.name },
      { key: "type", header: "타입", isMono: true, render: (row) => row.type },
      {
        key: "nullable",
        header: "NULL 허용",
        render: (row) => (row.nullable === false ? "아니오" : "예"),
      },
    ],
    [],
  );

  return (
    <div className="space-y-2 p-3">
      <div className="flex items-center gap-2">
        <span className="section-label">{nodeLabel(node)} · 파생 스키마</span>
        <StatusPill intent="neutral">
          행 데이터는 파이프라인 실행 시 생성됩니다
        </StatusPill>
      </div>
      <DataTable
        columns={columns}
        rows={schema}
        rowKey={(row) => row.name}
        emptyMessage="파생된 스키마가 없습니다. 입력을 연결하고 저장하세요."
      />
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}
