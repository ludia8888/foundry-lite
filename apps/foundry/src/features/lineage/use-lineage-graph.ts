import type { DatasetVersion, LineageEdge } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  type FoundryLiteQueryState,
} from "@foundry-lite/sdk/react";
import { useCallback } from "react";

import type { LineageGraphSource } from "@/features/lineage/lineage-model";
import { datasetRefOf } from "@/features/lineage/lineage-model";
import { useLineageQuery } from "@/features/lineage/use-lineage-query";

const GRAPH_SOURCE_KEY = ["lineage", "graph-source"] as const;

function dedupeLineageEdges(edges: readonly LineageEdge[]): LineageEdge[] {
  const edgesById = new Map<string, LineageEdge>();
  for (const edge of edges) {
    if (!edgesById.has(edge.id)) edgesById.set(edge.id, edge);
  }
  return [...edgesById.values()];
}

/**
 * 리니지 그래프 원천 데이터 로더.
 * datasets.list → 데이터셋별 versions → 최신 버전 id 기준 operations.lineage.get.
 * (lineage edge는 dataset_version 리소스 id로 기록되므로 version id로 조회한다)
 */
export function useLineageGraphSource(): FoundryLiteQueryState<LineageGraphSource> {
  const client = useFoundryLiteClient();

  const load = useCallback(async (): Promise<LineageGraphSource> => {
    const datasets = await client.datasets.list();
    const versionLists = await Promise.all(
      datasets.map((dataset) =>
        client.datasets.versions(dataset.namespace, dataset.name),
      ),
    );
    const versionsByDatasetRef: Record<string, DatasetVersion[]> = {};
    datasets.forEach((dataset, index) => {
      versionsByDatasetRef[datasetRefOf(dataset)] = versionLists[index] ?? [];
    });
    const latestVersionIds = versionLists
      .map((versions) => versions[0]?.id)
      .filter((versionId): versionId is string => Boolean(versionId));
    const edgeLists = await Promise.all(
      latestVersionIds.map((versionId) =>
        client.operations.lineage.get(versionId),
      ),
    );
    return {
      datasets,
      versionsByDatasetRef,
      lineageEdges: dedupeLineageEdges(edgeLists.flat()),
    };
  }, [client]);

  return useLineageQuery<LineageGraphSource>(GRAPH_SOURCE_KEY, load);
}
