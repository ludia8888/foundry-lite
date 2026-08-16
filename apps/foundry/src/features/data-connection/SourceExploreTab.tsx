import type {
  SourceConnection,
  SourceExploreRequest,
  SourceExploreResult,
} from "@foundry-lite/sdk";
import { normalizeFoundryLiteError } from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
} from "@foundry-lite/sdk/react";
import { Info, Loader2, Search } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import type { ExploreTablePreview } from "./explore-model";
import {
  inferForeignKeyEdges,
  readExploreSampleRows,
  readExploreSchemaColumns,
  readExploreTables,
} from "./explore-model";
import { ExploreGraphView } from "./explore/ExploreGraphView";
import { KafkaSourceExplorer } from "./explore/KafkaSourceExplorer";
import { ExplorePreviewPane } from "./explore/ExplorePreviewPane";
import { RestSourceExplorer } from "./explore/RestSourceExplorer";
import { ExploreSourcePanel } from "./explore/ExploreSourcePanel";
import { ExploreSyncPanel } from "./explore/ExploreSyncPanel";
import { SourceExplorationEvidenceLink } from "./explore/SourceExplorationEvidenceLink";
import { readTextField, sourceExplorationEvidence } from "./source-model";
import { WizardField } from "./wizard/WizardFields";

interface SourceExploreTabProps {
  source: SourceConnection;
  onSyncCreated?: (syncName: string) => void;
  onCreateSync?: (resourceName: string) => void;
}

/**
 * 소스 탐색 탭 (공식 db-explorer 구조): 상단 정보 배너 + 3-pane
 * (좌 소스 미리보기 트리 / 중앙 프리뷰 탭 + 접이식 그래프 뷰 /
 * 우 동기화할 테이블 패널 + 일괄 sync 생성). sources.exploration.run 사용.
 */
export function SourceExploreTab({
  source,
  onSyncCreated,
  onCreateSync,
}: SourceExploreTabProps) {
  if (source.kind === "kafka") {
    return <KafkaSourceExplorer source={source} onCreateSync={onCreateSync} />;
  }
  if (
    source.kind === "rest" ||
    source.kind === "rest_api" ||
    source.kind === "sap_odata"
  ) {
    return <RestSourceExplorer source={source} onCreateSync={onCreateSync} />;
  }
  return (
    <DatabaseSourceExplorer
      source={source}
      onSyncCreated={onSyncCreated}
    />
  );
}

