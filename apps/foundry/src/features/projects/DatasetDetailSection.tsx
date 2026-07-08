import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";

import { formatByteSize, formatTimestamp } from "./resource-model";
import { useProjectsQuery } from "./use-projects-query";

interface DatasetDetailSectionProps {
  namespace: string;
  name: string;
}

/**
 * 데이터셋 evidence 섹션: 버전·row count·크기·lineage를 숨기지 않고 표시한다.
 * SDK datasets.versions / datasets.inspect / operations.lineage.get 사용.
 */
export function DatasetDetailSection({
  namespace,
  name,
}: DatasetDetailSectionProps) {
  const client = useFoundryLiteClient();
  const datasetRef = `${namespace}.${name}`;

  const loadVersions = useCallback(
    () => client.datasets.versions(namespace, name),
    [client, namespace, name],
  );
  const loadInspection = useCallback(
    () => client.datasets.inspect(namespace, name),
    [client, namespace, name],
  );
  const loadLineage = useCallback(
    () => client.operations.lineage.get(datasetRef),
    [client, datasetRef],
  );

  const versionsQuery = useProjectsQuery(
    ["projects", "dataset-versions", datasetRef],
    loadVersions,
  );
  const inspectionQuery = useProjectsQuery(
    ["projects", "dataset-inspect", datasetRef],
    loadInspection,
  );
  const lineageQuery = useProjectsQuery(
    ["projects", "dataset-lineage", datasetRef],
    loadLineage,
  );

  const isLoading =
    versionsQuery.isLoading ||
    inspectionQuery.isLoading ||
    lineageQuery.isLoading;
  const error =
    versionsQuery.error ?? inspectionQuery.error ?? lineageQuery.error;
  if (isLoading) return <LoadingState rowCount={4} />;
  if (error) {
    return (
      <ErrorState
        error={error}
        onRetry={() =>
          void Promise.all([
            versionsQuery.reload(),
            inspectionQuery.reload(),
            lineageQuery.reload(),
          ])
        }
      />
    );
  }

  const versions = versionsQuery.data ?? [];
  const latestVersion = versions[0] ?? null;
  const manifestFiles = inspectionQuery.data?.manifest.files ?? [];
  const lineageEdges = lineageQuery.data ?? [];

  return (
    <div className="space-y-3">
      <section className="space-y-1.5">
        <div className="section-label">데이터 EVIDENCE</div>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5 text-[12px]">
          <dt className="text-muted-foreground">행 수</dt>
          <dd className="text-right font-mono text-[11px]">
            {latestVersion?.row_count ?? "—"}
          </dd>
          <dt className="text-muted-foreground">크기</dt>
          <dd className="text-right font-mono text-[11px]">
            {formatByteSize(latestVersion?.byte_size ?? null)}
          </dd>
          <dt className="text-muted-foreground">버전 수</dt>
          <dd className="text-right font-mono text-[11px]">
            {versions.length}
          </dd>
          <dt className="text-muted-foreground">manifest 파일</dt>
          <dd className="text-right font-mono text-[11px]">
            {manifestFiles.length}
          </dd>
        </dl>
      </section>

      <section className="space-y-1.5">
        <div className="section-label">최근 버전</div>
        {versions.length > 0 ? (
          <div className="space-y-1">
            {versions.slice(0, 3).map((version) => (
              <div
                key={version.id}
                className="flex items-center gap-2 rounded border px-2 py-1"
              >
                <span className="font-mono text-[11px]">
                  v{version.version_number}
                </span>
                <StatusPill
                  intent={
                    version.status === "committed" ? "success" : "neutral"
                  }
                >
                  {version.status}
                </StatusPill>
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  {version.row_count}행 · {formatTimestamp(version.created_at)}
                </span>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">버전이 없습니다.</p>
        )}
      </section>

      <section className="space-y-1.5">
        <div className="section-label">LINEAGE</div>
        {lineageEdges.length > 0 ? (
          <div className="space-y-1">
            {lineageEdges.slice(0, 5).map((edge) => (
              <div
                key={edge.id}
                className="rounded border px-2 py-1 font-mono text-[11px]"
              >
                {edge.from_resource_id}
                <span className="text-muted-foreground">
                  {" "}
                  → {edge.relation} →{" "}
                </span>
                {edge.to_resource_id}
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            이 리소스 ID로 기록된 lineage edge가 없습니다.
          </p>
        )}
      </section>
    </div>
  );
}
