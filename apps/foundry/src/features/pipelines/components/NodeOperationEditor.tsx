import { Plus, Trash2 } from "lucide-react";

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

import type {
  PipelineCanvasNode,
  SelectCastColumn,
} from "../pipeline-model";

interface NodeOperationEditorProps {
  node: PipelineCanvasNode;
  draft: Record<string, string>;
  selectCastColumns: readonly SelectCastColumn[];
  onUpdateDraft: (key: string, value: string) => void;
  onChangeSelectCastColumns: (columns: SelectCastColumn[]) => void;
}

export function NodeOperationEditor({
  node,
  draft,
  selectCastColumns,
  onUpdateDraft,
  onChangeSelectCastColumns,
}: NodeOperationEditorProps) {
  if (node.type === "sql") {
    return (
      <Field label="SQL">
        <Textarea
          aria-label="SQL"
          className="min-h-24 font-mono text-[11px]"
          value={draft.sql ?? ""}
          onChange={(event) => onUpdateDraft("sql", event.target.value)}
        />
      </Field>
    );
  }
  if (node.type === "python") {
    return (
      <div className="space-y-2">
        <Field label="함수 이름">
          <Input
            aria-label="Python 함수 이름"
            className="h-7 font-mono text-[11px]"
            value={draft.functionName ?? ""}
            onChange={(event) =>
              onUpdateDraft("functionName", event.target.value)
            }
          />
        </Field>
        <Field label="Python source">
          <Textarea
            aria-label="Python source"
            className="min-h-40 font-mono text-[11px]"
            spellCheck={false}
            value={draft.sourceCode ?? ""}
            onChange={(event) =>
              onUpdateDraft("sourceCode", event.target.value)
            }
          />
        </Field>
        <p className="text-[11px] text-muted-foreground">
          실행 함수는 입력 Dataset handle을 keyword 인자로 받고 행 목록을
          반환합니다. 원본 저장 경로에는 직접 접근하지 않습니다.
        </p>
      </div>
    );
  }
  if (node.type === "join") {
    return (
      <div className="space-y-2">
        <div className="grid grid-cols-2 gap-2">
          <Field label="좌측 키">
            <Input
              aria-label="좌측 조인 키"
              className="h-7 font-mono text-[11px]"
              value={draft.leftKey ?? ""}
              onChange={(event) =>
                onUpdateDraft("leftKey", event.target.value)
              }
            />
          </Field>
          <Field label="우측 키">
            <Input
              aria-label="우측 조인 키"
              className="h-7 font-mono text-[11px]"
              value={draft.rightKey ?? ""}
              onChange={(event) =>
                onUpdateDraft("rightKey", event.target.value)
              }
            />
          </Field>
        </div>
        <Field label="조인 방식">
          <Select
            value={draft.joinType ?? "inner"}
            onValueChange={(value) => onUpdateDraft("joinType", value)}
          >
            <SelectTrigger
              aria-label="조인 방식"
              className="h-7 w-full text-[12px]"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="inner" className="text-[12px]">
                inner
              </SelectItem>
              <SelectItem value="left" className="text-[12px]">
                left
              </SelectItem>
              <SelectItem value="right" className="text-[12px]">
                right
              </SelectItem>
              <SelectItem value="full outer" className="text-[12px]">
                full outer
              </SelectItem>
            </SelectContent>
          </Select>
        </Field>
        <p className="text-[11px] text-muted-foreground">
          연결 순서가 아니라 노드의 좌측·우측 포트가 조인 역할을 결정합니다.
        </p>
      </div>
    );
  }
  if (node.type === "select_cast") {
    return (
      <SelectCastEditor
        node={node}
        columns={selectCastColumns}
        onChange={onChangeSelectCastColumns}
      />
    );
  }
  if (node.type === "union") {
    return (
      <div className="rounded border border-[#C5CBD3] bg-[#F8F9FA] px-2.5 py-2 text-[11px]">
        <div className="font-semibold text-foreground">엄격한 스키마 정렬</div>
        <p className="mt-1 text-muted-foreground">
          현재 실행기는 컬럼 이름·순서·타입이 같은 입력만 UNION ALL로
          결합합니다. 다른 스키마는 먼저 선택/캐스트 노드로 맞추세요.
        </p>
      </div>
    );
  }
  if (node.type === "source_media_set") {
    return (
      <div className="space-y-2">
        <Field label="Media Set ref">
          <Input
            aria-label="Media Set ref"
            className="h-7 font-mono text-[11px]"
            value={draft.mediaSetRef ?? ""}
            onChange={(event) =>
              onUpdateDraft("mediaSetRef", event.target.value)
            }
          />
        </Field>
        <Field label="Committed media version IDs">
          <Textarea
            aria-label="Committed media version IDs"
            className="min-h-20 font-mono text-[11px]"
            placeholder="miv-001, miv-002"
            value={draft.mediaItemVersionIds ?? ""}
            onChange={(event) =>
              onUpdateDraft("mediaItemVersionIds", event.target.value)
            }
          />
        </Field>
        <p className="text-[11px] leading-5 text-muted-foreground">
          미리보기는 committed MediaVersion ID만 읽습니다. Media Set 자체를
          테이블로 평탄화하지 않고 media artifact로 유지합니다.
        </p>
      </div>
    );
  }
  if (node.type === "output_media_set") {
    return (
      <div className="space-y-2">
        <Field label="Output Media Set ref">
          <Input
            aria-label="Output Media Set ref"
            className="h-7 font-mono text-[11px]"
            value={draft.mediaSetRef ?? ""}
            onChange={(event) =>
              onUpdateDraft("mediaSetRef", event.target.value)
            }
          />
        </Field>
        <p className="text-[11px] leading-5 text-muted-foreground">
          build 시 입력 selection 또는 durable derivative bytes를 대상 Media
          Set으로 stage → validate → commit합니다. 동일 run 재시도는 기존
          transaction을 재사용하며 source security envelope와 lineage pin을
          그대로 보존합니다.
        </p>
      </div>
    );
  }
  if (node.type === "output_virtual_table") {
    return (
      <div className="space-y-2">
        <Field label="Virtual Table ref">
          <Input
            aria-label="Virtual Table ref"
            className="h-7 font-mono text-[11px]"
            value={draft.virtualTableRef ?? ""}
            onChange={(event) =>
              onUpdateDraft("virtualTableRef", event.target.value)
            }
          />
        </Field>
        <p className="text-[11px] leading-5 text-muted-foreground">
          현재는 Graph v2 authoring, contract validation, no-commit preview만
          지원합니다. 외부 원본을 serving Virtual Table로 승격하는 runtime은
          아직 활성화되지 않았습니다.
        </p>
      </div>
    );
  }
  if (node.type === "chunk") {
    return (
      <div className="grid grid-cols-2 gap-2">
        <Field label="Chunk size">
          <Input
            aria-label="Chunk size"
            type="number"
            min={1}
            className="h-7 font-mono text-[11px]"
            value={draft.chunkSize ?? ""}
            onChange={(event) =>
              onUpdateDraft("chunkSize", event.target.value)
            }
          />
        </Field>
        <Field label="Overlap">
          <Input
            aria-label="Chunk overlap"
            type="number"
            min={0}
            className="h-7 font-mono text-[11px]"
            value={draft.overlap ?? ""}
            onChange={(event) =>
              onUpdateDraft("overlap", event.target.value)
            }
          />
        </Field>
      </div>
    );
  }
  if (node.type === "embedding_text") {
    return (
      <Field label="Pinned embedding model">
        <Input
          aria-label="Pinned embedding model"
          className="h-7 font-mono text-[11px]"
          value={draft.modelRef ?? ""}
          onChange={(event) => onUpdateDraft("modelRef", event.target.value)}
        />
      </Field>
    );
  }
  if (node.type === "output_semantic_index") {
    return (
      <Field label="Semantic index ref">
        <Input
          aria-label="Semantic index ref"
          className="h-7 font-mono text-[11px]"
          value={draft.indexRef ?? ""}
          onChange={(event) => onUpdateDraft("indexRef", event.target.value)}
        />
      </Field>
    );
  }
  if (node.type === "output_ontology") {
    return (
      <div className="space-y-2">
        <Field label="Ontology mapping ref">
          <Input
            aria-label="Ontology mapping ref"
            className="h-7 font-mono text-[11px]"
            value={draft.mappingRef ?? ""}
            onChange={(event) =>
              onUpdateDraft("mappingRef", event.target.value)
            }
          />
        </Field>
        <p className="text-[11px] leading-5 text-muted-foreground">
          Build는 immutable mapping candidate만 만듭니다. 활성 Ontology와 object
          index는 별도의 제안 검토·승인·reindex gate를 통과하기 전까지 바뀌지
          않습니다.
        </p>
      </div>
    );
  }
  if (
    node.type === "media_to_table_rows" ||
    node.type === "content_units_to_dataset"
  ) {
    return (
      <div className="border border-[#B7D9D5] bg-[#F3FAF9] px-2.5 py-2 text-[11px] leading-5">
        이 bridge는 artifact 정체성과 보안 envelope를 유지하면서 다음 plane의
        typed row contract를 만듭니다. 별도의 사용자 설정은 없습니다.
      </div>
    );
  }
  return null;
}

