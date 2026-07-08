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
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

import type { ExploreTablePreview } from "./explore-model";
import {
  formatSampleCell,
  inferForeignKeyEdges,
  readExploreSampleRows,
  readExploreSchemaColumns,
  readExploreTables,
} from "./explore-model";
import { ExploreGraphView } from "./explore/ExploreGraphView";
import { ExplorePreviewPane } from "./explore/ExplorePreviewPane";
import { ExploreSourcePanel } from "./explore/ExploreSourcePanel";
import { ExploreSyncPanel } from "./explore/ExploreSyncPanel";
import { readTextField } from "./source-model";
import { WizardField } from "./wizard/WizardFields";

type ExploreType = "postgres_jdbc" | "rest_api";

interface SourceExploreTabProps {
  source: SourceConnection;
  onSyncCreated?: (syncName: string) => void;
}

/**
 * 소스 탐색 탭 (공식 db-explorer 구조): 상단 정보 배너 + 3-pane
 * (좌 소스 미리보기 트리 / 중앙 프리뷰 탭 + 접이식 그래프 뷰 /
 * 우 동기화할 테이블 패널 + 일괄 sync 생성). sources.exploration.run 사용.
 */
export function SourceExploreTab({
  source,
  onSyncCreated,
}: SourceExploreTabProps) {
  const client = useFoundryLiteClient();
  const [exploreType, setExploreType] = useState<ExploreType>(
    source.kind === "rest" || source.kind === "rest_api"
      ? "rest_api"
      : "postgres_jdbc",
  );
  const [databaseUrlSecretRef, setDatabaseUrlSecretRef] = useState(
    () => readTextField(source.configSummary, "databaseUrlSecretRef") ?? "",
  );
  const [sampleLimitText, setSampleLimitText] = useState("20");
  const [baseUrl, setBaseUrl] = useState("");
  const [resourcePath, setResourcePath] = useState("");

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
  const restPreviewMutation = useFoundryLiteMutation<
    SourceExploreResult,
    SourceExploreRequest
  >((payload) => client.sources.exploration.run(payload));

  const sampleLimit = Number.parseInt(sampleLimitText, 10) || 20;
  const canExplore =
    exploreType === "postgres_jdbc"
      ? databaseUrlSecretRef.trim().length > 0
      : baseUrl.trim().length > 0 && resourcePath.trim().length > 0;

  const handleExplore = async () => {
    setOpenTables([]);
    setActiveTable(null);
    setPreviewsByTable({});
    setSelectedTables([]);
    if (exploreType === "postgres_jdbc") {
      await treeMutation.execute({
        sourceName: source.sourceName,
        sourceType: "postgres_jdbc",
        request: {
          databaseUrlSecretRef: databaseUrlSecretRef.trim(),
          sampleLimit,
        },
      });
      return;
    }
    await restPreviewMutation.execute({
      sourceName: source.sourceName,
      sourceType: "rest_api",
      request: {
        baseUrl: baseUrl.trim(),
        resourcePath: resourcePath.trim(),
        resourceName: "preview",
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
            errorMessage: result.error ? JSON.stringify(result.error) : null,
          },
        }));
      } catch (caught) {
        const normalized = normalizeFoundryLiteError(caught);
        setPreviewsByTable((current) => ({
          ...current,
          [tableName]: {
            status: "failed",
            rows: [],
            columns: [],
            explorationRunId: null,
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
  const hasTree =
    exploreType === "postgres_jdbc" && treeMutation.result !== null;

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
        exploreType={exploreType}
        onExploreTypeChange={(type) => {
          setExploreType(type);
          setActiveTable(null);
        }}
        databaseUrlSecretRef={databaseUrlSecretRef}
        onDatabaseUrlSecretRefChange={setDatabaseUrlSecretRef}
        sampleLimitText={sampleLimitText}
        onSampleLimitChange={setSampleLimitText}
        baseUrl={baseUrl}
        onBaseUrlChange={setBaseUrl}
        resourcePath={resourcePath}
        onResourcePathChange={setResourcePath}
        canExplore={canExplore}
        isRunning={treeMutation.isRunning}
        onExplore={() => void handleExplore()}
      />
      {treeMutation.error ? <ErrorState error={treeMutation.error} /> : null}
      {hasTree ? (
        <div className="flex h-[calc(100vh-260px)] min-h-[520px] overflow-hidden rounded border bg-card">
          <ExploreSourcePanel
            tables={tables}
            explorationRunId={treeMutation.result?.explorationRunId ?? null}
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
      {exploreType === "rest_api" &&
      (restPreviewMutation.result !== null ||
        restPreviewMutation.isRunning ||
        restPreviewMutation.error) ? (
        <div className="space-y-4">
          <RestPreviewPanel
            title={`${baseUrl}${resourcePath}`}
            isRunning={restPreviewMutation.isRunning}
            error={restPreviewMutation.error}
            result={restPreviewMutation.result}
          />
          <p className="text-[11px] text-muted-foreground">
            REST 소스의 managed sync는 커넥터 연결이 필요합니다 — 새 소스
            위저드의 REST API 타입에서 생성하세요.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function ExploreConfigCard({
  exploreType,
  onExploreTypeChange,
  databaseUrlSecretRef,
  onDatabaseUrlSecretRefChange,
  sampleLimitText,
  onSampleLimitChange,
  baseUrl,
  onBaseUrlChange,
  resourcePath,
  onResourcePathChange,
  canExplore,
  isRunning,
  onExplore,
}: {
  exploreType: ExploreType;
  onExploreTypeChange: (type: ExploreType) => void;
  databaseUrlSecretRef: string;
  onDatabaseUrlSecretRefChange: (value: string) => void;
  sampleLimitText: string;
  onSampleLimitChange: (value: string) => void;
  baseUrl: string;
  onBaseUrlChange: (value: string) => void;
  resourcePath: string;
  onResourcePathChange: (value: string) => void;
  canExplore: boolean;
  isRunning: boolean;
  onExplore: () => void;
}) {
  return (
    <div className="rounded border bg-card">
      <div className="section-label border-b px-3 py-2">탐색 설정</div>
      <div className="flex flex-wrap items-end gap-3 p-3">
        <WizardField label="탐색 타입" className="w-48">
          <Select
            value={exploreType}
            onValueChange={(value) => onExploreTypeChange(value as ExploreType)}
          >
            <SelectTrigger size="sm" className="w-full text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="postgres_jdbc">
                데이터베이스 (Postgres)
              </SelectItem>
              <SelectItem value="rest_api">REST API</SelectItem>
            </SelectContent>
          </Select>
        </WizardField>
        {exploreType === "postgres_jdbc" ? (
          <>
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
          </>
        ) : (
          <>
            <WizardField label="Base URL" className="w-64">
              <Input
                value={baseUrl}
                onChange={(event) => onBaseUrlChange(event.target.value)}
                placeholder="https://api.example.com"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
            <WizardField label="리소스 경로" className="w-48">
              <Input
                value={resourcePath}
                onChange={(event) => onResourcePathChange(event.target.value)}
                placeholder="/orders"
                className="h-8 font-mono text-xs"
              />
            </WizardField>
          </>
        )}
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
          {exploreType === "postgres_jdbc" ? "리소스 탐색" : "프리뷰 실행"}
        </Button>
      </div>
    </div>
  );
}

function RestPreviewPanel({
  title,
  isRunning,
  error,
  result,
}: {
  title: string;
  isRunning: boolean;
  error: unknown;
  result: SourceExploreResult | null;
}) {
  if (isRunning) {
    return (
      <div className="flex items-center gap-2 rounded border bg-card p-4 text-xs text-muted-foreground">
        <Loader2 className="size-3.5 animate-spin" /> {title} 프리뷰 실행 중…
      </div>
    );
  }
  if (error) return <ErrorState error={error} />;
  if (!result) return null;
  const rows = readExploreSampleRows(result.resultSummary);
  const columns = readExploreSchemaColumns(result.resultSummary);
  const columnNames =
    columns.length > 0
      ? columns.map((column) => column.name)
      : rows.length > 0
        ? Object.keys(rows[0])
        : [];

  return (
    <div className="rounded border bg-card">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <span className="section-label">프리뷰</span>
        <span className="font-mono text-[11px]">{title}</span>
        <StatusPill
          intent={result.status === "succeeded" ? "success" : "neutral"}
        >
          {result.status}
        </StatusPill>
        <span className="ml-auto font-mono text-[10px] text-muted-foreground">
          run={result.explorationRunId}
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="px-3 py-4 text-xs text-muted-foreground">
          샘플 행이 없습니다.
        </div>
      ) : (
        <div className="max-h-72 overflow-auto">
          <table className="w-full text-left">
            <thead>
              <tr className="border-b bg-muted/40">
                {columnNames.map((name) => (
                  <th
                    key={name}
                    className="px-2 py-1.5 font-mono text-[10px] font-medium whitespace-nowrap text-muted-foreground"
                  >
                    {name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, index) => (
                <tr key={index} className="h-8 border-b last:border-0">
                  {columnNames.map((name) => (
                    <td
                      key={name}
                      className="max-w-48 truncate px-2 py-1 font-mono text-[11px] whitespace-nowrap"
                    >
                      {formatSampleCell(row[name])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="border-t px-3 py-1.5 text-[11px] text-muted-foreground">
        샘플 {rows.length}행 · 컬럼 {columnNames.length}개
      </div>
    </div>
  );
}
