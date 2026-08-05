import type { FoundryLiteActionParameterField } from "@foundry-lite/sdk/react";
import { useEffect, useState } from "react";

import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
  onUpload?: (field: FoundryLiteActionParameterField, file: File) => Promise<unknown>;
}

/** 파라미터 schema의 inputKind에 맞춘 컨트롤 (checkbox/number/json/text). */
export function ParameterInput({ field, onChange, onUpload }: ParameterInputProps) {
  const parameterConfig = recordValue(field.schema["x-foundry-parameter-config"]);
  const itemType = typeof parameterConfig.itemType === "string" ? parameterConfig.itemType : null;
  const isMediaList = ["array", "objectSet"].includes(field.dataType)
    && (itemType === "media" || itemType === "attachment");
  const isFilePicker = parameterConfig.render !== "textInput";
  if (field.dataType === "struct") {
    return <StructParameterInput field={field} onChange={onChange} onUpload={onUpload} />;
  }
  if (field.inputKind === "select") {
    return (
      <Select
        disabled={!field.isEditable}
        value={field.hasValue ? String(field.value) : undefined}
        onValueChange={onChange}
      >
        <SelectTrigger className="h-8 text-xs" aria-label={field.label}>
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
  if ((field.inputKind === "media" || field.inputKind === "attachment" || isMediaList) && isFilePicker) {
    return <FileParameterInput field={field} onChange={onChange} onUpload={onUpload} isMultiple={isMediaList} />;
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
        min={numberConstraint(field.schema.minimum)}
        max={numberConstraint(field.schema.maximum)}
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
    return <JsonParameterInput field={field} onChange={onChange} />;
  }
  return (
    <Input
      type={field.inputKind === "date" ? "date" : field.inputKind === "datetime" ? "datetime-local" : "text"}
      minLength={integerConstraint(field.schema.minLength)}
      maxLength={integerConstraint(field.schema.maxLength)}
      disabled={!field.isEditable}
      className="h-8 text-xs"
      value={typeof field.value === "string" ? field.value : ""}
      onChange={(event) => onChange(event.target.value)}
      placeholder={field.label}
    />
  );
}

function FileParameterInput({
  field,
  onChange,
  onUpload,
  isMultiple,
}: ParameterInputProps & { isMultiple: boolean }) {
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const reference = recordValue(field.value);
  const config = recordValue(field.schema["x-foundry-parameter-config"]);
  const allowed = Array.isArray(config.allowedMimeTypes)
    ? config.allowedMimeTypes.filter((value): value is string => typeof value === "string").join(",")
    : undefined;
  return (
    <div className="space-y-1">
      <Input
        type="file"
        multiple={isMultiple}
        accept={allowed}
        disabled={!field.isEditable || isUploading || !onUpload}
        className="h-9 text-xs"
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          if (!files.length || !onUpload) return;
          setIsUploading(true);
          setError(null);
          void Promise.all(files.map((file) => onUpload(field, file)))
            .then((references) => onChange(isMultiple ? references : references[0]))
            .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "업로드에 실패했습니다."))
            .finally(() => setIsUploading(false));
        }}
      />
      {isUploading ? <p className="text-[10px] text-muted-foreground">불변 버전으로 업로드 중...</p> : null}
      {typeof reference.logicalPath === "string" ? (
        <p className="truncate text-[10px] text-emerald-700">연결됨: {reference.logicalPath}</p>
      ) : Array.isArray(field.value) ? (
        <p className="text-[10px] text-emerald-700">{field.value.length}개 불변 파일 참조 연결됨</p>
      ) : null}
      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}

function StructParameterInput({ field, onChange, onUpload }: ParameterInputProps) {
  const properties = recordValue(field.schema.properties);
  const required = new Set(
    Array.isArray(field.schema.required)
      ? field.schema.required.filter((name): name is string => typeof name === "string")
      : [],
  );
  const current = recordValue(field.value);
  if (Object.keys(properties).length === 0) {
    return <JsonParameterInput field={field} onChange={onChange} />;
  }
  return (
    <div className="space-y-2 rounded border border-dashed bg-muted/20 p-2">
      {Object.entries(properties).map(([name, schema]) => {
        const child = childParameterField(
          name,
          schema,
          current,
          required,
          field.isEditable,
          field.parameterPath,
        );
        return (
          <div key={name} className="space-y-1">
            <Label className="flex items-center gap-1 text-[10px]">
              {child.label}
              {child.isRequired ? <span className="text-destructive">*</span> : null}
              <span className="font-mono text-[9px] text-muted-foreground">{child.dataType}</span>
            </Label>
            <ParameterInput
              field={child}
              onChange={(value) => onChange({ ...current, [name]: value })}
              onUpload={onUpload}
            />
            {child.description ? <p className="text-[10px] text-muted-foreground">{child.description}</p> : null}
          </div>
        );
      })}
    </div>
  );
}

