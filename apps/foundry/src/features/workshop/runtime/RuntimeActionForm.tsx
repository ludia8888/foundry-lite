import {
  idempotencyKey as createIdempotencyKey,
  type GenericObject,
  type OsdkActionPayload,
  type OsdkActionType,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteMutation,
  useFoundryLiteOsdkClient,
  useFoundryLiteProvidedActionForm,
  useFoundryLiteSession,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";

/** apply 성공 evidence 형태. */
type ActionRunResult = {
  status?: string;
  actionRunId?: string;
  newObjectVersion?: number;
  objectEditId?: string;
  idempotentReplay?: boolean;
};

/** 제출 시점에 고정한 apply 요청: 재제출 시 그대로 재전송해 idempotent replay를 얻는다. */
type PinnedRequest = {
  actionType: OsdkActionType;
  payload: OsdkActionPayload<OsdkActionType>;
  idempotencyKey: string;
  expectedObjectVersion: number;
};

interface RuntimeActionFormProps {
  actionView: FoundryLiteOntologyActionView;
  targetObject: GenericObject;
  onApplied: () => void;
  onCancel: () => void;
}

/**
 * 런타임 액션 폼 위젯: 파라미터 입력 → 실행(apply).
 * idempotency key / expectedObjectVersion / request_id를 evidence로 노출한다.
 */
export function RuntimeActionForm({
  actionView,
  targetObject,
  onApplied,
  onCancel,
}: RuntimeActionFormProps) {
  const initialIdempotencyKey = useMemo(
    () => createIdempotencyKey(actionView.apiName, targetObject.objectId),
    [actionView.apiName, targetObject.objectId],
  );
  const form = useFoundryLiteProvidedActionForm(actionView, {
    targetObject,
    initialIdempotencyKey,
  });
  const osdk = useFoundryLiteOsdkClient();
  const session = useFoundryLiteSession();
  const [pinnedRequest, setPinnedRequest] = useState<PinnedRequest | null>(
    null,
  );
  const [runEvidence, setRunEvidence] = useState<ActionRunResult | null>(null);
  const [successRequestId, setSuccessRequestId] = useState<string | null>(null);

  const issueIdempotencyKey = useCallback(
    () => createIdempotencyKey(actionView.apiName, targetObject.objectId),
    [actionView.apiName, targetObject.objectId],
  );

  const applyMutation = useFoundryLiteMutation(
    (request: PinnedRequest) =>
      osdk(request.actionType).applyAction(request.payload),
    {
      onSuccess: (result) => {
        setRunEvidence(result as ActionRunResult);
        onApplied();
      },
      onError: (error) => {
        // 재시도 가능(네트워크/5xx)은 동일 payload 재전송이 안전 → 고정 유지.
        // 그 외(검증 실패 등)는 키가 소모되었으므로 새 키로 재구성.
        if (error.retryable) return;
        setPinnedRequest(null);
        form.setIdempotencyKey(issueIdempotencyKey());
      },
    },
  );

  const lastResponse = session.lastResponse;
  useEffect(() => {
    if (
      lastResponse?.ok &&
      lastResponse.path.includes("/api/actions/") &&
      lastResponse.path.endsWith("/apply")
    ) {
      setSuccessRequestId(lastResponse.requestId);
    }
  }, [lastResponse]);

  const buildRequest = (submitIdempotencyKey: string): PinnedRequest | null => {
    const actionType = form.generatedActionType as OsdkActionType | null;
    if (
      !actionType ||
      form.targetObjectId === null ||
      form.expectedObjectVersion === null
    ) {
      return null;
    }
    return {
      actionType,
      idempotencyKey: submitIdempotencyKey,
      expectedObjectVersion: form.expectedObjectVersion,
      payload: {
        objectId: form.targetObjectId,
        expectedObjectVersion: form.expectedObjectVersion,
        params: form.params,
        idempotencyKey: submitIdempotencyKey,
      } as OsdkActionPayload<OsdkActionType>,
    };
  };

  const handleSubmit = () => {
    const request =
      pinnedRequest ??
      (form.idempotencyKey ? buildRequest(form.idempotencyKey) : null);
    if (!request) return;
    if (!pinnedRequest) setPinnedRequest(request);
    void applyMutation.execute(request);
  };

  const handleReissue = () => {
    setPinnedRequest(null);
    form.setIdempotencyKey(issueIdempotencyKey());
  };

  const activeIdempotencyKey =
    pinnedRequest?.idempotencyKey ?? form.idempotencyKey ?? "—";
  const activeVersion =
    pinnedRequest?.expectedObjectVersion ??
    form.expectedObjectVersion ??
    targetObject.objectVersion;

  return (
    <div className="space-y-3">
      <div className="space-y-1 rounded border bg-muted/40 p-2 font-mono text-[11px]">
        <div>
          대상: {targetObject.objectType} / {targetObject.objectId}
        </div>
        <div>
          expectedObjectVersion=v{activeVersion}
          <span className="ml-1 font-sans text-muted-foreground">
            {pinnedRequest ? "(제출 payload 고정)" : "(낙관적 잠금)"}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="max-w-64 truncate">
            Idempotency-Key={activeIdempotencyKey}
          </span>
          <button
            type="button"
            aria-label="Idempotency key 재발급"
            className="rounded border p-0.5 text-muted-foreground hover:text-foreground"
            onClick={handleReissue}
          >
            <RefreshCw className="size-3" />
          </button>
        </div>
      </div>

      {form.parameterFields.length > 0 ? (
        form.parameterFields.map((field) => (
          <div key={field.name} className="space-y-1">
            <label className="flex items-center gap-1 text-xs font-medium">
              {field.label}
              {field.isRequired ? (
                <span className="text-destructive">*</span>
              ) : null}
              <span className="font-mono text-[10px] text-muted-foreground">
                {field.dataType}
              </span>
            </label>
            {field.inputKind === "checkbox" ? (
              <Checkbox
                checked={field.value === true}
                onCheckedChange={(checked) =>
                  form.setParam(field.name, checked === true)
                }
              />
            ) : field.inputKind === "number" ? (
              <Input
                type="number"
                className="h-8 text-xs"
                value={field.hasValue ? String(field.value) : ""}
                onChange={(event) =>
                  form.setParam(
                    field.name,
                    event.target.value === ""
                      ? undefined
                      : Number(event.target.value),
                  )
                }
              />
            ) : field.inputKind === "json" ? (
              <Textarea
                className="min-h-16 font-mono text-[11px]"
                value={
                  typeof field.value === "string"
                    ? field.value
                    : JSON.stringify(field.value ?? "")
                }
                onChange={(event) =>
                  form.setParam(field.name, event.target.value)
                }
                placeholder="JSON 값"
              />
            ) : (
              <Input
                className="h-8 text-xs"
                value={typeof field.value === "string" ? field.value : ""}
                onChange={(event) =>
                  form.setParam(field.name, event.target.value)
                }
                placeholder={field.label}
              />
            )}
          </div>
        ))
      ) : (
        <p className="text-[11px] text-muted-foreground">
          이 액션은 파라미터가 없습니다.
        </p>
      )}

      {applyMutation.error ? <ErrorState error={applyMutation.error} /> : null}

      {runEvidence ? (
        <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill intent="success">
              {runEvidence.status ?? "적용됨"}
            </StatusPill>
            {runEvidence.idempotentReplay ? (
              <StatusPill intent="info">idempotent replay</StatusPill>
            ) : null}
          </div>
          <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
            {runEvidence.actionRunId ? (
              <div>run_id={runEvidence.actionRunId}</div>
            ) : null}
            {runEvidence.objectEditId ? (
              <div>edit_id={runEvidence.objectEditId}</div>
            ) : null}
            {runEvidence.newObjectVersion !== undefined ? (
              <div>new_version=v{runEvidence.newObjectVersion}</div>
            ) : null}
            {successRequestId ? <div>request_id={successRequestId}</div> : null}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {form.hasRequiredParameters && !form.hasAllRequiredParameters ? (
          <StatusPill intent="warning">필수 파라미터 미입력</StatusPill>
        ) : null}
        <button
          type="button"
          className="h-8 rounded border px-3 text-[13px] font-medium text-foreground hover:bg-muted/60"
          onClick={onCancel}
        >
          취소
        </button>
        <button
          type="button"
          className="h-8 rounded bg-[#2b9f6c] px-3.5 text-[13px] font-semibold text-white shadow-sm hover:bg-[#24895c] disabled:cursor-not-allowed disabled:opacity-50"
          disabled={
            (pinnedRequest === null && !form.canSubmitAction) ||
            applyMutation.isRunning
          }
          onClick={handleSubmit}
        >
          {applyMutation.isRunning
            ? "실행 중..."
            : pinnedRequest
              ? "동일 요청 재전송"
              : actionView.displayName}
        </button>
      </div>
    </div>
  );
}
