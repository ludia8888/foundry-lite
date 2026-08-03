import {
  idempotencyKey as createIdempotencyKey,
  type ActionValidationResponse,
  type GenericObject,
  type OsdkActionPayload,
  type OsdkActionType,
  type OsdkActionValidationPayload,
} from "@foundry-lite/sdk";
import {
  useFoundryLiteMutation,
  useFoundryLiteOsdkClient,
  useFoundryLiteProvidedActionForm,
  useFoundryLiteSession,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  StaleObjectVersionNotice,
  isStaleObjectVersionError,
  staleObjectVersionDetails,
} from "@/components/shared/StaleObjectVersionNotice";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";

import {
  ActionRunEvidence,
  type ActionRunEvidenceData,
} from "./ActionRunEvidence";
import { ParameterInput } from "./ParameterInput";
import { ValidationResultPanel } from "./ValidationResultPanel";

interface ActionFormPanelProps {
  actionView: FoundryLiteOntologyActionView;
  targetObject: GenericObject;
  onApplied: () => void;
}

/** 제출 시점에 고정된 apply 요청: 재제출 시 그대로 재전송해 idempotent replay를 얻는다. */
type PinnedActionRequest = {
  actionType: OsdkActionType;
  payload: OsdkActionPayload<OsdkActionType>;
  idempotencyKey: string;
  expectedObjectVersion: number;
};

/**
 * SDK가 반환하는 영문 disabledReason 중 구조적 차단만 Korean으로 매핑한다.
 * 필수 파라미터 미입력은 별도 필로 안내하므로 여기서는 null을 돌려 중복을 피한다.
 */
function resolveKoreanDisabledReason(
  form: ReturnType<typeof useFoundryLiteProvidedActionForm>,
): string | null {
  if (!form.isActionEnabled) {
    return "이 액션은 활성 온톨로지에서 비활성화되어 있습니다.";
  }
  if (!form.isGeneratedActionTypeAvailable) {
    return "정적 OSDK로 실행하려면 SDK를 재생성해야 합니다.";
  }
  if (!form.isTargetObjectTypeMatched) {
    return "선택한 객체 타입이 이 액션의 대상과 일치하지 않습니다.";
  }
  return null;
}

/**
 * 폼 런타임: 파라미터 입력 → 사전 검증(validate) → 실행(apply).
 * 검증 결과와 apply 결과를 분리해 표시하고, idempotency key/버전을 evidence로 노출한다.
 */
