import type { PipelineSchemaColumn } from "@foundry-lite/sdk";
import { FlaskConical, LockKeyhole, X } from "lucide-react";
import { useEffect, useState } from "react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

import {
  NODE_TYPE_META,
  asText,
  nodeDataOf,
  nodeInputPorts,
  nodeOutputPorts,
  normalizeJoinType,
  selectCastColumnsOf,
  type PipelineCanvasNode,
  type SelectCastColumn,
  type PipelineValidationIssue,
} from "../pipeline-model";
import { NodeOperationEditor } from "./NodeOperationEditor";

interface NodeInspectorProps {
  node: PipelineCanvasNode;
  issues: readonly PipelineValidationIssue[];
  onUpdateNodeData: (nodeId: string, patch: Record<string, unknown>) => void;
  onClose: () => void;
}

/** 노드 선택 시 우측 인스펙터: 설정 편집 + 노드 미리보기 + 검증 결과 연결. */
export function NodeInspector({
  node,
  issues,
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
          aria-label="노드 인스펙터 닫기"
          onClick={onClose}
        >
          <X className="size-3.5" />
        </Button>
      </div>

      {node.isReadOnly ? (
        <ReadOnlyNodeContract node={node} />
      ) : (
        <NodeConfigForm node={node} onUpdateNodeData={onUpdateNodeData} />
      )}
      <NodeSchemaSection schema={node.schema ?? []} />
      {node.isReadOnly ? null : <NodePreviewSection nodeId={node.id} />}
      <NodeIssuesSection issues={issues} />
    </aside>
  );
}

function ReadOnlyNodeContract({ node }: { node: PipelineCanvasNode }) {
  return (
    <div className="space-y-3 border-b p-3">
      <div className="flex items-center gap-2 border border-[#E2C98B] bg-[#FFF8E7] p-2.5 text-[#725B20]">
        <LockKeyhole className="size-4 shrink-0" />
        <div>
          <div className="text-[11px] font-semibold">
            읽기 전용 Graph v2 노드
          </div>
          <p className="mt-0.5 text-[10px] leading-4">
            현재 UI 편집기가 이 descriptor를 이해하지 못해도 노드·설정·named
            port 연결은 저장 시 그대로 보존됩니다.
          </p>
        </div>
      </div>
      <dl className="space-y-1.5 font-mono text-[10px]">
        <ReadOnlyField label="descriptor" value={node.descriptorId} />
        <ReadOnlyField label="spec version" value={String(node.specVersion)} />
        <ReadOnlyField
          label="reason"
          value={readOnlyReasonLabel(node.readOnlyReason)}
        />
        <ReadOnlyField
          label="input ports"
          value={nodeInputPorts(node).join(", ") || "-"}
        />
        <ReadOnlyField
          label="output ports"
          value={nodeOutputPorts(node).join(", ") || "-"}
        />
      </dl>
      <pre className="max-h-52 overflow-auto border border-[#C5CBD3] bg-[#17212B] p-2 font-mono text-[9px] leading-4 text-[#D7E0EA]">
        {JSON.stringify(redactOpaqueConfig(node.config ?? {}), null, 2)}
      </pre>
    </div>
  );
}

function readOnlyReasonLabel(
  reason: PipelineCanvasNode["readOnlyReason"],
): string {
  const labels = {
    unknown_descriptor: "UI editor binding 없음",
    unsupported_spec_version: "UI가 이 spec version을 지원하지 않음",
    descriptor_contract_missing: "서버 descriptor 계약을 찾지 못함",
    descriptor_kind_mismatch: "서버 descriptor kind 불일치",
  };
  return reason ? labels[reason] : "호환 가능한 편집기 없음";
}

function redactOpaqueConfig(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactOpaqueConfig);
  if (typeof value !== "object" || value === null) return value;
  return Object.fromEntries(
    Object.entries(value as Record<string, unknown>).map(([key, item]) => [
      key,
      /(password|secret|token|api[-_]?key|credential)/i.test(key)
        ? "[REDACTED]"
        : redactOpaqueConfig(item),
    ]),
  );
}

function ReadOnlyField({ label, value }: { label: string; value: string }) {
  return (
    <div className="grid grid-cols-[84px_minmax(0,1fr)] gap-2">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-all">{value}</dd>
    </div>
  );
}

