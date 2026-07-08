import type { DatasetManifest } from "@foundry-lite/sdk";
import { ChevronDown, ChevronRight, KeyRound } from "lucide-react";
import { useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";

import type { SchemaColumn } from "./dataset-schema";
import { formatByteSize, formatRowCount } from "./dataset-schema";

interface SchemaPanelProps {
  columns: readonly SchemaColumn[];
  primaryKey: readonly string[];
  manifest: DatasetManifest | null;
}

/** 우측 스키마 패널: 컬럼 목록 + PK/nullable 배지 + manifest evidence (항상 프리뷰와 함께 표시). */
export function SchemaPanel({
  columns,
  primaryKey,
  manifest,
}: SchemaPanelProps) {
  return (
    <div className="flex min-h-0 flex-col rounded border bg-card">
      <div className="flex items-center justify-between border-b px-3 py-2">
        <span className="section-label">스키마</span>
        <span className="font-mono text-[11px] text-muted-foreground">
          {columns.length}개 컬럼
        </span>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {columns.length === 0 ? (
          <p className="px-3 py-3 text-xs text-muted-foreground">
            스키마 정보가 없습니다.
          </p>
        ) : (
          columns.map((column) => (
            <SchemaColumnRow
              key={column.name}
              column={column}
              isPrimaryKey={primaryKey.includes(column.name)}
            />
          ))
        )}
      </div>

      {manifest ? <ManifestEvidence manifest={manifest} /> : null}
    </div>
  );
}

interface SchemaColumnRowProps {
  column: SchemaColumn;
  isPrimaryKey: boolean;
}

function SchemaColumnRow({ column, isPrimaryKey }: SchemaColumnRowProps) {
  return (
    <div className="flex h-8 items-center gap-2 border-b px-3 last:border-b-0">
      <span className="min-w-0 flex-1 truncate font-mono text-[11px] font-medium">
        {column.name}
      </span>
      {isPrimaryKey ? (
        <StatusPill intent="info" className="gap-0.5">
          <KeyRound className="size-2.5" />
          PK
        </StatusPill>
      ) : null}
      {!column.nullable ? (
        <StatusPill intent="neutral">not null</StatusPill>
      ) : null}
      <span className="shrink-0 font-mono text-[10px] text-muted-foreground lowercase">
        {column.type}
      </span>
    </div>
  );
}

function ManifestEvidence({ manifest }: { manifest: DatasetManifest }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="border-t">
      <button
        type="button"
        onClick={() => setIsOpen((previous) => !previous)}
        className="flex h-8 w-full items-center gap-1.5 px-3 text-left hover:bg-muted/60"
      >
        {isOpen ? (
          <ChevronDown className="size-3.5 text-muted-foreground" />
        ) : (
          <ChevronRight className="size-3.5 text-muted-foreground" />
        )}
        <span className="section-label">Manifest evidence</span>
        <span className="ml-auto font-mono text-[11px] text-muted-foreground">
          {manifest.files.length}개 파일
        </span>
      </button>
      {isOpen ? (
        <div className="space-y-1.5 px-3 pt-1 pb-2 font-mono text-[11px] text-muted-foreground">
          <div className="truncate" title={manifest.schema_hash}>
            schema_hash={manifest.schema_hash}
          </div>
          <div className="truncate" title={manifest.version_id}>
            version_id={manifest.version_id}
          </div>
          {manifest.files.map((file) => (
            <div
              key={file.uri}
              className="rounded border bg-muted/40 px-2 py-1.5"
            >
              <div className="truncate text-foreground/80" title={file.uri}>
                {file.uri.split("/").slice(-1)[0]}
              </div>
              <div>
                {file.format} · {formatRowCount(file.row_count)}행 ·{" "}
                {formatByteSize(file.byte_size)}
              </div>
              <div className="truncate" title={file.content_hash}>
                hash={file.content_hash}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
