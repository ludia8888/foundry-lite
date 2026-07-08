import type {
  Dataset,
  TabularRow,
  TransformRunResult,
  TransformSchedulerTickResult,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteProvidedSqlTransformSubmit,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { useCallback, useMemo, useState } from "react";

import { toRegisteredTransform, type RegisteredTransform } from "./code-model";

const TRANSFORM_LIST_LIMIT = 200;
const PREVIEW_LIMIT = 20;

export type OutputPreview = {
  datasetRef: string;
  rows: TabularRow[];
};

/**
 * Code Repositories(/code) 화면 데이터+뮤테이션 묶음.
 * - 등록된 SQL transform 목록: transforms.previewDue (scheduler 평가 결과에 등록 transform이 노출됨)
 * - 입력 데이터셋 카탈로그: datasets.list
 * - 등록+실행: useFoundryLiteProvidedSqlTransformSubmit (register → run)
 * - 출력 프리뷰: datasets.preview
 * mutation은 SDK idempotency 규약을 따르고 request id를 evidence로 노출한다.
 */
export function useCodeRepository() {
  const client = useFoundryLiteClient();
  const [lastIdempotencyKey, setLastIdempotencyKey] = useState<string | null>(
    null,
  );
  const [outputPreview, setOutputPreview] = useState<OutputPreview | null>(
    null,
  );

  const transformsQuery = useFoundryLiteQuery(
    ["code", "transforms", "due"],
    () => client.transforms.previewDue({ maxRuns: TRANSFORM_LIST_LIMIT }),
  );

  const datasetsQuery = useFoundryLiteQuery(["code", "datasets"], () =>
    client.datasets.list(),
  );

  const submit = useFoundryLiteProvidedSqlTransformSubmit({
    onSuccess: () => {
      void transformsQuery.reload();
    },
  });

  const schedulerTick = useFoundryLiteMutation<
    TransformSchedulerTickResult,
    undefined
  >(() => client.transforms.tick({ maxRuns: TRANSFORM_LIST_LIMIT }), {
    onSuccess: () => {
      void transformsQuery.reload();
      void datasetsQuery.reload();
    },
  });

  const registeredTransforms = useMemo<RegisteredTransform[]>(() => {
    const due = transformsQuery.data?.due ?? [];
    const skipped = transformsQuery.data?.skipped ?? [];
    const decisions = [...due, ...skipped];
    const byApiName = new Map<string, RegisteredTransform>();
    for (const decision of decisions) {
      byApiName.set(decision.apiName, toRegisteredTransform(decision));
    }
    return [...byApiName.values()].sort((a, b) =>
      a.apiName.localeCompare(b.apiName),
    );
  }, [transformsQuery.data]);

  const datasets = useMemo<Dataset[]>(
    () => datasetsQuery.data ?? [],
    [datasetsQuery.data],
  );

  const registerAndRun = useCallback(
    (payload: {
      apiName: string;
      sql: string;
      inputs: Record<string, string>;
      outputDatasetRef: string;
    }) => {
      const key = idempotencyKey("transform-sql-run", payload.apiName);
      setLastIdempotencyKey(key);
      setOutputPreview(null);
      return submit.execute({
        definition: {
          apiName: payload.apiName,
          sql: payload.sql,
          inputs: payload.inputs,
          outputDatasetRef: payload.outputDatasetRef,
        },
        run: true,
      });
    },
    [submit],
  );

  const loadOutputPreview = useFoundryLiteMutation(
    async (run: TransformRunResult) => {
      const [namespace, ...rest] = run.dataset_ref.split(".");
      const name = rest.join(".");
      const rows = await client.datasets.preview(namespace, name, {
        limit: PREVIEW_LIMIT,
      });
      return { datasetRef: run.dataset_ref, rows };
    },
    {
      lockKey: (run) => `code:preview:${run.dataset_ref}`,
      onSuccess: (preview) => setOutputPreview(preview),
    },
  );

  return {
    transformsQuery,
    datasetsQuery,
    registeredTransforms,
    datasets,
    submit,
    schedulerTick,
    registerAndRun,
    lastIdempotencyKey,
    loadOutputPreview,
    outputPreview,
  };
}

export type CodeRepositoryState = ReturnType<typeof useCodeRepository>;
