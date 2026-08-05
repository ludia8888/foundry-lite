import type {
  FoundryLiteActionFormSection,
  FoundryLiteActionParameterField,
} from "@foundry-lite/sdk/react";
import { ChevronDown } from "lucide-react";

import { Label } from "@/components/ui/label";

import { ParameterInput } from "./ParameterInput";

interface ActionParameterSectionProps {
  section: FoundryLiteActionFormSection;
  onChange: (field: FoundryLiteActionParameterField, value: unknown) => void;
  onUpload: (field: FoundryLiteActionParameterField, file: File) => Promise<unknown>;
}

export function ActionParameterSection({
  section,
  onChange,
  onUpload,
}: ActionParameterSectionProps) {
  if (!section.isVisible) return null;
  const fields = section.fields.filter((field) => field.isVisible);
  if (fields.length === 0) return null;
  const content = (
    <div
      className={
        section.columns === 2
          ? "grid gap-3 pt-3 md:grid-cols-2"
          : "grid gap-3 pt-3"
      }
    >
      {fields.map((field) => (
        <ActionParameterField
          key={field.name}
          field={field}
          onChange={(value) => onChange(field, value)}
          onUpload={onUpload}
        />
      ))}
    </div>
  );
  if (!section.isCollapsible) {
    return (
      <section className="rounded border bg-card p-3" aria-labelledby={`${section.id}-title`}>
        <SectionHeading section={section} />
        {content}
      </section>
    );
  }
  return (
    <details
      className="group rounded border bg-card p-3"
      open={!section.isInitiallyCollapsed}
    >
      <summary className="flex cursor-pointer list-none items-start justify-between gap-3">
        <SectionHeading section={section} />
        <ChevronDown className="mt-0.5 size-4 text-muted-foreground transition-transform group-open:rotate-180" />
      </summary>
      {content}
    </details>
  );
}

function SectionHeading({ section }: { section: FoundryLiteActionFormSection }) {
  return (
    <div className="min-w-0">
      <h3 id={`${section.id}-title`} className="text-xs font-semibold">
        {section.title}
      </h3>
      {section.description ? (
        <p className="mt-0.5 text-[11px] text-muted-foreground">
          {section.description}
        </p>
      ) : null}
    </div>
  );
}

function ActionParameterField({
  field,
  onChange,
  onUpload,
}: {
  field: FoundryLiteActionParameterField;
  onChange: (value: unknown) => void;
  onUpload: (field: FoundryLiteActionParameterField, file: File) => Promise<unknown>;
}) {
  return (
    <div className="min-w-0 space-y-1">
      <Label className="flex items-center gap-1 text-xs">
        {field.label}
        {field.isRequired ? <span className="text-destructive">*</span> : null}
        <span className="font-mono text-[10px] text-muted-foreground">
          {field.dataType}
        </span>
        {field.matchedOverride !== null ? (
          <span className="rounded bg-primary/10 px-1 py-0.5 text-[10px] font-medium text-primary">
            override {field.matchedOverride + 1} 적용
          </span>
        ) : null}
      </Label>
      <ParameterInput field={field} onChange={onChange} onUpload={onUpload} />
      {field.description ? (
        <p className="text-[11px] text-muted-foreground">{field.description}</p>
      ) : null}
      {field.constraintError ? (
        <p className="text-[11px] text-destructive">제약조건 불충족: {field.constraintError}</p>
      ) : null}
    </div>
  );
}