function SelectCastEditor({
  node,
  columns,
  onChange,
}: {
  node: PipelineCanvasNode;
  columns: readonly SelectCastColumn[];
  onChange: (columns: SelectCastColumn[]) => void;
}) {
  const seedFromSchema = () => {
    onChange(
      (node.schema ?? []).map((column) => ({
        source: column.name,
        name: column.name,
        type: safeCastTypeFor(column.type),
      })),
    );
  };
  const updateColumn = (
    index: number,
    key: keyof SelectCastColumn,
    value: string,
  ) => {
    onChange(
      columns.map((column, columnIndex) =>
        columnIndex === index ? { ...column, [key]: value } : column,
      ),
    );
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-medium text-muted-foreground">
          컬럼 매핑
        </span>
        <div className="flex items-center gap-1">
          {columns.length === 0 && (node.schema?.length ?? 0) > 0 ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="h-6 px-2 text-[10px]"
              onClick={seedFromSchema}
            >
              현재 스키마 채우기
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[10px]"
            onClick={() =>
              onChange([
                ...columns,
                { source: "", name: "", type: "VARCHAR" },
              ])
            }
          >
            <Plus className="size-3" />
            컬럼
          </Button>
        </div>
      </div>
      {columns.length === 0 ? (
        <p className="rounded border border-dashed px-2 py-2 text-[11px] text-muted-foreground">
          출력할 컬럼을 추가하세요. 각 행은 원본 컬럼, 출력 이름, 변환 타입을
          실행 설정으로 저장합니다.
        </p>
      ) : (
        <div className="space-y-1.5">
          {columns.map((column, index) => (
            <div
              key={index}
              className="grid grid-cols-[1fr_1fr_82px_24px] items-center gap-1"
            >
              <Input
                aria-label={`원본 컬럼 ${index + 1}`}
                className="h-7 min-w-0 font-mono text-[10px]"
                placeholder="source"
                value={column.source}
                onChange={(event) =>
                  updateColumn(index, "source", event.target.value)
                }
              />
              <Input
                aria-label={`출력 컬럼 ${index + 1}`}
                className="h-7 min-w-0 font-mono text-[10px]"
                placeholder="name"
                value={column.name}
                onChange={(event) =>
                  updateColumn(index, "name", event.target.value)
                }
              />
              <Input
                aria-label={`캐스트 타입 ${index + 1}`}
                className="h-7 min-w-0 font-mono text-[10px]"
                placeholder="VARCHAR"
                value={column.type}
                onChange={(event) =>
                  updateColumn(index, "type", event.target.value.toUpperCase())
                }
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`${column.name || column.source || index + 1} 컬럼 제거`}
                className="size-6 p-0 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                onClick={() =>
                  onChange(
                    columns.filter((_, columnIndex) => columnIndex !== index),
                  )
                }
              >
                <Trash2 className="size-3" />
              </Button>
            </div>
          ))}
        </div>
      )}
      <div className="grid grid-cols-[1fr_1fr_82px_24px] gap-1 font-mono text-[9px] text-muted-foreground">
        <span>원본</span>
        <span>출력</span>
        <span>타입</span>
        <span />
      </div>
    </div>
  );
}

function safeCastTypeFor(sourceType: string): string {
  const normalized = sourceType.trim().toLowerCase();
  if (["string", "text", "utf8"].includes(normalized)) return "VARCHAR";
  if (["int", "integer", "int32", "int64", "long"].includes(normalized))
    return "BIGINT";
  if (
    ["number", "float", "float32", "float64", "double"].includes(normalized)
  )
    return "DOUBLE";
  if (["bool", "boolean"].includes(normalized)) return "BOOLEAN";
  if (normalized === "date") return "DATE";
  if (
    ["datetime", "timestamp", "timestamp with time zone"].includes(normalized)
  )
    return "TIMESTAMP";
  const upper = sourceType.trim().toUpperCase();
  return /^[A-Z][A-Z0-9_]*(\([0-9]+(,[0-9]+)?\))?$/.test(upper)
    ? upper
    : "VARCHAR";
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