function JsonParameterInput({ field, onChange }: ParameterInputProps) {
  const [text, setText] = useState(() => printableJsonValue(field));
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    setText(printableJsonValue(field));
    setError(null);
  }, [field.value, field.dataType]);
  return (
    <div className="space-y-1">
      <Textarea
        className="min-h-16 font-mono text-[11px]"
        disabled={!field.isEditable}
        value={text}
        onChange={(event) => {
          const next = event.target.value;
          setText(next);
          try {
            onChange(JSON.parse(next) as unknown);
            setError(null);
          } catch {
            setError("유효한 JSON을 입력하세요. 저장 전까지 잘못된 문자열은 제출되지 않습니다.");
          }
        }}
        aria-invalid={error !== null}
        placeholder="JSON 값"
      />
      {error ? <p className="text-[10px] text-destructive">{error}</p> : null}
    </div>
  );
}

function childParameterField(
  name: string,
  rawSchema: unknown,
  values: Record<string, unknown>,
  required: Set<string>,
  isEditable: boolean,
  parentPath: string,
): FoundryLiteActionParameterField {
  const schema = recordValue(rawSchema);
  const config = recordValue(schema["x-foundry-parameter-config"]);
  const dataType = typeof config.type === "string" ? config.type : schemaDataType(schema);
  const value = values[name];
  return {
    name,
    parameterPath: `${parentPath}.${name}`,
    label: typeof schema.title === "string" ? schema.title : name,
    description: typeof schema.description === "string" ? schema.description : null,
    dataType,
    inputKind: inputKind(dataType, schema),
    isRequired: required.has(name),
    isVisible: true,
    isEditable,
    hasServerDefault: false,
    matchedOverride: null,
    options: Array.isArray(schema.enum) ? schema.enum : [],
    schema,
    value,
    hasValue: value !== null && value !== undefined && value !== "",
    constraintError: parameterConstraintError(name, value, schema),
  };
}

function parameterConstraintError(name: string, value: unknown, schema: Record<string, unknown>): string | null {
  if (value === null || value === undefined || value === "") return null;
  if (Array.isArray(schema.enum) && !schema.enum.some((item) => Object.is(item, value))) return `${name}: enum`;
  if (typeof value === "string" && typeof schema.minLength === "number" && value.length < schema.minLength) return `${name}: minLength`;
  if (typeof value === "string" && typeof schema.maxLength === "number" && value.length > schema.maxLength) return `${name}: maxLength`;
  if (typeof value === "number" && typeof schema.minimum === "number" && value < schema.minimum) return `${name}: minimum`;
  if (typeof value === "number" && typeof schema.maximum === "number" && value > schema.maximum) return `${name}: maximum`;
  if (Array.isArray(value) && typeof schema.minItems === "number" && value.length < schema.minItems) return `${name}: minItems`;
  if (Array.isArray(value) && typeof schema.maxItems === "number" && value.length > schema.maxItems) return `${name}: maxItems`;
  return null;
}

function numberConstraint(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}

function integerConstraint(value: unknown): number | undefined {
  return typeof value === "number" && Number.isSafeInteger(value) && value >= 0 ? value : undefined;
}

function schemaDataType(schema: Record<string, unknown>): string {
  if (schema.type === "object") return "struct";
  if (schema.type === "array") return "array";
  if (schema.format === "date") return "date";
  if (schema.format === "date-time") return "timestamp";
  return typeof schema.type === "string" ? schema.type : "unknown";
}

function inputKind(
  dataType: string,
  schema: Record<string, unknown>,
): FoundryLiteActionParameterField["inputKind"] {
  if (Array.isArray(schema.enum)) return "select";
  if (dataType === "date") return "date";
  if (dataType === "timestamp") return "datetime";
  if (dataType === "media") return "media";
  if (dataType === "attachment") return "attachment";
  if (["integer", "long", "float", "decimal", "number"].includes(dataType)) return "number";
  if (dataType === "boolean") return "checkbox";
  if (["struct", "array", "objectSet", "unknown"].includes(dataType)) return "json";
  return "text";
}

function printableJsonValue(field: FoundryLiteActionParameterField): string {
  const fallback = field.dataType === "array" || field.dataType === "objectSet" ? [] : {};
  return JSON.stringify(field.value ?? fallback, null, 2);
}

function recordValue(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}
