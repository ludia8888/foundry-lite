import type { FoundryLiteActionParameterField } from "@foundry-lite/sdk/react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

interface ParameterInputProps {
  field: FoundryLiteActionParameterField;
  onChange: (value: unknown) => void;
}

/** 파라미터 schema의 inputKind에 맞춘 컨트롤 (checkbox/number/json/text). */
export function ParameterInput({ field, onChange }: ParameterInputProps) {
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
      className="h-8 text-xs"
      value={typeof field.value === "string" ? field.value : ""}
      onChange={(event) => onChange(event.target.value)}
      placeholder={field.label}
    />
  );
}
