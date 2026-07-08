import { FileCode2, Plus, Wand2, X } from "lucide-react";

import { StatusPill } from "@/components/shared/StatusPill";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

import { FAILING_SQL, tokenizeSql, unreferencedInputs } from "../code-model";

const TOKEN_CLASS: Record<string, string> = {
  keyword: "text-[#1F6FEB] font-medium",
  string: "text-[#0A7B4E]",
  comment: "text-muted-foreground italic",
  input: "text-[#8250DF] font-medium",
  number: "text-[#B5451A]",
  plain: "text-foreground",
};

interface SqlEditorProps {
  apiName: string;
  sql: string;
  inputs: Record<string, string>;
  outputDatasetRef: string;
  onChangeApiName: (value: string) => void;
  onChangeSql: (value: string) => void;
  onChangeOutputDatasetRef: (value: string) => void;
  onRemoveInput: (alias: string) => void;
  onLoadFailingSample: () => void;
}

/**
 * 중앙 SQL 에디터(code-view.png의 examples.py 위치).
 * textarea 위에 토큰 하이라이트 오버레이를 겹쳐 mono 신택스 컬러를 낸다.
 * 상단은 transform 설정(API name·inputs·output dataset), 하단은 에디터.
 */
export function SqlEditor({
  apiName,
  sql,
  inputs,
  outputDatasetRef,
  onChangeApiName,
  onChangeSql,
  onChangeOutputDatasetRef,
  onRemoveInput,
  onLoadFailingSample,
}: SqlEditorProps) {
  const tokens = tokenizeSql(sql);
  const lineCount = Math.max(sql.split("\n").length, 1);
  const inputEntries = Object.entries(inputs);
  const unusedInputs = unreferencedInputs(sql, inputs);

  return (
    <div className="flex min-w-0 flex-1 flex-col bg-[#FBFCFD]">
      {/* 에디터 탭 바 */}
      <div className="flex h-8 shrink-0 items-center border-b bg-card px-2">
        <div className="flex items-center gap-1.5 rounded-t border border-b-0 bg-[#FBFCFD] px-2 py-1 text-[12px]">
          <FileCode2 className="size-3.5 text-[#00847A]" />
          <span className="font-medium">{apiName || "transform"}.sql</span>
        </div>
        <button
          type="button"
          className="ml-auto flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px] text-muted-foreground hover:bg-muted"
          title="실패 케이스 SQL 불러오기 (존재하지 않는 컬럼)"
          onClick={onLoadFailingSample}
        >
          <Wand2 className="size-3" />
          실패 샘플
        </button>
      </div>

      {/* transform 설정: API name / output dataset / inputs */}
      <div className="shrink-0 space-y-2 border-b bg-card px-3 py-2.5">
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1">
            <span className="text-[11px] font-semibold text-muted-foreground">
              API 이름
            </span>
            <Input
              value={apiName}
              onChange={(event) => onChangeApiName(event.target.value)}
              placeholder="예: orders_over_500"
              className="h-8 font-mono text-[12px]"
            />
          </label>
          <label className="space-y-1">
            <span className="text-[11px] font-semibold text-muted-foreground">
              출력 데이터셋 ref
            </span>
            <Input
              value={outputDatasetRef}
              onChange={(event) => onChangeOutputDatasetRef(event.target.value)}
              placeholder="예: pipelines.pipeline_demo_probe_output"
              className="h-8 font-mono text-[12px]"
            />
          </label>
        </div>
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-muted-foreground">
              선언된 입력
            </span>
            <span className="text-[10px] text-muted-foreground/70">
              좌측 Datasets에서 클릭해 추가 · SQL에서 {"{{ input('ref') }}"} 로
              참조
            </span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {inputEntries.length === 0 ? (
              <span className="text-[11px] text-muted-foreground">
                입력이 없습니다. 좌측 Datasets 트리에서 데이터셋을 추가하세요.
              </span>
            ) : (
              inputEntries.map(([alias, ref]) => {
                const isUnused = unusedInputs.includes(ref);
                return (
                  <span
                    key={alias}
                    className={cn(
                      "inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px]",
                      isUnused
                        ? "border-warning/40 bg-warning/10 text-warning"
                        : "border-border bg-muted text-foreground",
                    )}
                    title={isUnused ? "SQL에서 참조되지 않는 입력" : ref}
                  >
                    {ref}
                    <button
                      type="button"
                      className="text-muted-foreground hover:text-destructive"
                      title="입력 제거"
                      onClick={() => onRemoveInput(alias)}
                    >
                      <X className="size-3" />
                    </button>
                  </span>
                );
              })
            )}
          </div>
          {unusedInputs.length > 0 ? (
            <StatusPill intent="warning" className="mt-1">
              미참조 입력 {unusedInputs.length}건
            </StatusPill>
          ) : null}
        </div>
      </div>

      {/* 코드 에디터: 줄번호 + 하이라이트 오버레이 + textarea */}
      <div className="relative min-h-0 flex-1 overflow-hidden">
        <div className="flex h-full">
          {/* 줄번호 거터 */}
          <div className="shrink-0 select-none border-r bg-card px-2 py-2 text-right font-mono text-[12px] leading-5 text-muted-foreground/60">
            {Array.from({ length: lineCount }, (_, index) => (
              <div key={index}>{index + 1}</div>
            ))}
          </div>
          <div className="relative min-w-0 flex-1">
            {/* 하이라이트 오버레이 */}
            <pre
              aria-hidden
              className="pointer-events-none absolute inset-0 overflow-auto px-3 py-2 font-mono text-[12px] leading-5 break-words whitespace-pre-wrap"
            >
              {tokens.map((token, index) => (
                <span key={index} className={TOKEN_CLASS[token.kind]}>
                  {token.text}
                </span>
              ))}
              {"\n"}
            </pre>
            {/* 실제 입력 textarea (텍스트는 투명, 커서만 보임) */}
            <textarea
              value={sql}
              onChange={(event) => onChangeSql(event.target.value)}
              spellCheck={false}
              className="absolute inset-0 resize-none overflow-auto border-0 bg-transparent px-3 py-2 font-mono text-[12px] leading-5 break-words whitespace-pre-wrap text-transparent caret-foreground outline-none"
              placeholder="SELECT ... FROM {{ input('clean.orders') }}"
            />
          </div>
        </div>
      </div>

      {/* 하단 도움말 */}
      <div className="flex shrink-0 items-center gap-2 border-t bg-card px-3 py-1 text-[11px] text-muted-foreground">
        <Plus className="size-3" />
        입력은 {"{{ input('namespace.name') }}"} 플레이스홀더로 참조합니다.
        {sql === FAILING_SQL ? (
          <StatusPill intent="danger" className="ml-auto">
            실패 케이스 로드됨
          </StatusPill>
        ) : null}
      </div>
    </div>
  );
}
