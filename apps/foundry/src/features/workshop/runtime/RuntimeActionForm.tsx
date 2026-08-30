import {
  idempotencyKey as createIdempotencyKey,
  type GenericObject,
  type OsdkActionPayload,
  type OsdkActionType,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteMutation,
  useFoundryLiteClient,
  useFoundryLiteOsdkClient,
  useFoundryLiteProvidedActionForm,
  useFoundryLiteSession,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { useWorkshopRuntimeApplicationId } from "./runtime-application-context";

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
  requiresHumanConfirmation?: boolean;
}

/**
 * 런타임 액션 폼 위젯: 파라미터 입력 → 실행(apply).
 * Technical concurrency/idempotency details stay behind the product surface.
 */
export function RuntimeActionForm({
  actionView,
  targetObject,
  onApplied,
  onCancel,
  requiresHumanConfirmation = false,
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
  const client = useFoundryLiteClient();
  const applicationId = useWorkshopRuntimeApplicationId();
  const session = useFoundryLiteSession();
  const [pinnedRequest, setPinnedRequest] = useState<PinnedRequest | null>(
    null,
  );
  const [runEvidence, setRunEvidence] = useState<ActionRunResult | null>(null);
  const [successRequestId, setSuccessRequestId] = useState<string | null>(null);
  const [isConfirmationOpen, setIsConfirmationOpen] = useState(false);

  const issueIdempotencyKey = useCallback(
    () => createIdempotencyKey(actionView.apiName, targetObject.objectId),
    [actionView.apiName, targetObject.objectId],
  );

  const applyMutation = useFoundryLiteMutation(
    async (request: PinnedRequest) => {
      if (!applicationId) return osdk(request.actionType).applyAction(request.payload);
      const payload = request.payload as {
        objectType?: string;
        params?: Record<string, unknown>;
      };
      return client.aip.pilot.startAction(
        applicationId,
        request.actionType.apiName,
        {
          target: {
            objectType: payload.objectType ?? targetObject.objectType,
            objectId: targetObject.objectId,
          },
          expectedObjectVersion: request.expectedObjectVersion,
          params: payload.params ?? {},
        },
        { idempotencyKey: request.idempotencyKey, waitSeconds: 30 },
      );
    },
    {
      onSuccess: (result) => {
        setRunEvidence(result as ActionRunResult);
        setIsConfirmationOpen(false);
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
    const actionType = form.runtimeActionType as OsdkActionType | null;
    if (
      !actionType ||
      !form.payload ||
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
        ...form.payload,
        idempotencyKey: submitIdempotencyKey,
      } as OsdkActionPayload<OsdkActionType>,
    };
  };

  const handleSubmit = () => {
    if (requiresHumanConfirmation && !isConfirmationOpen) {
      setIsConfirmationOpen(true);
      return;
    }
    const request =
      pinnedRequest ??
      (form.idempotencyKey ? buildRequest(form.idempotencyKey) : null);
    if (!request) return;
    if (!pinnedRequest) setPinnedRequest(request);
    void applyMutation.execute(request);
  };

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-[var(--workshop-line,#d5dce1)] bg-[var(--workshop-subtle,#f6f8fa)] p-3">
        <div className="text-[10px] font-semibold tracking-wide text-[#748195] uppercase">처리할 업무</div>
        <div className="mt-1 truncate text-[12px] font-medium text-[var(--workshop-ink,#1c2127)]">
          {targetObject.objectId}
        </div>
        <div className="mt-1 text-[10px] text-[#748195]">현재 화면에 선택된 항목만 변경됩니다.</div>
      </div>

      {form.parameterFields.length > 0 ? (
        form.parameterFields.filter((field) => field.isVisible).map((field) => (
          <div key={field.name} className="space-y-1">
            <label className="flex items-center gap-1 text-xs font-medium">
              {field.label}
              {field.isRequired ? (
                <span className="text-destructive">*</span>
              ) : null}
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

      {requiresHumanConfirmation && isConfirmationOpen ? (
        <div className="space-y-2 rounded border border-[#d99a3d] bg-[#fff8e8] p-3">
          <p className="text-[12px] font-semibold text-[#7a4b08]">사람 확인이 필요한 업무입니다</p>
          <p className="text-[11px] leading-5 text-[#6d5a3d]">
            대상, 입력값, 현재 버전을 확인하세요. “확인하고 실행”을 누르기 전에는 아무것도 바뀌지 않습니다.
          </p>
        </div>
      ) : null}

      {runEvidence ? (
        <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
          <div className="flex flex-wrap items-center gap-2">
            <StatusPill intent="success">
              {runEvidence.status ?? "적용됨"}
            </StatusPill>
            {runEvidence.idempotentReplay ? <StatusPill intent="info">안전하게 재확인됨</StatusPill> : null}
          </div>
          <p className="text-[11px] text-muted-foreground">변경 내용은 운영 기록에 안전하게 남았습니다.</p>
          <details className="text-[10px] text-muted-foreground">
            <summary className="cursor-pointer">운영 기록 번호 보기</summary>
            <div className="mt-1 space-y-0.5 font-mono">
              {runEvidence.actionRunId ? <div>{runEvidence.actionRunId}</div> : null}
              {runEvidence.objectEditId ? <div>{runEvidence.objectEditId}</div> : null}
              {successRequestId ? <div>{successRequestId}</div> : null}
            </div>
          </details>
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
              ? "같은 내용 다시 시도"
              : requiresHumanConfirmation && isConfirmationOpen
                ? "확인하고 실행"
                : requiresHumanConfirmation
                  ? "내용 검토"
                  : actionView.displayName}
        </button>
      </div>
    </div>
  );
}
