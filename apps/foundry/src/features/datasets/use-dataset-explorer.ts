import type { DatasetVersion } from "@foundry-lite/sdk";
import type {
  FoundryLiteDatasetExplorerData,
  FoundryLiteDatasetExplorerView,
} from "@foundry-lite/sdk/react";
import {
  foundryLiteDatasetExplorerView,
  useFoundryLiteClient,
} from "@foundry-lite/sdk/react";
import { createDatasetExplorerRecipe } from "@foundry-lite/sdk/screen-recipes";
import { useCallback, useMemo } from "react";

import { isDatasetMissingError } from "@/lib/errors";

import type { ScreenQueryState } from "./use-screen-query";
import { useScreenQuery } from "./use-screen-query";

export interface DatasetExplorerSelectionInput {
  namespace: string;
  name: string;
  version?: string;
  previewLimit?: number;
}

export type DatasetExplorerState =
  ScreenQueryState<FoundryLiteDatasetExplorerData> &
    FoundryLiteDatasetExplorerView;

const EMPTY_EXPLORER_DATA: FoundryLiteDatasetExplorerData = {
  datasets: [],
  versions: [],
  inspection: null,
  previewRows: [],
  qualitySummary: null,
  lineage: [],
};

function selectedLineageResourceId(
  selection: DatasetExplorerSelectionInput,
  versions: readonly DatasetVersion[],
): string | undefined {
  if (versions.length === 0) return undefined;
  if (selection.version) {
    return versions.find(
      (version) =>
        version.id === selection.version ||
        String(version.version_number) === selection.version,
    )?.id;
  }
  return versions.reduce((latest, version) =>
    version.version_number > latest.version_number ? version : latest,
  ).id;
}

/**
 * createDatasetExplorerRecipe(SDK) 호출을 useScreenQuery로 묶고
 * foundryLiteDatasetExplorerView(SDK)로 화면 모델을 만든다.
 * (Provided 훅 대신 recipe를 쓰는 이유는 use-screen-query.ts 주석 참고)
 */
export function useDatasetExplorer(
  selection: DatasetExplorerSelectionInput | null,
): DatasetExplorerState {
  const client = useFoundryLiteClient();
  const recipe = useMemo(() => createDatasetExplorerRecipe(client), [client]);
  const selectionKey = JSON.stringify(selection ?? null);

  const load =
    useCallback(async (): Promise<FoundryLiteDatasetExplorerData> => {
      const datasetsPromise = recipe.listDatasets();
      if (!selection) {
        return { ...EMPTY_EXPLORER_DATA, datasets: await datasetsPromise };
      }
      const datasets = await datasetsPromise;
      const selectedExists = datasets.some(
        (dataset) =>
          dataset.namespace === selection.namespace &&
          dataset.name === selection.name,
      );
      if (!selectedExists) {
        return { ...EMPTY_EXPLORER_DATA, datasets };
      }
      const versions = await recipe.listVersions(selection);
      const lineageResourceId = selectedLineageResourceId(selection, versions);
      const lineageSelection = lineageResourceId
        ? { ...selection, lineageResourceId }
        : selection;
      const selectedDatasetPromise = Promise.all([
        recipe.inspect(selection),
        recipe.preview(selection),
        recipe.qualitySummary(selection),
        recipe.lineage(lineageSelection),
      ]);
      try {
        const [inspection, previewRows, qualitySummary, lineage] =
          await selectedDatasetPromise;
        return {
          datasets,
          versions,
          inspection,
          previewRows,
          qualitySummary,
          lineage,
        };
      } catch (caught) {
        if (isDatasetMissingError(caught)) {
          return { ...EMPTY_EXPLORER_DATA, datasets };
        }
        throw caught;
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [recipe, selectionKey]);

  const query = useScreenQuery(["datasets", "explorer", selectionKey], load);
  const view = useMemo(
    () => foundryLiteDatasetExplorerView(query.data, selection ?? {}),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [query.data, selectionKey],
  );

  return { ...query, ...view };
}
