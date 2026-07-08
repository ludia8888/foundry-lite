import {
  foundryLiteDatasetExplorerView,
  useFoundryLiteClient,
  type FoundryLiteDatasetExplorerData,
  type FoundryLiteDatasetExplorerState,
} from "@foundry-lite/sdk/react";
import { createDatasetExplorerRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useCallback, useMemo } from "react";

import { useLineageQuery } from "@/features/lineage/use-lineage-query";

interface LineageDatasetExplorerSelection {
  namespace: string | null;
  name: string | null;
  previewLimit: number;
}

/**
 * createDatasetExplorerRecipe + foundryLiteDatasetExplorerView 조합.
 * SDK의 useFoundryLiteDatasetExplorer(useFoundryLiteQuery 기반)는 StrictMode에서
 * 결과 반영이 막히는 문제가 있어 StrictMode-safe 로컬 프리미티브로 대체한다.
 */
export function useLineageDatasetExplorer(
  selection: LineageDatasetExplorerSelection,
): FoundryLiteDatasetExplorerState {
  const client = useFoundryLiteClient();
  const recipe = useMemo(() => createDatasetExplorerRecipe(client), [client]);
  const { namespace, name, previewLimit } = selection;

  const load =
    useCallback(async (): Promise<FoundryLiteDatasetExplorerData> => {
      if (!namespace || !name) {
        return {
          datasets: await recipe.listDatasets(),
          versions: [],
          inspection: null,
          previewRows: [],
          qualitySummary: null,
          lineage: [],
        };
      }
      const datasetSelection = { namespace, name, previewLimit };
      const [
        datasets,
        versions,
        inspection,
        previewRows,
        qualitySummary,
        lineage,
      ] = await Promise.all([
        recipe.listDatasets(),
        recipe.listVersions(datasetSelection),
        recipe.inspect(datasetSelection),
        recipe.preview(datasetSelection),
        recipe.qualitySummary(datasetSelection),
        recipe.lineage(datasetSelection),
      ]);
      return {
        datasets,
        versions,
        inspection,
        previewRows,
        qualitySummary,
        lineage,
      };
    }, [recipe, namespace, name, previewLimit]);

  const query = useLineageQuery<FoundryLiteDatasetExplorerData>(
    ["lineage", "dataset-explorer", namespace, name, previewLimit],
    load,
  );
  const view = useMemo(
    () =>
      foundryLiteDatasetExplorerView(query.data, {
        namespace,
        name,
        previewLimit,
      }),
    [query.data, namespace, name, previewLimit],
  );
  return { ...query, ...view };
}
