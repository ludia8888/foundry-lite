import type { DatasetVersion } from "@foundry-lite/sdk";
import { History } from "lucide-react";

import type { DataTableColumn } from "@/components/shared/DataTable";
import { DataTable } from "@/components/shared/DataTable";
import { EmptyState } from "@/components/shared/EmptyState";
import { StatusPill } from "@/components/shared/StatusPill";

import {
  formatByteSize,
  formatRowCount,
  formatTimestamp,
} from "./dataset-schema";

interface VersionHistoryPanelProps {
  versions: readonly DatasetVersion[];
  inspectedVersionId: string | null;
  onSelectVersion: (versionId: string) => void;
}

const VERSION_COLUMNS: readonly DataTableColumn<DatasetVersion>[] = [
  {
    key: "version",
    header: "버전",
    isMono: true,
    render: (version) => `v${version.version_number}`,
  },
  {
    key: "branch",
    header: "브랜치",
    isMono: true,
    render: (version) => version.branch,
  },
  {
    key: "status",
    header: "상태",
    render: (version) => (
      <StatusPill intent={version.status === "active" ? "success" : "neutral"}>
        {version.status}
      </StatusPill>
    ),
  },
  {
    key: "rowCount",
    header: "행 수",
    isMono: true,
    render: (version) => formatRowCount(version.row_count),
  },
  {
    key: "byteSize",
    header: "크기",
    isMono: true,
    render: (version) => formatByteSize(version.byte_size),
  },
  {
    key: "schemaVersion",
    header: "스키마 버전",
    isMono: true,
    render: (version) => String(version.schema_version),
  },
  {
    key: "transaction",
    header: "트랜잭션",
    isMono: true,
    className: "max-w-64 truncate",
    render: (version) => (
      <span title={version.transaction_id}>{version.transaction_id}</span>
    ),
  },
  {
    key: "createdAt",
    header: "생성 시각",
    isMono: true,
    render: (version) => formatTimestamp(version.created_at),
  },
];

/** 버전 히스토리: version/transaction/row count/storage evidence 테이블. 행 클릭 시 해당 버전 inspect. */
export function VersionHistoryPanel({
  versions,
  inspectedVersionId,
  onSelectVersion,
}: VersionHistoryPanelProps) {
  if (versions.length === 0) {
    return (
      <EmptyState
        icon={History}
        title="커밋된 버전이 없습니다"
        description="트랜잭션이 커밋되면 버전과 manifest evidence가 여기에 기록됩니다."
      />
    );
  }

  return (
    <div className="space-y-2">
      <DataTable
        columns={VERSION_COLUMNS}
        rows={versions}
        rowKey={(version) => version.id}
        onRowClick={(version) => onSelectVersion(version.id)}
        selectedKey={inspectedVersionId}
      />
      <p className="text-xs text-muted-foreground">
        행을 클릭하면 해당 버전 기준으로 스키마/manifest를 다시 검사합니다.
      </p>
    </div>
  );
}
