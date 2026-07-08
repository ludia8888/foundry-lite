import type { PipelineNode, PipelineSchemaColumn } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

import {
  NODE_TYPE_META,
  asText,
  nodeDataOf,
  type PipelineValidationIssue,
} from "../pipeline-model";
import { useSafeQuery } from "../use-safe-query";

interface NodeInspectorProps {
  branchId: string | null;
  node: PipelineNode;
  issues: readonly PipelineValidationIssue[];
  isGraphDirty: boolean;
  onUpdateNodeData: (nodeId: string, patch: Record<string, unknown>) => void;
  onClose: () => void;
}

/** 노드 선택 시 우측 인스펙터: 설정 편집 + 노드 미리보기 + 검증 결과 연결. */
export function NodeInspector({
  branchId,
  node,
  issues,
  isGraphDirty,
  onUpdateNodeData,
  onClose,
}: NodeInspectorProps) {
  const meta = NODE_TYPE_META[node.type];

  return (
    <aside className="flex w-80 shrink-0 flex-col overflow-y-auto border-l bg-card">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b px-3">
        <StatusPill intent="info">{meta.shortLabel}</StatusPill>
        <span className="truncate font-mono text-[11px] text-muted-foreground">
          {node.id}
        </span>
        <Button
          variant="ghost"
          size="sm"
          className="ml-auto size-6 p-0"
          onClick={onClose}
        >
          <X className="size-3.5" />
        </Button>
      </div>

      <NodeConfigForm node={node} onUpdateNodeData={onUpdateNodeData} />
      <NodeSchemaSection schema={node.schema ?? []} />
      <NodePreviewSection
        branchId={branchId}
        nodeId={node.id}
        isGraphDirty={isGraphDirty}
      />
      <NodeIssuesSection issues={issues} />
    </aside>
  );
}

