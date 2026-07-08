import type { OntologyDraftProperty } from "@foundry-lite/sdk/ontology-draft";
import { Code2, KeyRound, LayoutList, Trash2, X } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

import { ALLOWED_PROPERTY_TYPES } from "../lib/object-type-draft";

const EDIT_POLICIES = ["edit_wins", "edit_only"] as const;

interface PropertyEditorFormProps {
  property: OntologyDraftProperty;
  /** 데이터소스 컬럼 후보 (Backing column Select). */
  columnNames: string[];
  isPrimaryKey: boolean;
  isTitle: boolean;
  isEditable: boolean;
  onUpdate: (patch: Partial<Omit<OntologyDraftProperty, "apiName">>) => void;
  onSetPrimaryKey: () => void;
  onSetTitle: (isTitle: boolean) => void;
  onRemove: () => void;
  onClose: () => void;
}

/**
 * 속성 편집 폼 (Form/JSON 토글). Display name/타입/토글 등 필드는
 * updateOntologyDraftProperty로 매핑된다. JSON 탭은 escape hatch.
 */
export function PropertyEditorForm({
  property,
  columnNames,
  isPrimaryKey,
  isTitle,
  isEditable,
  onUpdate,
  onSetPrimaryKey,
  onSetTitle,
  onRemove,
  onClose,
}: PropertyEditorFormProps) {
  const [mode, setMode] = useState<"form" | "json">("form");

  return (
    <div className="w-[360px] shrink-0 rounded border bg-card">
      <div className="flex h-11 items-center gap-2 px-3">
        <span className="min-w-0 flex-1 truncate text-[13px] font-semibold">
          {property.displayName ?? property.apiName}
        </span>
        <div className="flex items-center rounded bg-muted/60 p-0.5">
          <button
            type="button"
            onClick={() => setMode("form")}
            className={cn(
              "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]",
              mode === "form"
                ? "bg-card font-medium text-foreground shadow-sm"
                : "text-muted-foreground",
            )}
          >
            <LayoutList className="size-3" />
            Form
          </button>
          <button
            type="button"
            onClick={() => setMode("json")}
            className={cn(
              "flex items-center gap-1 rounded px-1.5 py-0.5 text-[11px]",
              mode === "json"
                ? "bg-card font-medium text-foreground shadow-sm"
                : "text-muted-foreground",
            )}
          >
            <Code2 className="size-3" />
            {"</>"} JSON
          </button>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="속성 편집 닫기"
          className="text-muted-foreground hover:text-foreground"
        >
          <X className="size-4" />
        </button>
      </div>
      <Separator />

      {mode === "form" ? (
        <div className="space-y-3 p-3">
          <Field label="Display name">
            <Input
              value={property.displayName ?? ""}
              disabled={!isEditable}
              onChange={(event) =>
                onUpdate({ displayName: event.target.value || null })
              }
              className="h-8 text-xs"
            />
          </Field>
          <Field label="API name">
            <div className="flex items-center gap-1.5 font-mono text-[11px] text-muted-foreground">
              {property.apiName}
            </div>
          </Field>
          <Field label="Property type">
            <Select
              value={property.type}
              disabled={!isEditable}
              onValueChange={(value) => onUpdate({ type: value })}
            >
              <SelectTrigger size="sm" className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {ALLOWED_PROPERTY_TYPES.map((type) => (
                  <SelectItem key={type} value={type} className="text-xs">
                    {type}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Backing column">
            <Select
              value={property.column ?? "__none__"}
              disabled={!isEditable}
              onValueChange={(value) =>
                onUpdate({ column: value === "__none__" ? null : value })
              }
            >
              <SelectTrigger size="sm" className="h-8 text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" className="text-xs">
                  derived / edit-layer
                </SelectItem>
                {columnNames.map((column) => (
                  <SelectItem
                    key={column}
                    value={column}
                    className="font-mono text-xs"
                  >
                    {column}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>

          <Separator />

          <ToggleRow
            label="Primary key"
            checked={isPrimaryKey}
            disabled={!isEditable}
            onChange={() => onSetPrimaryKey()}
          />
          <ToggleRow
            label="Title property"
            checked={isTitle}
            disabled={!isEditable}
            onChange={(value) => onSetTitle(value)}
          />
          <ToggleRow
            label="Indexed"
            checked={property.indexed === true}
            disabled={!isEditable}
            onChange={(value) => onUpdate({ indexed: value })}
          />
          <ToggleRow
            label="Nullable"
            checked={property.nullable === true}
            disabled={!isEditable}
            onChange={(value) => onUpdate({ nullable: value })}
          />
          <ToggleRow
            label="Searchable"
            checked={property.searchable === true}
            disabled={!isEditable}
            onChange={(value) => onUpdate({ searchable: value })}
          />
          <ToggleRow
            label="Editable"
            checked={property.editable === true}
            disabled={!isEditable}
            onChange={(value) =>
              onUpdate({
                editable: value,
                editPolicy: value ? (property.editPolicy ?? "edit_wins") : null,
              })
            }
          />
          {property.editable ? (
            <Field label="Edit policy">
              <Select
                value={property.editPolicy ?? "edit_wins"}
                disabled={!isEditable}
                onValueChange={(value) => onUpdate({ editPolicy: value })}
              >
                <SelectTrigger size="sm" className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EDIT_POLICIES.map((policy) => (
                    <SelectItem key={policy} value={policy} className="text-xs">
                      {policy}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          ) : null}
          <Field label="Classification">
            <Input
              value={property.classification ?? ""}
              disabled={!isEditable}
              placeholder="예: finance"
              onChange={(event) =>
                onUpdate({ classification: event.target.value || null })
              }
              className="h-8 text-xs"
            />
          </Field>

          <Separator />
          <div className="flex items-center gap-2">
            {isPrimaryKey ? (
              <span className="inline-flex items-center gap-1 rounded bg-[#e6e1f5] px-1.5 py-0.5 text-[10px] font-medium text-[#5b4a9e]">
                <KeyRound className="size-3" />
                Primary key
              </span>
            ) : null}
            <Button
              size="sm"
              variant="outline"
              disabled={!isEditable || isPrimaryKey}
              onClick={onRemove}
              className="ml-auto text-destructive hover:text-destructive"
            >
              <Trash2 />
              삭제
            </Button>
          </div>
          {isPrimaryKey ? (
            <p className="text-[11px] text-muted-foreground">
              기본 키 속성은 삭제할 수 없습니다. 먼저 다른 속성을 기본 키로
              지정하세요.
            </p>
          ) : null}
        </div>
      ) : (
        <div className="space-y-2 p-3">
          <p className="text-[11px] text-muted-foreground">
            이 속성 조각의 raw JSON입니다. 폼 편집이 우선이며, JSON 탭은
            읽기용입니다.
          </p>
          <Textarea
            readOnly
            value={JSON.stringify(property, null, 2)}
            className="min-h-64 font-mono text-[11px]"
            spellCheck={false}
          />
        </div>
      )}
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <Label className="text-[11px] text-[#9da4af]">{label}</Label>
      {children}
    </div>
  );
}

function ToggleRow({
  label,
  checked,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  disabled: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-xs">{label}</span>
      <Switch
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
      />
    </div>
  );
}
