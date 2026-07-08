import { useCallback, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";

import {
  FAILING_SQL,
  referencedInputs,
  SAMPLE_SQL,
  type RegisteredTransform,
} from "./code-model";
import { CodeStatusBar } from "./components/CodeStatusBar";
import { CodeToolbar } from "./components/CodeToolbar";
import { ExecutionPanel } from "./components/ExecutionPanel";
import { SqlEditor } from "./components/SqlEditor";
import { TransformTree } from "./components/TransformTree";
import { useCodeRepository } from "./use-code-repository";

const DEFAULT_OUTPUT_REF = "pipelines.pipeline_demo_probe_output";
const REPOSITORY_NAME = "supply-chain-transforms";

const PHASE_STATUS_LABEL: Record<string, string> = {
  idle: "대기",
  registering: "등록 중",
  running: "실행 중",
  succeeded: "성공",
  failed: "실패",
};

/** dataset ref에서 SQL 입력 alias를 만든다 (`clean.orders` → `clean_orders`). */
function aliasForRef(ref: string): string {
  return ref.replace(/[^A-Za-z0-9]+/g, "_");
}

/**
 * Code Repositories / Transforms (route /code) — partial IDE.
 * 좌: transform·dataset 트리 / 중앙: SQL 에디터 / 우: 실행·빌드 evidence.
 * SQL transform 등록→실행→출력 데이터셋 확인→Dataset Preview 딥링크, 실패→에러→수정 재실행.
 * 웹 IDE(git/PR/diff/debugger)와 Python 런타임은 future로 명시한다.
 */
export default function CodeRepositoriesPage() {
  const code = useCodeRepository();

  const [apiName, setApiName] = useState("orders_over_500");
  const [sql, setSql] = useState(SAMPLE_SQL);
  const [inputs, setInputs] = useState<Record<string, string>>({
    clean_orders: "clean.orders",
  });
  const [outputDatasetRef, setOutputDatasetRef] = useState(DEFAULT_OUTPUT_REF);

  const handleAddInput = useCallback((ref: string) => {
    setInputs((prev) => {
      if (Object.values(prev).includes(ref)) return prev;
      return { ...prev, [aliasForRef(ref)]: ref };
    });
  }, []);

  const handleRemoveInput = useCallback((alias: string) => {
    setInputs((prev) => {
      const next = { ...prev };
      delete next[alias];
      return next;
    });
  }, []);

  const handleSelectTransform = useCallback(
    (transform: RegisteredTransform) => {
      // 등록된 transform 선택 → 에디터 컨텍스트(API 이름·출력 ref)를 로드.
      // (SQL 원문 조회 surface는 미제공이라 정의 메타만 채운다.)
      setApiName(transform.apiName);
      setOutputDatasetRef(transform.outputDatasetRef);
    },
    [],
  );

  const buildInputs = useMemo(
    () => referencedInputs(sql, inputs),
    [inputs, sql],
  );

  const handleBuild = useCallback(() => {
    void code.registerAndRun({
      apiName,
      sql,
      inputs: buildInputs,
      outputDatasetRef,
    });
  }, [apiName, buildInputs, code, outputDatasetRef, sql]);

  const handleLoadFailingSample = useCallback(() => {
    setSql(FAILING_SQL);
  }, []);

  const canBuild = useMemo(
    () =>
      apiName.trim().length > 0 &&
      sql.trim().length > 0 &&
      outputDatasetRef.trim().length > 0 &&
      Object.keys(buildInputs).length > 0,
    [apiName, buildInputs, outputDatasetRef, sql],
  );

  const isBuilding =
    code.submit.phase === "registering" || code.submit.phase === "running";

  return (
    <div className="flex h-full flex-col">
      <CodeToolbar
        repositoryName={REPOSITORY_NAME}
        isBuilding={isBuilding}
        canBuild={canBuild}
        onBuild={handleBuild}
      />

      {code.transformsQuery.error ? (
        <div className="border-b px-3 py-2">
          <ErrorState
            error={code.transformsQuery.error}
            onRetry={() => void code.transformsQuery.reload()}
          />
        </div>
      ) : null}

      <div className="flex min-h-0 flex-1">
        <TransformTree
          transforms={code.registeredTransforms}
          datasets={code.datasets}
          selectedApiName={apiName}
          isLoading={
            code.transformsQuery.isLoading || code.datasetsQuery.isLoading
          }
          onSelectTransform={handleSelectTransform}
          onAddInput={handleAddInput}
          onRefresh={() => {
            void code.transformsQuery.reload();
            void code.datasetsQuery.reload();
          }}
        />

        <SqlEditor
          apiName={apiName}
          sql={sql}
          inputs={inputs}
          outputDatasetRef={outputDatasetRef}
          onChangeApiName={setApiName}
          onChangeSql={setSql}
          onChangeOutputDatasetRef={setOutputDatasetRef}
          onRemoveInput={handleRemoveInput}
          onLoadFailingSample={handleLoadFailingSample}
        />

        <ExecutionPanel
          code={code}
          lastIdempotencyKey={code.lastIdempotencyKey}
          onRerun={handleBuild}
          canRerun={canBuild}
        />
      </div>

      <CodeStatusBar
        transformCount={code.registeredTransforms.length}
        datasetCount={code.datasets.length}
        phaseLabel={PHASE_STATUS_LABEL[code.submit.phase] ?? "대기"}
      />
    </div>
  );
}
