import type { FoundryLiteActionParameterField } from "@foundry-lite/sdk/react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";

interface ParameterInputProps {
  field: FoundryLiteActionParameterField;
  onChange: (value: unknown) => void;
}

/** 파라미터 schema의 inputKind에 맞춘 컨트롤 (checkbox/number/json/text). */
export function ParameterInput({ field, onChange }: ParameterInputProps) {
  if (field.inputKind === "select") {
    return (
      <Select
        disabled={!field.isEditable}
        value={field.hasValue ? String(field.value) : undefined}
        onValueChange={onChange}
      >
        <SelectTrigger className="h-8 text-xs">
          <SelectValue placeholder={`${field.label} 선택`} />
        </SelectTrigger>
        <SelectContent>
          {field.options.map((option) => (
            <SelectItem key={String(option)} value={String(option)}>
              {String(option)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  if (field.inputKind === "checkbox") {
    return (
      <Checkbox
        checked={field.value === true}
        disabled={!field.isEditable}
        onCheckedChange={(checked) => onChange(checked === true)}
      />
    );
  }
  if (field.inputKind === "number") {
    return (
      <Input
        type="number"
        disabled={!field.isEditable}
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
        disabled={!field.isEditable}
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
      type={field.inputKind === "date" ? "date" : field.inputKind === "datetime" ? "datetime-local" : "text"}
      disabled={!field.isEditable}
      className="h-8 text-xs"
      value={typeof field.value === "string" ? field.value : ""}
      onChange={(event) => onChange(event.target.value)}
      placeholder={field.label}
    />
  );
}
