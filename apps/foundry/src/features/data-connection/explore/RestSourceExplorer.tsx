import type {
  ConnectorResource,
  ConnectorResourceTestResult,
  SourceConnection,
  SourceExploreResult,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { CheckCircle2, ChevronRight } from "lucide-react";
import { useCallback, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";

import { readTextField } from "../source-model";
import { RestResourceListPane } from "./RestResourceListPane";
import { RestResourcePreviewPane } from "./RestResourcePreviewPane";
import { RestResourceSelectionPane } from "./RestResourceSelectionPane";

interface RestSourceExplorerProps {
  source: SourceConnection;
  onCreateSync?: (resourceName: string) => void;
}

interface ResourceTestPayload {
  connectorName: string;
  resourceName: string;
}

/**
 * REST Source 탐색기:
 * 등록된 Connector 리소스 -> 읽기 전용 실제 미리보기 -> 동기화 생성 진입을
 * 한 화면에서 연결한다. REST에는 관계 그래프가 없으므로 리소스/미리보기/선택
 * 3-pane만 사용한다.
 */
export function RestSourceExplorer({
  source,
  onCreateSync,
}: RestSourceExplorerProps) {
  const client = useFoundryLiteClient();
  const connectorName =
    readTextField(source.configSummary, "connectorName") ?? source.sourceName;
  const configuredResourceName =
    readTextField(source.configSummary, "resourceName") ?? "";
  const [searchText, setSearchText] = useState("");
  const [requestedResourceName, setRequestedResourceName] = useState(
    configuredResourceName,
  );
  const [previewResult, setPreviewResult] =
    useState<ConnectorResourceTestResult | null>(null);
  const [previewResourceName, setPreviewResourceName] = useState<string | null>(
    null,
  );

  const connectionQuery = useFoundryLiteQuery(
    ["data-connection", "rest-explorer", connectorName],
    () => client.connectors.connections.get(connectorName),
    { enabled: connectorName.length > 0 },
  );
  const testResource = useFoundryLiteMutation<
    SourceExploreResult,
    ResourceTestPayload
  >(
    useCallback(
      (payload: ResourceTestPayload) =>
        client.sources.exploration.run({
          sourceName: source.sourceName,
          sourceType: source.kind === "rest" ? "rest_api" : source.kind,
          request: {
            connectorName: payload.connectorName,
            resourceName: payload.resourceName,
          },
        }),
      [client, source.kind, source.sourceName],
    ),
    {
      lockKey: (payload) =>
        `sources:explore:${payload.connectorName}:${payload.resourceName}`,
    },
  );

  const resources = connectionQuery.data?.resources ?? [];
  const selectedResource =
    resources.find(
      (resource) => resource.resourceName === requestedResourceName,
    ) ??
    resources[0] ??
    null;
  const selectedResourceName = selectedResource?.resourceName ?? "";
  const filteredResources = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    if (!query) return resources;
    return resources.filter((resource) =>
      [resource.resourceName, resource.resourcePath, resource.datasetRef].some(
        (value) => value.toLowerCase().includes(query),
      ),
    );
  }, [resources, searchText]);
  const visiblePreview =
    previewResult?.resourceName === selectedResourceName ? previewResult : null;
  const visibleError =
    previewResourceName === selectedResourceName ? testResource.error : null;
  const visibleExploration =
    previewResourceName === selectedResourceName ? testResource.result : null;

  const handlePreview = async () => {
    if (!selectedResource) return;
    setPreviewResourceName(selectedResource.resourceName);
    setPreviewResult(null);
    const result = await testResource.execute({
      connectorName,
      resourceName: selectedResource.resourceName,
    });
    if (result) {
      setPreviewResult(
        connectorPreviewResult(
          result,
          selectedResource,
          connectionQuery.data?.configFingerprint ?? "",
        ),
      );
    }
  };

  if (connectionQuery.isLoading && !connectionQuery.data) {
    return <LoadingState rowCount={8} />;
  }
  if (connectionQuery.error) {
    return (
      <ErrorState
        error={connectionQuery.error}
        onRetry={() => void connectionQuery.reload()}
      />
    );
  }

  return (
    <div className="space-y-3" data-testid="rest-source-explorer">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded border border-primary/30 bg-primary/10 px-3 py-2">
        <div className="flex min-w-0 items-start gap-2 text-xs">
          <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-success" />
          <p>
            등록된 원격 리소스를 읽기 전용으로 확인합니다. 미리보기는 외부
            데이터를 Dataset에 저장하지 않습니다.
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-1 font-mono text-[10px] text-muted-foreground">
          <span>{connectorName}</span>
          <ChevronRight className="size-3" />
          <span>{resources.length} resources</span>
        </div>
      </div>

      <div className="flex h-[calc(100vh-230px)] min-h-[540px] overflow-hidden rounded border bg-card">
        <RestResourceListPane
          resources={filteredResources}
          resourceCount={resources.length}
          searchText={searchText}
          selectedResourceName={selectedResourceName}
          onSearchTextChange={setSearchText}
          onSelectResource={setRequestedResourceName}
          onReload={() => void connectionQuery.reload()}
        />
        <RestResourcePreviewPane
          resource={selectedResource}
          result={visiblePreview}
          error={visibleError}
          isRunning={
            testResource.isRunning &&
            previewResourceName === selectedResourceName
          }
          requestId={testResource.requestId}
          explorationResult={
            visibleExploration
              ? {
                  explorationRunId: visibleExploration.explorationRunId,
                  operationsPath: visibleExploration.operationsPath,
                }
              : null
          }
          onPreview={() => void handlePreview()}
        />
        <RestResourceSelectionPane
          resource={selectedResource}
          previewResult={visiblePreview}
          onCreateSync={onCreateSync}
        />
      </div>
    </div>
  );
}

function connectorPreviewResult(
  exploration: SourceExploreResult,
  resource: ConnectorResource,
  fallbackFingerprint: string,
): ConnectorResourceTestResult {
  const summary = exploration.resultSummary;
  return {
    status: exploration.status,
    connectorName: readSummaryText(summary, "connectorName"),
    resourceName: readSummaryText(summary, "resourceName"),
    datasetRef: readSummaryText(summary, "datasetRef") || resource.datasetRef,
    configFingerprint:
      readSummaryText(summary, "configFingerprint") || fallbackFingerprint,
    rowCount: readSummaryNumber(summary, "rowCount"),
    sampleRows: readSummaryRows(summary, "sampleRows"),
    schema: readSummaryRecord(summary, "schema"),
    cursor: readSummaryRecord(summary, "cursor"),
    error: exploration.error ?? {},
    networkEvidence: readSummaryRecord(summary, "networkEvidence"),
  };
}

function readSummaryText(value: Record<string, unknown>, key: string): string {
  const item = value[key];
  return typeof item === "string" ? item : "";
}

function readSummaryNumber(value: Record<string, unknown>, key: string): number {
  const item = value[key];
  return typeof item === "number" ? item : 0;
}

function readSummaryRecord(
  value: Record<string, unknown>,
  key: string,
): Record<string, unknown> {
  const item = value[key];
  return item && typeof item === "object" && !Array.isArray(item)
    ? (item as Record<string, unknown>)
    : {};
}

function readSummaryRows(
  value: Record<string, unknown>,
  key: string,
): Array<Record<string, unknown>> {
  const item = value[key];
  if (!Array.isArray(item)) return [];
  return item.filter(
    (row): row is Record<string, unknown> =>
      row !== null && typeof row === "object" && !Array.isArray(row),
  );
}
