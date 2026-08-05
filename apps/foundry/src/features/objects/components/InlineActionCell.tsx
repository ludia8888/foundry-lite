import {
  idempotencyKey as createIdempotencyKey,
  type GenericObject,
  type OsdkActionPayload,
  type OsdkActionType,
} from "@foundry-lite/sdk";
import {
  foundryLiteActionFormView,
  useFoundryLiteMutation,
  useFoundryLiteOsdkClient,
  type FoundryLiteOntologyActionView,
  type FoundryLiteOntologyPropertyView,
} from "@foundry-lite/sdk/react";
import { Check, Pencil, X } from "lucide-react";
import { useState, type MouseEvent, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type InlineActionBinding = {
  propertyApiName: string;
  parameterApiName: string;
  parameterType: string;
};

type InlineApplyRequest = {
  actionType: OsdkActionType;
  payload: OsdkActionPayload<OsdkActionType>;
};

interface InlineActionCellProps {
  actionView: FoundryLiteOntologyActionView;
  binding: InlineActionBinding;
  object: GenericObject;
  property: FoundryLiteOntologyPropertyView;
  children: ReactNode;
  onApplied: () => void;
}

/** Canonical schema에 서버가 봉인한 단일 셀 Action binding만 신뢰한다. */
export function inlineActionBinding(
  actionView: FoundryLiteOntologyActionView,
): InlineActionBinding | null {
  const raw = actionView.action.parameterSchema["x-foundry-inline-eligibility"];
  if (!isRecord(raw) || raw.isEligible !== true) return null;
  const { propertyApiName, parameterApiName, parameterType } = raw;
  if (
    typeof propertyApiName !== "string" ||
    typeof parameterApiName !== "string" ||
    typeof parameterType !== "string"
  ) {
    return null;
  }
  return { propertyApiName, parameterApiName, parameterType };
}

export function InlineActionCell({
  actionView,
  binding,
  object,
  property,
  children,
  onApplied,
}: InlineActionCellProps) {
  const osdk = useFoundryLiteOsdkClient();
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(() => draftValue(object.properties[property.apiName]));
  const mutation = useFoundryLiteMutation(
    (request: InlineApplyRequest) => osdk(request.actionType).applyAction(request.payload),
    {
      onSuccess: () => {
        setIsEditing(false);
        onApplied();
      },
    },
  );

  const stop = (event: MouseEvent) => event.stopPropagation();
  const cancel = (event: MouseEvent) => {
    stop(event);
    setDraft(draftValue(object.properties[property.apiName]));
    setIsEditing(false);
  };
  const save = (event: MouseEvent) => {
    stop(event);
    const request = inlineApplyRequest(actionView, binding, object, draft);
    if (request) void mutation.execute(request);
  };

  if (!isEditing) {
    return (
      <div className="group/inline flex min-w-24 items-center gap-1" onClick={stop}>
        <span className="min-w-0 flex-1 truncate">{children}</span>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-6 opacity-0 group-hover/inline:opacity-100 focus-visible:opacity-100"
          aria-label={`인라인 편집: ${property.displayName} · ${object.objectId}`}
          title={`${actionView.displayName} Action으로 편집`}
          onClick={(event) => {
            stop(event);
            setDraft(draftValue(object.properties[property.apiName]));
            setIsEditing(true);
          }}
        >
          <Pencil className="size-3" />
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-w-56 items-center gap-1" onClick={stop}>
      <InlineValueInput
        ariaLabel={`${property.displayName} 새 값`}
        parameterType={binding.parameterType}
        value={draft}
        onChange={setDraft}
      />
      <Button
        type="button"
        size="icon"
        variant="outline"
        className="size-7"
        aria-label={`인라인 저장: ${property.displayName} · ${object.objectId}`}
        disabled={mutation.isRunning}
        onClick={save}
      >
        <Check className="size-3" />
      </Button>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className="size-7"
        aria-label={`인라인 취소: ${property.displayName} · ${object.objectId}`}
        disabled={mutation.isRunning}
        onClick={cancel}
      >
        <X className="size-3" />
      </Button>
      {mutation.error ? (
        <span className="max-w-40 truncate text-[10px] text-destructive" title={mutation.error.message}>
          {mutation.error.code} · 새로고침 후 재시도
        </span>
      ) : null}
    </div>
  );
}

function InlineValueInput({
  ariaLabel,
  parameterType,
  value,
  onChange,
}: {
  ariaLabel: string;
  parameterType: string;
  value: string;
  onChange: (value: string) => void;
}) {
  if (parameterType === "boolean") {
    return (
      <select
        aria-label={ariaLabel}
        className="h-7 flex-1 rounded border bg-background px-2 text-xs"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  return (
    <Input
      autoFocus
      aria-label={ariaLabel}
      className="h-7 flex-1 px-2 text-xs"
      type={inputType(parameterType)}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

function inlineApplyRequest(
  actionView: FoundryLiteOntologyActionView,
  binding: InlineActionBinding,
  object: GenericObject,
  draft: string,
): InlineApplyRequest | null {
  const form = foundryLiteActionFormView(actionView, {
    targetObject: object,
    params: { [binding.parameterApiName]: typedDraft(draft, binding.parameterType) },
    idempotencyKey: createIdempotencyKey(actionView.apiName, object.objectId),
    requireIdempotencyKey: true,
  });
  if (!form.runtimeActionType || !form.payload) return null;
  return { actionType: form.runtimeActionType, payload: form.payload };
}

function typedDraft(value: string, parameterType: string): unknown {
  if (["integer", "long", "float", "decimal"].includes(parameterType)) return Number(value);
  if (parameterType === "boolean") return value === "true";
  return value;
}

function draftValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
}

function inputType(parameterType: string): "date" | "datetime-local" | "number" | "text" {
  if (parameterType === "date") return "date";
  if (parameterType === "timestamp") return "datetime-local";
  if (["integer", "long", "float", "decimal"].includes(parameterType)) return "number";
  return "text";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