function NodeConfigForm({
  node,
  onUpdateNodeData,
}: {
  node: PipelineCanvasNode;
  onUpdateNodeData: (nodeId: string, patch: Record<string, unknown>) => void;
}) {
  const data = nodeDataOf(node);
  const editorStateSignature = JSON.stringify({
    draft: {
      label: asText(data.label) ?? "",
      outputDatasetRef: asText(data.outputDatasetRef) ?? "",
      sql: asText(data.sql) ?? "",
      leftKey: asText(data.leftKey) ?? "",
      rightKey: asText(data.rightKey) ?? "",
      joinType: normalizeJoinType(data.joinType),
      sourceCode: asText(data.sourceCode) ?? "",
      functionName: asText(data.functionName) ?? "transform",
      mediaSetRef: asText(data.mediaSetRef) ?? "",
      mediaItemVersionIds: Array.isArray(data.mediaItemVersionIds)
        ? data.mediaItemVersionIds.join(", ")
        : "",
      chunkSize: String(data.chunkSize ?? "500"),
      overlap: String(data.overlap ?? "50"),
      modelRef: asText(data.modelRef) ?? "",
      indexRef: asText(data.indexRef) ?? "",
      virtualTableRef: asText(data.virtualTableRef) ?? "",
      mappingRef: asText(data.mappingRef) ?? "",
    },
    selectCastColumns: selectCastColumnsOf(node),
  });
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [selectCastColumns, setSelectCastColumns] = useState<
    SelectCastColumn[]
  >([]);

  useEffect(() => {
    const next = JSON.parse(editorStateSignature) as {
      draft: Record<string, string>;
      selectCastColumns: SelectCastColumn[];
    };
    setDraft(next.draft);
    setSelectCastColumns(next.selectCastColumns);
  }, [editorStateSignature]);

  const updateDraft = (key: string, value: string) =>
    setDraft((prev) => ({ ...prev, [key]: value }));

  const handleApply = () => {
    const patch: Record<string, unknown> = { label: draft.label };
    if (hasOutputDatasetRef(node))
      patch.outputDatasetRef = draft.outputDatasetRef;
    if (node.type === "sql") patch.sql = draft.sql;
    if (node.type === "python") {
      patch.sourceCode = draft.sourceCode;
      patch.functionName = draft.functionName;
    }
    if (node.type === "join") {
      patch.leftKey = draft.leftKey;
      patch.rightKey = draft.rightKey;
      patch.joinType = normalizeJoinType(draft.joinType);
    }
    if (node.type === "select_cast") patch.columns = selectCastColumns;
    if (node.type === "source_media_set") {
      patch.mediaSetRef = draft.mediaSetRef;
      patch.mediaItemVersionIds = csvValues(draft.mediaItemVersionIds);
    }
    if (node.type === "output_media_set") patch.mediaSetRef = draft.mediaSetRef;
    if (node.type === "output_virtual_table")
      patch.virtualTableRef = draft.virtualTableRef;
    if (node.type === "chunk") {
      patch.chunkSize = Number(draft.chunkSize);
      patch.overlap = Number(draft.overlap);
    }
    if (node.type === "embedding_text") patch.modelRef = draft.modelRef;
    if (node.type === "output_semantic_index") patch.indexRef = draft.indexRef;
    if (node.type === "output_ontology") patch.mappingRef = draft.mappingRef;
    onUpdateNodeData(node.id, patch);
  };

  const isOutputRefValid =
    !hasOutputDatasetRef(node) || isDatasetRef(draft.outputDatasetRef ?? "");
  const isOperationValid = operationDraftIsValid(
    node,
    draft,
    selectCastColumns,
  );
  const isApplyEnabled = isOutputRefValid && isOperationValid;

  return (
    <div className="space-y-2 border-b p-3">
      <div className="section-label">노드 설정</div>
      <Field label="표시 이름">
        <Input
          aria-label="노드 표시 이름"
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
      ) : hasOutputDatasetRef(node) ? (
        <Field label="출력 데이터셋 ref">
          <Input
            aria-label="출력 데이터셋 ref"
            className="h-7 font-mono text-[11px]"
            value={draft.outputDatasetRef ?? ""}
            onChange={(event) =>
              updateDraft("outputDatasetRef", event.target.value)
            }
          />
        </Field>
      ) : null}
      <NodeOperationEditor
        node={node}
        draft={draft}
        selectCastColumns={selectCastColumns}
        onUpdateDraft={updateDraft}
        onChangeSelectCastColumns={setSelectCastColumns}
      />
      {!isOutputRefValid ? (
        <p className="text-[11px] text-destructive">
          출력 ref는 namespace.name 형식의 안전한 식별자여야 합니다.
        </p>
      ) : null}
      <Button
        size="sm"
        className="h-7 w-full text-[12px]"
        disabled={!isApplyEnabled}
        title={
          isApplyEnabled
            ? "노드 실행 설정 적용"
            : "필수 실행 설정을 모두 입력하세요"
        }
        onClick={handleApply}
      >
        설정 적용
      </Button>
    </div>
  );
}