function DatabaseSourceExplorer({
  source,
  onSyncCreated,
}: Pick<SourceExploreTabProps, "source" | "onSyncCreated">) {
  const client = useFoundryLiteClient();
  const [databaseUrlSecretRef, setDatabaseUrlSecretRef] = useState(
    () => readTextField(source.configSummary, "databaseUrlSecretRef") ?? "",
  );
  const [sampleLimitText, setSampleLimitText] = useState("20");

  const [openTables, setOpenTables] = useState<string[]>([]);
  const [activeTable, setActiveTable] = useState<string | null>(null);
  const [previewsByTable, setPreviewsByTable] = useState<
    Record<string, ExploreTablePreview>
  >({});
  const [selectedTables, setSelectedTables] = useState<string[]>([]);

  const treeMutation = useFoundryLiteMutation<
    SourceExploreResult,
    SourceExploreRequest
  >((payload) => client.sources.exploration.run(payload));

  const sampleLimit = Number.parseInt(sampleLimitText, 10) || 20;
  const canExplore = databaseUrlSecretRef.trim().length > 0;

  const handleExplore = async () => {
    setOpenTables([]);
    setActiveTable(null);
    setPreviewsByTable({});
    setSelectedTables([]);
    await treeMutation.execute({
      sourceName: source.sourceName,
      sourceType: "postgres_jdbc",
      request: {
        databaseUrlSecretRef: databaseUrlSecretRef.trim(),
        sampleLimit,
      },
    });
  };

  const runTablePreview = useCallback(
    async (tableName: string) => {
      setPreviewsByTable((current) => ({
        ...current,
        [tableName]: {
          status: "running",
          rows: [],
          columns: [],
          explorationRunId: null,
          operationsPath: null,
          errorMessage: null,
        },
      }));
      try {
        const result = await client.sources.exploration.run({
          sourceName: source.sourceName,
          sourceType: "postgres_jdbc",
          request: {
            databaseUrlSecretRef: databaseUrlSecretRef.trim(),
            tableName,
            sampleLimit,
          },
        });
        setPreviewsByTable((current) => ({
          ...current,
          [tableName]: {
            status: result.status === "succeeded" ? "succeeded" : "failed",
            rows: readExploreSampleRows(result.resultSummary),
            columns: readExploreSchemaColumns(result.resultSummary),
            explorationRunId: result.explorationRunId,
            operationsPath: result.operationsPath,
            errorMessage: result.error ? JSON.stringify(result.error) : null,
          },
        }));
      } catch (caught) {
        const normalized = normalizeFoundryLiteError(caught);
        const evidence = sourceExplorationEvidence(null, normalized.details);
        setPreviewsByTable((current) => ({
          ...current,
          [tableName]: {
            status: "failed",
            rows: [],
            columns: [],
            explorationRunId: evidence?.runId ?? null,
            operationsPath: evidence?.operationsPath ?? null,
            errorMessage: normalized.message,
          },
        }));
      }
    },
    [client, source.sourceName, databaseUrlSecretRef, sampleLimit],
  );

  const handlePreviewTable = (tableName: string) => {
    setOpenTables((current) =>
      current.includes(tableName) ? current : [...current, tableName],
    );
    setActiveTable(tableName);
    const cached = previewsByTable[tableName];
    if (cached && cached.status !== "failed") return;
    void runTablePreview(tableName);
  };

  const handleCloseTable = (tableName: string) => {
    const next = openTables.filter((name) => name !== tableName);
    setOpenTables(next);
    if (activeTable === tableName) {
      setActiveTable(next[next.length - 1] ?? null);
    }
  };

  const handleToggleSelect = (tableName: string) => {
    setSelectedTables((current) =>
      current.includes(tableName)
        ? current.filter((name) => name !== tableName)
        : [...current, tableName],
    );
  };

  const handleSelectTables = (tableNames: string[]) => {
    setSelectedTables((current) => {
      const merged = [...current];
      for (const name of tableNames) {
        if (!merged.includes(name)) merged.push(name);
      }
      return merged;
    });
  };

  const tables = useMemo(
    () => readExploreTables(treeMutation.result?.resultSummary),
    [treeMutation.result],
  );
  const fkEdges = useMemo(() => inferForeignKeyEdges(tables), [tables]);
  const hasTree = treeMutation.result !== null;

  return (
    <div className="space-y-3">
      <div className="flex items-start gap-2 rounded border border-primary/30 bg-primary/10 px-3 py-2 text-xs">
        <Info className="mt-0.5 size-3.5 shrink-0 text-primary" />
        <p>
          아직 Foundry로 동기화되지 않은 원격 시스템의 테이블 리소스를 미리보는
          중입니다. 리소스 목록에서 테이블 리소스를 선택한 뒤 동기화를 생성해
          데이터를 Foundry로 가져오세요.
        </p>
      </div>
      <ExploreConfigCard
        databaseUrlSecretRef={databaseUrlSecretRef}
        onDatabaseUrlSecretRefChange={setDatabaseUrlSecretRef}
        sampleLimitText={sampleLimitText}
        onSampleLimitChange={setSampleLimitText}
        canExplore={canExplore}
        isRunning={treeMutation.isRunning}
        onExplore={() => void handleExplore()}
      />
      {treeMutation.error ? (
        <div className="space-y-2">
          <ErrorState error={treeMutation.error} />
          <SourceExplorationEvidenceLink
            error={treeMutation.error}
            className="inline-block text-xs"
            label="실패한 Source Explorer 실행 조사"
          />
        </div>
      ) : null}
      {hasTree ? (
        <div className="flex h-[calc(100vh-260px)] min-h-[520px] overflow-hidden rounded border bg-card">
          <ExploreSourcePanel
            tables={tables}
            explorationRunId={treeMutation.result?.explorationRunId ?? null}
            operationsPath={treeMutation.result?.operationsPath ?? null}
            activeTable={activeTable}
            selectedTables={selectedTables}
            onPreviewTable={handlePreviewTable}
            onToggleSelect={handleToggleSelect}
            onSelectTables={handleSelectTables}
          />
          <div className="flex min-w-0 flex-1 flex-col">
            <ExplorePreviewPane
              openTables={openTables}
              activeTable={activeTable}
              previewsByTable={previewsByTable}
              selectedTables={selectedTables}
              onActivateTable={handlePreviewTable}
              onCloseTable={handleCloseTable}
              onToggleSelect={handleToggleSelect}
            />
            <ExploreGraphView
              tables={tables}
              fkEdges={fkEdges}
              activeTable={activeTable}
              selectedTables={selectedTables}
              onPreviewTable={handlePreviewTable}
              onToggleSelect={handleToggleSelect}
              onCreateSyncForTable={(tableName) =>
                handleSelectTables([tableName])
              }
            />
          </div>
          <ExploreSyncPanel
            source={source}
            selectedTables={selectedTables}
            databaseUrlSecretRef={databaseUrlSecretRef.trim()}
            onRemoveTable={handleToggleSelect}
            onSyncCreated={onSyncCreated}
          />
        </div>
      ) : null}
    </div>
  );
}

function ExploreConfigCard({
  databaseUrlSecretRef,
  onDatabaseUrlSecretRefChange,
  sampleLimitText,
  onSampleLimitChange,
  canExplore,
  isRunning,
  onExplore,
}: {
  databaseUrlSecretRef: string;
  onDatabaseUrlSecretRefChange: (value: string) => void;
  sampleLimitText: string;
  onSampleLimitChange: (value: string) => void;
  canExplore: boolean;
  isRunning: boolean;
  onExplore: () => void;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="section-label border-b px-3 py-2">탐색 설정</div>
      <div className="flex flex-wrap items-end gap-3 p-3">
        <WizardField label="탐색 타입" className="w-48">
          <div className="flex h-8 items-center rounded border bg-muted/20 px-2.5 text-xs">
            데이터베이스 (Postgres)
          </div>
        </WizardField>
        <WizardField
          label="DB URL secret 참조"
          helper="소스 구성에서 자동으로 채워집니다."
          className="w-64"
        >
          <Input
            value={databaseUrlSecretRef}
            onChange={(event) =>
              onDatabaseUrlSecretRefChange(event.target.value)
            }
            placeholder="source_orders_db_cred"
            className="h-8 font-mono text-xs"
          />
        </WizardField>
        <WizardField label="샘플 limit">
          <Input
            value={sampleLimitText}
            onChange={(event) => onSampleLimitChange(event.target.value)}
            placeholder="20"
            className="h-8 w-24 font-mono text-xs"
          />
        </WizardField>
        <Button
          size="sm"
          disabled={!canExplore || isRunning}
          onClick={onExplore}
        >
          {isRunning ? (
            <Loader2 className="size-3.5 animate-spin" />
          ) : (
            <Search className="size-3.5" />
          )}
          리소스 탐색
        </Button>
      </div>
    </div>
  );
}