function NodeConfigForm({
  node,
  onUpdateNodeData,
}: {
  node: PipelineNode;
  onUpdateNodeData: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const data = nodeDataOf(node);
  const [draft, setDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    setDraft({
      label: asText(data.label) ?? "",
      outputDatasetRef: asText(data.outputDatasetRef) ?? "",
      sql: asText(data.sql) ?? "",
      leftKey: asText(data.leftKey) ?? "",
      rightKey: asText(data.rightKey) ?? "",
      joinType: asText(data.joinType) ?? "inner",
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [node.id]);

  const updateDraft = (key: string, value: string) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const handleApply = () => {
    const patch: Record<string, unknown> = { label: draft.label };
    if (node.type !== "dataset")
      patch.outputDatasetRef = draft.outputDatasetRef;
    if (node.type === "sql") patch.sql = draft.sql;
    if (node.type === "join") {
      patch.leftKey = draft.leftKey;
      patch.rightKey = draft.rightKey;
      patch.joinType = draft.joinType;
    }
    onUpdateNodeData(node.id, patch);
  };

  return (
    <div className="space-y-2 border-b p-3">
      <div className="section-label">노드 설정</div>
      <Field label="표시 이름">
        <Input
          className="h-7 text-[12px]"
          value={draft.label ?? ""}
          onChange={(event) => updateDraft("label", event.target.value)}
        />
      </Field>
      {node.type === "dataset" ? (
        <Field label="입력 데이터셋">
          <div className="rounded border bg-muted/40 px-2 py-1 font-mono text-[11px]">
            {asText(data.datasetRef) ?? "-"}
          </div>
        </Field>
      ) : (
        <Field label="출력 데이터셋 ref">
          <Input
            className="h-7 font-mono text-[11px]"
            value={draft.outputDatasetRef ?? ""}
            onChange={(event) =>
              updateDraft("outputDatasetRef", event.target.value)
            }
          />
        </Field>
      )}
      {node.type === "sql" ? (
        <Field label="SQL">
          <Textarea
            className="min-h-24 font-mono text-[11px]"
            value={draft.sql ?? ""}
            onChange={(event) => updateDraft("sql", event.target.value)}
          />
        </Field>
      ) : null}
      {node.type === "join" ? (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <Field label="좌측 키">
              <Input
                className="h-7 font-mono text-[11px]"
                value={draft.leftKey ?? ""}
                onChange={(event) => updateDraft("leftKey", event.target.value)}
              />
            </Field>
            <Field label="우측 키">
              <Input
                className="h-7 font-mono text-[11px]"
                value={draft.rightKey ?? ""}
                onChange={(event) =>
                  updateDraft("rightKey", event.target.value)
                }
              />
            </Field>
          </div>
          <Field label="조인 방식">
            <Select
              value={draft.joinType ?? "inner"}
              onValueChange={(value) => updateDraft("joinType", value)}
            >
              <SelectTrigger className="h-7 w-full text-[12px]">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="inner" className="text-[12px]">
                  inner
                </SelectItem>
                <SelectItem value="left" className="text-[12px]">
                  left
                </SelectItem>
                <SelectItem value="full" className="text-[12px]">
                  full
                </SelectItem>
              </SelectContent>
            </Select>
          </Field>
          <p className="text-[11px] text-muted-foreground">
            첫 번째로 연결한 입력이 좌측, 두 번째가 우측으로 처리됩니다.
          </p>
        </div>
      ) : null}
      <Button
        size="sm"
        className="h-7 w-full text-[12px]"
        onClick={handleApply}
      >
        설정 적용
      </Button>
    </div>
  );
}

function NodeSchemaSection({
  schema,
}: {
  schema: readonly PipelineSchemaColumn[];
}) {
  return (
    <div className="space-y-1.5 border-b p-3">
      <div className="section-label">스키마 · 컬럼 {schema.length}개</div>
      {schema.length === 0 ? (
        <p className="text-[11px] text-muted-foreground">
          입력을 연결하고 저장하면 스키마가 파생됩니다.
        </p>
      ) : (
        <div className="max-h-40 space-y-0.5 overflow-y-auto">
          {schema.map((column) => (
            <div
              key={column.name}
              className="flex items-center justify-between font-mono text-[11px]"
            >
              <span className="truncate">{column.name}</span>
              <span className="text-muted-foreground">{column.type}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function NodePreviewSection({
  branchId,
  nodeId,
  isGraphDirty,
}: {
  branchId: string | null;
  nodeId: string;
  isGraphDirty: boolean;
}) {
  const client = useFoundryLiteClient();
  // useFoundryLitePipelinePreview와 동일 조합 (previewNode + stats + suggestCasts 병렬).
  const loadPreview = useCallback(async () => {
    if (!branchId) return { preview: null, stats: null, casts: null };
    const [preview, stats, casts] = await Promise.all([
      client.pipelines.graph.previewNode(branchId, nodeId, { limit: 20 }),
      client.pipelines.graph.stats(branchId, nodeId, { limit: 20 }),
      client.pipelines.graph.suggestCasts(branchId, nodeId),
    ]);
    return { preview, stats, casts };
  }, [branchId, client, nodeId]);
  const preview = useSafeQuery(
    ["pipelines", "node-preview", branchId, nodeId],
    loadPreview,
    { enabled: Boolean(branchId) && !isGraphDirty },
  );

  const stats = preview.data?.stats ?? null;
  const casts = preview.data?.casts ?? null;
  const suggestionCount = Array.isArray(casts?.suggestions)
    ? casts.suggestions.length
    : 0;

  return (
    <div className="space-y-1.5 border-b p-3">
      <div className="flex items-center gap-2">
        <div className="section-label">노드 미리보기</div>
        {preview.requestId ? (
          <span className="ml-auto truncate font-mono text-[10px] text-muted-foreground">
            req={preview.requestId}
          </span>
        ) : null}
      </div>
      {isGraphDirty ? (
        <p className="text-[11px] text-warning">
          저장되지 않은 변경이 있습니다. 저장하면 미리보기가 갱신됩니다.
        </p>
      ) : preview.isLoading ? (
        <p className="text-[11px] text-muted-foreground">
          미리보기 불러오는 중...
        </p>
      ) : preview.error ? (
        <p className="text-[11px] text-destructive">
          미리보기 실패 · {preview.error.code}
        </p>
      ) : (
        <div className="space-y-1 text-[11px]">
          <div className="flex justify-between">
            <span className="text-muted-foreground">컬럼 수</span>
            <span className="font-mono">
              {String(stats?.columnCount ?? "-")}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">행 수</span>
            <span className="font-mono">
              {stats?.rowCount === null || stats?.rowCount === undefined
                ? "실행 전 미산출"
                : String(stats.rowCount)}
            </span>
          </div>
          <div className="flex justify-between">
            <span className="text-muted-foreground">캐스트 제안</span>
            <span className="font-mono">{suggestionCount}건</span>
          </div>
        </div>
      )}
    </div>
  );
}

function NodeIssuesSection({
  issues,
}: {
  issues: readonly PipelineValidationIssue[];
}) {
  return (
    <div className="space-y-1.5 p-3">
      <div className="section-label">이 노드의 검증 결과</div>
      {issues.length === 0 ? (
        <StatusPill intent="success">발견된 오류 없음</StatusPill>
      ) : (
        <div className="space-y-1">
          {issues.map((issue, index) => (
            <div
              key={`${issue.code}-${index}`}
              className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1"
            >
              <div className="font-mono text-[11px] text-destructive">
                {issue.code}
              </div>
              <div className="break-all font-mono text-[10px] text-muted-foreground">
                {JSON.stringify({ ...issue, code: undefined })}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px] text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}
