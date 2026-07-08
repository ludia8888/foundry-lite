import {
  idempotencyKey as createIdempotencyKey,
  type ActionBatchApplyResponse,
  type ActionValidationResponse,
  type GenericObject,
  type OsdkActionType,
  type OsdkActionValidationPayload,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteBatchActionSubmit,
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteOsdkClient,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { CheckCircle2, ListChecks, XCircle } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { operationsRunHref } from "@/lib/operations-links";

import type { ActionDefinitionView } from "../lib/action-definition";

interface BatchApplyPanelProps {
  actionView: FoundryLiteOntologyActionView;
  definition: ActionDefinitionView;
  objects: GenericObject[];
  onApplied: () => void;
}

type TargetPreflight = {
  objectId: string;
  result: ActionValidationResponse | null;
  requestId: string | null;
  error: string | null;
};

/** 배치 실행: 여러 objectId 선택 → 대상별 사전 검증(실패 row 추적) → 원자적 batch apply. */
export function BatchApplyPanel({
  actionView,
  definition,
  objects,
  onApplied,
}: BatchApplyPanelProps) {
  const osdk = useFoundryLiteOsdkClient();
  const client = useFoundryLiteClient();
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState("batch approve");
  const [preflights, setPreflights] = useState<TargetPreflight[]>([]);
  const [batchResponse, setBatchResponse] =
    useState<ActionBatchApplyResponse | null>(null);

  const selectedObjects = useMemo(
    () => objects.filter((object) => selectedIds.has(object.objectId)),
    [objects, selectedIds],
  );

  const batchAction = useMemo(
    () =>
      (
        client.actions as unknown as Record<
          string,
          Parameters<typeof useFoundryLiteBatchActionSubmit>[0]
        >
      )[actionView.apiName],
    [client, actionView.apiName],
  );

  const batchSubmit = useFoundryLiteBatchActionSubmit(
    batchAction,
    actionView.apiName,
    {
      onSuccess: (result) => {
        setBatchResponse(result);
        onApplied();
      },
    },
  );

  const runPreflight = useCallback(async () => {
    const generated = actionView.generatedActionType as OsdkActionType | null;
    if (!generated) return [];
    const results: TargetPreflight[] = [];
    for (const object of selectedObjects) {
      const payload = {
        objectId: object.objectId,
        expectedObjectVersion: object.objectVersion,
        params: { reason },
      } as OsdkActionValidationPayload<OsdkActionType>;
      try {
        const result = await osdk(generated).validateAction(payload);
        results.push({
          objectId: object.objectId,
          result,
          requestId: null,
          error: null,
        });
      } catch (error) {
        results.push({
          objectId: object.objectId,
          result: null,
          requestId: null,
          error: error instanceof Error ? error.message : String(error),
        });
      }
    }
    return results;
  }, [actionView.generatedActionType, osdk, reason, selectedObjects]);

  const preflightMutation = useFoundryLiteMutation(runPreflight, {
    onSuccess: (results) => setPreflights(results ?? []),
  });

  const handleToggle = (objectId: string) => {
    setSelectedIds((previous) => {
      const next = new Set(previous);
      if (next.has(objectId)) next.delete(objectId);
      else next.add(objectId);
      return next;
    });
    setPreflights([]);
    setBatchResponse(null);
  };

  const validCount = preflights.filter(
    (p) => p.result?.result === "VALID",
  ).length;
  const invalidCount = preflights.length - validCount;

  const handleBatchSubmit = () => {
    setBatchResponse(null);
    void batchSubmit.execute({
      request: {
        targets: selectedObjects.map((object) => ({
          objectId: object.objectId,
          expectedObjectVersion: object.objectVersion,
        })),
        params: { reason },
      } as Parameters<typeof batchSubmit.execute>[0]["request"],
      idempotencyKey: createIdempotencyKey(actionView.apiName, "batch"),
    });
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-1.5">
        <ListChecks className="size-3.5 text-muted-foreground" />
        <span className="section-label">
          배치 실행 · {actionView.displayName}
        </span>
      </div>

      <div className="space-y-1.5">
        <Label className="text-xs">대상 선택</Label>
        <div className="space-y-1">
          {objects.map((object) => {
            const preflight = preflights.find(
              (p) => p.objectId === object.objectId,
            );
            const status = object.properties?.status;
            return (
              <label
                key={object.objectId}
                className="flex cursor-pointer items-center gap-2 rounded border px-2 py-1.5 hover:bg-muted/60"
              >
                <Checkbox
                  checked={selectedIds.has(object.objectId)}
                  onCheckedChange={() => handleToggle(object.objectId)}
                />
                <span className="flex-1 font-mono text-[11px]">
                  {object.objectId}
                </span>
                {status !== undefined && status !== null ? (
                  <span className="font-mono text-[10px] text-muted-foreground">
                    {String(status)}
                  </span>
                ) : null}
                <span className="font-mono text-[10px] text-muted-foreground">
                  v{object.objectVersion}
                </span>
                {preflight ? (
                  preflight.result?.result === "VALID" ? (
                    <CheckCircle2 className="size-3.5 text-success" />
                  ) : (
                    <XCircle className="size-3.5 text-destructive" />
                  )
                ) : null}
              </label>
            );
          })}
        </div>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">reason</Label>
        <Input
          className="h-8 text-xs"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        />
      </div>

      {preflights.length > 0 ? (
        <div className="space-y-1.5 rounded border bg-muted/40 p-2">
          <div className="flex items-center gap-2">
            <span className="section-label">사전 검증 결과</span>
            <StatusPill intent="success">통과 {validCount}</StatusPill>
            <StatusPill intent={invalidCount > 0 ? "danger" : "neutral"}>
              실패 {invalidCount}
            </StatusPill>
          </div>
          {preflights.map((preflight) => {
            const isValid = preflight.result?.result === "VALID";
            const issues = preflight.result
              ? [
                  ...preflight.result.target.issues,
                  ...preflight.result.submissionCriteria,
                  ...Object.values(preflight.result.parameters).flatMap(
                    (parameter) => parameter.issues,
                  ),
                ]
              : [];
            return (
              <div
                key={preflight.objectId}
                className="rounded border bg-card px-2 py-1 text-[11px]"
              >
                <div className="flex items-center gap-1.5">
                  {isValid ? (
                    <CheckCircle2 className="size-3 text-success" />
                  ) : (
                    <XCircle className="size-3 text-destructive" />
                  )}
                  <span className="font-mono">{preflight.objectId}</span>
                  <StatusPill intent={isValid ? "success" : "danger"}>
                    {isValid ? "VALID" : "INVALID"}
                  </StatusPill>
                </div>
                {issues.map((issue, index) => (
                  <div
                    key={`${issue.code}-${index}`}
                    className="pl-4 text-[10px] text-destructive"
                  >
                    <span className="font-mono">[{issue.code}]</span>{" "}
                    {issue.message}
                  </div>
                ))}
                {preflight.error ? (
                  <div className="pl-4 text-[10px] text-destructive">
                    {preflight.error}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {definition.hasWritebacks ? (
        <div className="rounded border border-warning/40 bg-warning/5 p-2 text-[11px]">
          <div className="flex items-center gap-1.5">
            <StatusPill intent="warning">future</StatusPill>
            <span className="font-medium">
              이 액션은 외부 writeback을 선언해 배치 실행이 제한됩니다.
            </span>
          </div>
          <p className="mt-1 text-muted-foreground">
            writeback saga는 단일 대상 원자성으로 정의되어 있어, 백엔드는 여러
            대상 배치 apply를 거부합니다. 위 사전 검증으로 대상별 통과/실패를
            확인한 뒤, 실제 적용은 폼 런타임에서 대상별로 실행하세요.
          </p>
        </div>
      ) : null}

      {batchResponse ? (
        <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
          <div className="flex items-center gap-2">
            <StatusPill intent="success">{batchResponse.status}</StatusPill>
            <span className="text-[11px] text-muted-foreground">
              {batchResponse.results.length}건 적용
            </span>
          </div>
          <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
            <div>
              run_id=
              <Link
                to={operationsRunHref(batchResponse.actionRunId, "action")}
                className="text-primary hover:underline"
              >
                {batchResponse.actionRunId}
              </Link>
            </div>
            {batchResponse.results.map((item) => (
              <div key={item.objectId}>
                {item.objectId} → v{item.newObjectVersion} · {item.objectEditId}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {batchSubmit.error ? <ErrorState error={batchSubmit.error} /> : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={selectedObjects.length === 0 || preflightMutation.isRunning}
          onClick={() => void preflightMutation.execute(undefined)}
        >
          {preflightMutation.isRunning ? "검증 중..." : "대상별 사전 검증"}
        </Button>
        <Button
          size="sm"
          disabled={selectedObjects.length === 0 || batchSubmit.isRunning}
          onClick={handleBatchSubmit}
        >
          {batchSubmit.isRunning ? "실행 중..." : "배치 실행"}
        </Button>
      </div>
    </div>
  );
}
