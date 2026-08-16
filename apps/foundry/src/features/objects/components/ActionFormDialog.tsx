import {
  idempotencyKey as createIdempotencyKey,
  type ActionValidationResponse,
  type GenericObject,
  type OsdkActionPayload,
  type OsdkActionValidationPayload,
  type OsdkActionType,
} from "@foundry-lite/sdk";
import {
  foundryLiteActionInputText,
  foundryLiteActionInputType,
  foundryLiteActionInputValue,
} from "@foundry-lite/sdk/action-values";
import {
  useFoundryLiteMutation,
  useFoundryLiteOsdkClient,
  useFoundryLiteProvidedActionForm,
  useFoundryLiteSession,
  type FoundryLiteActionParameterField,
  type FoundryLiteOntologyActionView,
} from "@foundry-lite/sdk/react";
import { RefreshCw, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { ActionCriteriaEvaluationPanel } from "@/components/shared/ActionCriteriaEvaluation";
import { StatusPill } from "@/components/shared/StatusPill";
import {
  StaleObjectVersionNotice,
  isStaleObjectVersionError,
  staleObjectVersionDetails,
} from "@/components/shared/StaleObjectVersionNotice";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

interface ActionFormDialogProps {
  actionView: FoundryLiteOntologyActionView | null;
  targetObject: GenericObject | null;
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  onApplied: () => void;
}

/** 제출 시점에 고정된 apply 요청: 재제출 시 그대로 재전송해 idempotent replay를 얻는다. */
type PinnedActionRequest = {
  actionType: OsdkActionType;
  payload: OsdkActionPayload<OsdkActionType>;
  idempotencyKey: string;
  expectedObjectVersion: number;
};

type ActionRunEvidence = {
  status?: string;
  actionRunId?: string;
  newObjectVersion?: number;
  objectEditId?: string;
  idempotentReplay?: boolean;
};

function ParameterInput({
  field,
  onChange,
}: {
  field: FoundryLiteActionParameterField;
  onChange: (value: unknown) => void;
}) {
  if (field.inputKind === "checkbox") {
    return (
      <Checkbox
        checked={field.value === true}
        onCheckedChange={(checked) => onChange(checked === true)}
      />
    );
  }
  if (field.inputKind === "number") {
    return (
      <Input
        type="number"
        className="h-8 text-xs"
        value={field.hasValue ? String(field.value) : ""}
        onChange={(event) =>
          onChange(
            event.target.value === "" ? undefined : Number(event.target.value),
          )
        }
      />
    );
  }
  if (field.dataType === "decimal") {
    return (
      <Input
        type="text"
        inputMode="decimal"
        className="h-8 text-xs"
        value={typeof field.value === "string" ? field.value : ""}
        onChange={(event) => onChange(foundryLiteActionInputValue(field.dataType, event.target.value))}
      />
    );
  }
  if (field.inputKind === "json") {
    return (
      <Textarea
        className="min-h-16 font-mono text-[11px]"
        value={
          typeof field.value === "string"
            ? field.value
            : JSON.stringify(field.value ?? "")
        }
        onChange={(event) => onChange(event.target.value)}
        placeholder="JSON 값"
      />
    );
  }
  return (
    <Input
      type={foundryLiteActionInputType(field.dataType)}
      className="h-8 text-xs"
      value={foundryLiteActionInputText(field.dataType, field.value)}
      onChange={(event) => onChange(foundryLiteActionInputValue(field.dataType, event.target.value))}
    />
  );
}

function ValidationResultPanel({
  result,
}: {
  result: ActionValidationResponse;
}) {
  const isValid = result.result === "VALID";
  const issues = [
    ...result.target.issues,
    ...result.submissionCriteria,
    ...Object.values(result.parameters).flatMap(
      (parameter) => parameter.issues,
    ),
  ];
  return (
    <div className="space-y-1 rounded border bg-muted/40 p-2">
      <div className="flex items-center gap-2">
        <StatusPill intent={isValid ? "success" : "danger"}>
          검증 {isValid ? "통과" : "실패"}
        </StatusPill>
        <span className="font-mono text-[10px] text-muted-foreground">
          expected=v{result.target.expectedObjectVersion} / current=
          {result.target.currentObjectVersion === null
            ? "?"
            : `v${result.target.currentObjectVersion}`}
        </span>
      </div>
      {issues.map((issue, index) => (
        <div
          key={`${issue.code}-${index}`}
          className="text-[11px] text-destructive"
        >
          <span className="font-mono">[{issue.code}]</span> {issue.message}
        </div>
      ))}
      <ActionCriteriaEvaluationPanel evaluation={result.submissionCriteriaEvaluation} />
    </div>
  );
}

function ActionFormContent({
  actionView,
  targetObject,
  onApplied,
  onClose,
}: {
  actionView: FoundryLiteOntologyActionView;
  targetObject: GenericObject;
  onApplied: () => void;
  onClose: () => void;
}) {
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
  // 첫 제출 시점의 payload(expectedObjectVersion + idempotency key 포함)를 고정 보관.
  // onApplied 이후 객체가 새 버전으로 reload되어도 재제출은 동일 payload를 재전송해
  // 백엔드 idempotent replay evidence를 그대로 받는다 (409 CONFLICT 방지).
  const [pinnedRequest, setPinnedRequest] =
    useState<PinnedActionRequest | null>(null);
  const [runEvidence, setRunEvidence] = useState<ActionRunEvidence | null>(
    null,
  );
  const [successRequestId, setSuccessRequestId] = useState<string | null>(null);
  const [isBlockedByFailedValidation, setIsBlockedByFailedValidation] =
    useState(false);

  const runValidate = useCallback(async () => {
    const actionType = form.runtimeActionType as OsdkActionType | null;
    if (!actionType) return null;
    const payload = {
      objectId: targetObject.objectId,
      expectedObjectVersion: targetObject.objectVersion,
      params: form.params,
    } as OsdkActionValidationPayload<OsdkActionType>;
    return osdk(actionType).validateAction(payload);
  }, [form.runtimeActionType, form.params, osdk, targetObject]);

  const validateMutation = useFoundryLiteMutation(runValidate, {
    onSuccess: (result) => {
      if (!result) return;
      setValidation(result);
      setIsBlockedByFailedValidation(result.result !== "VALID");
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
        setRunEvidence(result as ActionRunEvidence);
        onApplied();
      },
      onError: (error) => {
        // 재시도 가능(네트워크/5xx) 에러는 동일 payload 재전송이 안전하므로 고정 유지.
        // 그 외(검증 실패 등)는 키가 이미 소모되었으므로 새 키로 재구성하게 한다.
        if (error.retryable) return;
        if (isStaleObjectVersionError(error)) return;
        if (error.code === "VALIDATION_FAILED") {
          setIsBlockedByFailedValidation(true);
        }
        setPinnedRequest(null);
        form.setIdempotencyKey(issueIdempotencyKey());
      },
    },
  );

  // 성공 패널 request_id는 성공한 액션 apply 응답의 값으로 고정한다.
  // 액션 apply 경로 응답만 관찰하므로 onApplied가 촉발한 reload 응답이 덮어쓰지 못한다.
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
    setRunEvidence(null);
    setSuccessRequestId(null);
    setIsBlockedByFailedValidation(false);
    form.setIdempotencyKey(issueIdempotencyKey());
    onApplied();
  };

  const handleParameterChange = (fieldName: string, value: unknown) => {
    form.setParam(fieldName, value);
    setRunEvidence(null);
  };

  const result = runEvidence;
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
      {form.parameterFields.filter((field) => field.isVisible).map((field) => (
        <div key={field.name} className="space-y-1">
          <Label className="text-xs">
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
            onChange={(value) => handleParameterChange(field.name, value)}
          />
          {field.description ? (
            <p className="text-[11px] text-muted-foreground">
              {field.description}
            </p>
          ) : null}
        </div>
      ))}
      {validation ? <ValidationResultPanel result={validation} /> : null}
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
      {result ? (
        <div className="space-y-1 rounded border border-success/40 bg-success/5 p-2">
          <div className="flex items-center gap-2">
            <StatusPill intent="success">
              {result.status ?? "적용됨"}
            </StatusPill>
            {result.idempotentReplay ? (
              <StatusPill intent="info">idempotent replay</StatusPill>
            ) : null}
          </div>
          <div className="space-y-0.5 font-mono text-[11px] text-muted-foreground">
            {result.actionRunId ? <div>run_id={result.actionRunId}</div> : null}
            {result.objectEditId ? (
              <div>edit_id={result.objectEditId}</div>
            ) : null}
            {result.newObjectVersion !== undefined ? (
              <div>new_version=v{result.newObjectVersion}</div>
            ) : null}
            {successRequestId ? <div>request_id={successRequestId}</div> : null}
          </div>
        </div>
      ) : null}
      {form.disabledReason && !result ? (
        <p className="text-[11px] text-muted-foreground">
          {form.disabledReason}
        </p>
      ) : null}
      {isBlockedByFailedValidation && !result ? (
        <p className="text-[11px] text-destructive">
          사전 검증 또는 서버 검증에 실패했습니다. 입력/대상 상태를 고친 뒤
          사전 검증을 다시 통과해야 실행할 수 있습니다.
        </p>
      ) : null}
      <div className="flex items-center justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={onClose}>
          닫기
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={!form.runtimeActionType || validateMutation.isRunning}
          onClick={() => void validateMutation.execute(undefined)}
        >
          <ShieldCheck />
          사전 검증
        </Button>
        {result ? (
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
            validateMutation.isRunning ||
            isBlockedByFailedValidation ||
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

/** 액션 폼 다이얼로그: 파라미터 입력 + idempotency/버전 증거 + 검증/실행. */
export function ActionFormDialog({
  actionView,
  targetObject,
  isOpen,
  onOpenChange,
  onApplied,
}: ActionFormDialogProps) {
  return (
    <Dialog open={isOpen} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle className="text-sm">
            {actionView?.displayName ?? "액션 실행"}
          </DialogTitle>
          <DialogDescription className="text-xs">
            실행 전 사전 검증으로 대상 상태와 파라미터를 확인할 수 있습니다.
          </DialogDescription>
        </DialogHeader>
        {isOpen && actionView && targetObject ? (
          <ActionFormContent
            actionView={actionView}
            targetObject={targetObject}
            onApplied={onApplied}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}