export function ActionFormPanel({
  actionView,
  targetObject,
  onApplied,
}: ActionFormPanelProps) {
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
  const [validation, setValidation] = useState<ActionValidationResponse | null>(
    null,
  );
  const [validationRequestId, setValidationRequestId] = useState<string | null>(
    null,
  );
  // 첫 제출 시점의 payload를 고정 보관해 재제출 시 idempotent replay evidence를 그대로 받는다.
  const [pinnedRequest, setPinnedRequest] =
    useState<PinnedActionRequest | null>(null);
  const [runEvidence, setRunEvidence] = useState<ActionRunEvidenceData | null>(
    null,
  );
  const [successRequestId, setSuccessRequestId] = useState<string | null>(null);

  const runValidate = useCallback(async () => {
    const generated = form.generatedActionType as OsdkActionType | null;
    if (!generated) return null;
    const payload = {
      objectId: targetObject.objectId,
      expectedObjectVersion: targetObject.objectVersion,
      params: form.params,
    } as OsdkActionValidationPayload<OsdkActionType>;
    return osdk(generated).validateAction(payload);
  }, [form.generatedActionType, form.params, osdk, targetObject]);

  const validateMutation = useFoundryLiteMutation(runValidate, {
    onSuccess: (result) => {
      setValidation(result);
      setValidationRequestId(session.lastRequestId);
    },
  });

  const issueIdempotencyKey = useCallback(
    () => createIdempotencyKey(actionView.apiName, targetObject.objectId),
    [actionView.apiName, targetObject.objectId],
  );

  const applyMutation = useFoundryLiteMutation(
    (request: PinnedActionRequest) =>
      osdk(request.actionType).applyAction(request.payload),
    {
      onSuccess: (result) => {
        setRunEvidence(result as ActionRunEvidenceData);
        onApplied();
      },
      onError: (error) => {
        // 재시도 가능(네트워크/5xx) 에러는 동일 payload 재전송이 안전하므로 고정 유지.
        // 그 외(검증 실패 등)는 키가 이미 소모되었으므로 새 키로 재구성한다.
        if (error.retryable) return;
        if (isStaleObjectVersionError(error)) return;
        setPinnedRequest(null);
        form.setIdempotencyKey(issueIdempotencyKey());
      },
    },
  );

  // 성공 패널 request_id는 apply 응답 값으로 고정 (reload 응답이 덮어쓰지 못하게).
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

  const buildSubmitRequest = (
    submitIdempotencyKey: string,
  ): PinnedActionRequest | null => {
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
      (form.idempotencyKey ? buildSubmitRequest(form.idempotencyKey) : null);
    if (!request) return;
    if (!pinnedRequest) setPinnedRequest(request);
    void applyMutation.execute(request);
  };

  const handleSubmitAsNewRequest = () => {
    const nextIdempotencyKey = issueIdempotencyKey();
    form.setIdempotencyKey(nextIdempotencyKey);
    const request = buildSubmitRequest(nextIdempotencyKey);
    if (!request) return;
    setPinnedRequest(request);
    setRunEvidence(null);
    setSuccessRequestId(null);
    void applyMutation.execute(request);
  };

  const handleReissueIdempotencyKey = () => {
    setPinnedRequest(null);
    form.setIdempotencyKey(issueIdempotencyKey());
  };

  const handleRefreshAfterConflict = () => {
    setPinnedRequest(null);
    setValidation(null);
    setValidationRequestId(null);
    setRunEvidence(null);
    setSuccessRequestId(null);
    form.setIdempotencyKey(issueIdempotencyKey());
    onApplied();
  };

  const liveObjectVersion =
    form.expectedObjectVersion ?? targetObject.objectVersion;
  const isPinnedVersionStale =
    pinnedRequest !== null &&
    pinnedRequest.expectedObjectVersion !== liveObjectVersion;
  const staleVersion = staleObjectVersionDetails(applyMutation.error);
  const isStaleConflictResolved =
    staleVersion.currentNumber !== null &&
    targetObject.objectVersion >= staleVersion.currentNumber;
  const isApplyBlockedByStaleVersion =
    isStaleObjectVersionError(applyMutation.error) && !isStaleConflictResolved;

  // 파라미터 미입력은 별도 Korean 필로 안내하므로, 구조적 차단만 Korean 문구로 노출한다.
  const koreanDisabledReason = resolveKoreanDisabledReason(form);

  return (
    <div className="space-y-3">
      <div className="space-y-1 rounded border bg-muted/40 p-2 font-mono text-[11px]">
        <div>
          대상: {targetObject.objectType} / {targetObject.objectId}
        </div>
        <div>
          expectedObjectVersion=v
          {pinnedRequest?.expectedObjectVersion ?? liveObjectVersion}
          {isPinnedVersionStale ? ` · current=v${liveObjectVersion}` : null}
          <span className="ml-1 font-sans text-muted-foreground">
            {pinnedRequest ? "(제출 payload 고정)" : "(낙관적 잠금)"}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          <span className="max-w-72 truncate">
            Idempotency-Key=
            {pinnedRequest?.idempotencyKey ?? form.idempotencyKey ?? "—"}
          </span>
          <button
            type="button"
            aria-label="Idempotency key 재발급"
            className="rounded border p-0.5 text-muted-foreground hover:text-foreground"
            onClick={handleReissueIdempotencyKey}
          >
            <RefreshCw className="size-3" />
          </button>
        </div>
        {pinnedRequest ? (
          <p className="font-sans text-[10px] text-muted-foreground">
            재제출 시 동일 payload를 재전송해 idempotent replay 근거를
            표시합니다.
          </p>
        ) : null}
      </div>

      {form.parameterFields.length > 0 ? (
        form.parameterFields.filter((field) => field.isVisible).map((field) => (
          <div key={field.name} className="space-y-1">
            <Label className="flex items-center gap-1 text-xs">
              {field.label}
              {field.isRequired ? (
                <span className="text-destructive">*</span>
              ) : null}
              <span className="font-mono text-[10px] text-muted-foreground">
                {field.dataType}
              </span>
            </Label>
            <ParameterInput
              field={field}
              onChange={(value) => form.setParam(field.name, value)}
            />
            {field.description ? (
              <p className="text-[11px] text-muted-foreground">
                {field.description}
              </p>
            ) : null}
          </div>
        ))
      ) : (
        <p className="text-[11px] text-muted-foreground">
          이 액션은 파라미터가 없습니다.
        </p>
      )}

      {validation ? (
        <ValidationResultPanel
          result={validation}
          requestId={validationRequestId}
        />
      ) : null}
      {validateMutation.error ? (
        <ErrorState error={validateMutation.error} />
      ) : null}
      {applyMutation.error && !isStaleConflictResolved ? (
        <ErrorState error={applyMutation.error} />
      ) : null}
      <StaleObjectVersionNotice
        error={applyMutation.error}
        onRefresh={handleRefreshAfterConflict}
        isResolved={isStaleConflictResolved}
      />
      {runEvidence ? (
        <ActionRunEvidence result={runEvidence} requestId={successRequestId} />
      ) : null}

      {koreanDisabledReason && !runEvidence ? (
        <p className="text-[11px] text-muted-foreground">
          {koreanDisabledReason}
        </p>
      ) : null}

      <div className="flex flex-wrap items-center justify-end gap-2">
        {form.hasRequiredParameters && !form.hasAllRequiredParameters ? (
          <StatusPill intent="warning">필수 파라미터 미입력</StatusPill>
        ) : null}
        <Button
          size="sm"
          variant="outline"
          disabled={!form.generatedActionType || validateMutation.isRunning}
          onClick={() => void validateMutation.execute(undefined)}
        >
          <ShieldCheck />
          {validateMutation.isRunning ? "검증 중..." : "사전 검증"}
        </Button>
        {runEvidence ? (
          <Button
            size="sm"
            variant="outline"
            disabled={applyMutation.isRunning}
            onClick={handleSubmitAsNewRequest}
          >
            새 요청으로 실행
          </Button>
        ) : null}
        <Button
          size="sm"
          disabled={
            (pinnedRequest === null && !form.canSubmitAction) ||
            applyMutation.isRunning ||
            isApplyBlockedByStaleVersion
          }
          onClick={handleSubmit}
        >
          {applyMutation.isRunning
            ? "실행 중..."
            : pinnedRequest
              ? "동일 요청 재전송"
              : "액션 실행"}
        </Button>
      </div>
    </div>
  );
}
