import type { OntologyCatalog } from "@foundry-lite/sdk";
import {
  useFoundryLiteProvidedFunctionExecute,
  useFoundryLiteSession,
} from "@foundry-lite/sdk/react";
import { FunctionSquare, Play } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { operationsRunHref } from "@/lib/operations-links";
import { cn } from "@/lib/utils";

interface FunctionsPanelProps {
  catalog: OntologyCatalog | null;
}

type FunctionInput = {
  apiName: string;
  type: string;
  required: boolean;
};

type FunctionTypeView = {
  apiName: string;
  displayName: string;
  inputs: FunctionInput[];
};

function readFunctionTypes(
  catalog: OntologyCatalog | null,
): FunctionTypeView[] {
  const raw = (catalog as unknown as { functionTypes?: unknown })
    ?.functionTypes;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item) => item as Record<string, unknown>)
    .filter((item) => typeof item.apiName === "string")
    .map((item) => ({
      apiName: item.apiName as string,
      displayName:
        typeof item.displayName === "string"
          ? item.displayName
          : (item.apiName as string),
      inputs: Array.isArray(item.inputs)
        ? (item.inputs as Record<string, unknown>[]).map((input) => ({
            apiName: String(input.apiName ?? ""),
            type: String(input.type ?? "unknown"),
            required: input.required === true,
          }))
        : [],
    }));
}

function defaultInputJson(inputs: FunctionInput[]): string {
  if (inputs.length === 0) return "{}";
  const example = Object.fromEntries(
    inputs.map((input) => [
      input.apiName,
      input.apiName === "objectId" ? "O-1001" : "",
    ]),
  );
  return JSON.stringify(example, null, 2);
}

/** Functions 탭: 온톨로지 함수(orderRiskSummary 등)를 JSON 입력으로 실행하고 결과·evidence 노출. */
export function FunctionsPanel({ catalog }: FunctionsPanelProps) {
  const functionTypes = useMemo(() => readFunctionTypes(catalog), [catalog]);
  const [selectedApiName, setSelectedApiName] = useState<string | null>(null);
  const selected = useMemo(
    () =>
      functionTypes.find((fn) => fn.apiName === selectedApiName) ??
      functionTypes[0] ??
      null,
    [functionTypes, selectedApiName],
  );
  const [inputText, setInputText] = useState<string>(() =>
    defaultInputJson(selected?.inputs ?? []),
  );
  const [inputError, setInputError] = useState<string | null>(null);
  const execute = useFoundryLiteProvidedFunctionExecute();
  const session = useFoundryLiteSession();

  const handleSelect = (fn: FunctionTypeView) => {
    setSelectedApiName(fn.apiName);
    setInputText(defaultInputJson(fn.inputs));
    setInputError(null);
  };

  const handleExecute = () => {
    if (!selected) return;
    let inputs: Record<string, unknown> = {};
    try {
      inputs = inputText.trim() ? JSON.parse(inputText) : {};
      setInputError(null);
    } catch (error) {
      setInputError(
        error instanceof Error ? error.message : "JSON 파싱에 실패했습니다.",
      );
      return;
    }
    void execute.execute({ functionApiName: selected.apiName, inputs });
  };

  const result = execute.execution;

  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-[220px_minmax(0,1fr)]">
      <div className="space-y-2">
        <div className="section-label px-1">함수 ({functionTypes.length})</div>
        <div className="space-y-1">
          {functionTypes.map((fn) => {
            const isSelected = fn.apiName === selected?.apiName;
            return (
              <button
                key={fn.apiName}
                type="button"
                onClick={() => handleSelect(fn)}
                className={cn(
                  "flex w-full items-start gap-2 rounded border px-2 py-1.5 text-left transition-colors",
                  isSelected
                    ? "border-primary/40 bg-accent"
                    : "border-transparent hover:bg-muted/60",
                )}
              >
                <FunctionSquare
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0",
                    isSelected ? "text-primary" : "text-muted-foreground",
                  )}
                />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-xs font-medium">
                    {fn.displayName}
                  </div>
                  <div className="truncate font-mono text-[10px] text-muted-foreground">
                    {fn.apiName}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      </div>

      <div className="space-y-3 rounded border bg-card p-3">
        {selected ? (
          <>
            <div className="space-y-0.5">
              <div className="text-sm font-medium">{selected.displayName}</div>
              <div className="font-mono text-[10px] text-muted-foreground">
                functions.generic.execute · {selected.apiName}
              </div>
            </div>
            {selected.inputs.length > 0 ? (
              <div className="flex flex-wrap gap-1">
                {selected.inputs.map((input) => (
                  <StatusPill
                    key={input.apiName}
                    intent={input.required ? "info" : "neutral"}
                  >
                    {input.apiName}: {input.type}
                    {input.required ? " *" : ""}
                  </StatusPill>
                ))}
              </div>
            ) : null}
            <div className="space-y-1">
              <Label className="text-xs">입력 (JSON)</Label>
              <Textarea
                className="min-h-24 font-mono text-[11px]"
                value={inputText}
                onChange={(event) => setInputText(event.target.value)}
              />
              {inputError ? (
                <p className="text-[11px] text-destructive">{inputError}</p>
              ) : null}
            </div>
            {execute.error ? <ErrorState error={execute.error} /> : null}
            {result ? (
              <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
                <div className="flex items-center gap-2">
                  <StatusPill intent="success">{result.status}</StatusPill>
                </div>
                <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
                  <div>logic_run_id={result.logicRunId}</div>
                  {result.aiRunId ? (
                    <div>
                      ai_run_id=
                      <Link
                        to={operationsRunHref(result.aiRunId, "ai")}
                        className="text-primary hover:underline"
                      >
                        {result.aiRunId}
                      </Link>
                    </div>
                  ) : null}
                  <div>result_hash={result.resultHash}</div>
                  {session.lastRequestId ? (
                    <div>request_id={session.lastRequestId}</div>
                  ) : null}
                </div>
                <pre className="max-h-48 overflow-auto rounded border bg-muted/40 p-2 font-mono text-[10px] text-foreground/80">
                  {JSON.stringify(result.output, null, 2)}
                </pre>
              </div>
            ) : null}
            <div className="flex items-center justify-end">
              <Button
                size="sm"
                disabled={execute.isRunning}
                onClick={handleExecute}
              >
                <Play />
                {execute.isRunning ? "실행 중..." : "함수 실행"}
              </Button>
            </div>
          </>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            실행 가능한 함수가 없습니다.
          </p>
        )}
      </div>
    </div>
  );
}