function isDatasetRef(value: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$/.test(
    value.trim(),
  );
}

function operationDraftIsValid(
  node: PipelineCanvasNode,
  draft: Record<string, string>,
  selectCastColumns: readonly SelectCastColumn[],
): boolean {
  if (node.type === "sql") return Boolean(draft.sql?.trim());
  if (node.type === "python") {
    return Boolean(
      draft.sourceCode?.trim() && isSafeIdentifier(draft.functionName ?? ""),
    );
  }
  if (node.type === "join") {
    return (
      isSafeIdentifier(draft.leftKey ?? "") &&
      isSafeIdentifier(draft.rightKey ?? "") &&
      ["inner", "left", "right", "full outer"].includes(
        normalizeJoinType(draft.joinType),
      )
    );
  }
  if (node.type === "source_media_set") {
    return Boolean(
      draft.mediaSetRef?.trim() &&
        csvValues(draft.mediaItemVersionIds).length > 0,
    );
  }
  if (node.type === "output_media_set")
    return isDatasetRef(draft.mediaSetRef ?? "");
  if (node.type === "output_virtual_table")
    return isDatasetRef(draft.virtualTableRef ?? "");
  if (node.type === "chunk") {
    const size = Number(draft.chunkSize);
    const overlap = Number(draft.overlap);
    return (
      Number.isInteger(size) &&
      size > 0 &&
      Number.isInteger(overlap) &&
      overlap >= 0 &&
      overlap < size
    );
  }
  if (node.type === "embedding_text") return Boolean(draft.modelRef?.trim());
  if (node.type === "output_semantic_index")
    return isDatasetRef(draft.indexRef ?? "");
  if (node.type === "output_ontology")
    return Boolean(draft.mappingRef?.trim());
  if (node.type !== "select_cast") return true;
  return (
    selectCastColumns.length > 0 &&
    selectCastColumns.every(
      (column) =>
        isSafeIdentifier(column.source) &&
        isSafeIdentifier(column.name) &&
        isSafeCastType(column.type),
    )
  );
}

function hasOutputDatasetRef(node: PipelineCanvasNode): boolean {
  return [
    "sql",
    "python",
    "join",
    "union",
    "select_cast",
    "output_dataset",
  ].includes(node.type);
}

function csvValues(value: string | undefined): string[] {
  return (value ?? "")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function isSafeIdentifier(value: string): boolean {
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(value.trim());
}

function isSafeCastType(value: string): boolean {
  return /^[A-Za-z][A-Za-z0-9_]*(\([0-9]+(,[0-9]+)?\))?$/.test(
    value.trim(),
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

function NodePreviewSection({ nodeId }: { nodeId: string }) {
  return (
    <div className="space-y-2 border-b bg-[#FFFDF6] p-3">
      <div className="flex items-center gap-1.5">
        <FlaskConical className="size-3.5 text-[#9B6D14]" />
        <div className="section-label">실제 no-commit 미리보기</div>
      </div>
      <p className="text-[11px] leading-5 text-muted-foreground">
        하단 <strong className="text-foreground">실제 데이터 미리보기</strong>에서
        현재 저장되지 않은 draft graph를 이 노드까지 실행합니다. 중간 artifact와
        output version은 생성되지 않습니다.
      </p>
      <span className="font-mono text-[10px] text-muted-foreground">
        targetNodeId={nodeId}
      </span>
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
