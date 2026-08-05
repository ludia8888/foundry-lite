import { Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
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

import {
  ACTION_BUILDER_PARAMETER_TYPES,
  newActionBuilderCondition,
  newActionBuilderFormSection,
  newActionBuilderParameter,
  newActionBuilderStructField,
  type ActionBuilderDraft,
  type ActionBuilderFormSection,
  type ActionBuilderParameter,
  type ActionBuilderPropertyOption,
  type ActionBuilderStructField,
} from "../lib/action-builder-model";
import {
  ActionBuilderParameterPolicyEditor,
  ConditionNodeEditor,
} from "./ActionBuilderPolicyEditors";

type ObjectProperties = ActionBuilderPropertyOption[];

export function ActionBuilderParameterEditor(props: {
  draft: ActionBuilderDraft;
  properties: ObjectProperties;
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const addParameter = () => {
    const parameter = newActionBuilderParameter(props.draft.parameters.length);
    const sections = props.draft.sections.map((section, index) =>
      index === 0 ? { ...section, parameterKeys: [...section.parameterKeys, parameter.key] } : section,
    );
    props.onChange({ ...props.draft, parameters: [...props.draft.parameters, parameter], sections });
  };
  return (
    <section className="space-y-3 rounded border bg-card p-3">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-sm font-semibold">파라미터와 폼</h2>
          <p className="text-[11px] text-muted-foreground">UI·SDK·MCP가 같은 typed schema와 조건부 섹션을 사용합니다.</p>
        </div>
        <Button size="sm" variant="outline" onClick={addParameter}><Plus />파라미터</Button>
      </div>
      {props.draft.parameters.map((parameter, index) => (
        <ParameterCard
          key={parameter.key}
          parameter={parameter}
          index={index}
          draft={props.draft}
          properties={props.properties}
          onChange={props.onChange}
        />
      ))}
      {props.draft.parameters.length === 0 ? (
        <p className="rounded border border-dashed p-4 text-center text-[11px] text-muted-foreground">파라미터가 없는 Action도 만들 수 있습니다.</p>
      ) : null}
      <FormSectionEditor {...props} />
    </section>
  );
}

function ParameterCard(props: {
  parameter: ActionBuilderParameter;
  index: number;
  draft: ActionBuilderDraft;
  properties: ObjectProperties;
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const update = (values: Partial<ActionBuilderParameter>) => props.onChange({
    ...props.draft,
    parameters: props.draft.parameters.map((item) =>
      item.key === props.parameter.key ? { ...item, ...values } : item,
    ),
  });
  const remove = () => props.onChange({
    ...props.draft,
    parameters: props.draft.parameters.filter((item) => item.key !== props.parameter.key),
    sections: props.draft.sections.map((section) => ({
      ...section,
      parameterKeys: section.parameterKeys.filter((key) => key !== props.parameter.key),
    })),
  });
  return (
    <div className="space-y-2 rounded border bg-muted/20 p-2">
      <div className="grid items-end gap-2 md:grid-cols-[1fr_150px_1fr_auto_auto]">
        <Field label="API name"><Input aria-label="파라미터 API name" value={props.parameter.apiName} onChange={(event) => update({ apiName: event.target.value })} placeholder="reason" /></Field>
        <Field label="타입"><ParameterTypeSelect value={props.parameter.dataType} onChange={(dataType) => update({ dataType })} /></Field>
        <Field label="설명"><Input aria-label="파라미터 설명" value={props.parameter.description} onChange={(event) => update({ description: event.target.value })} /></Field>
        <label className="flex h-9 items-center gap-1.5 text-[11px]"><Checkbox checked={props.parameter.isRequired} onCheckedChange={(value) => update({ isRequired: value === true })} />필수</label>
        <Button size="icon-sm" variant="ghost" aria-label="파라미터 삭제" onClick={remove}><Trash2 /></Button>
      </div>
      <ParameterTypeDetails parameter={props.parameter} onChange={update} />
      <ActionBuilderParameterPolicyEditor
        parameter={props.parameter}
        earlierParameters={props.draft.parameters.slice(0, props.index).map((item) => item.apiName).filter(Boolean)}
        properties={props.properties}
        onChange={(next) => update(next)}
      />
    </div>
  );
}

function ParameterTypeDetails(props: {
  parameter: ActionBuilderParameter;
  onChange: (values: Partial<ActionBuilderParameter>) => void;
}) {
  if (props.parameter.dataType === "media" || props.parameter.dataType === "attachment") {
    return <MediaParameterDetails value={props.parameter} onChange={props.onChange} />;
  }
  if (props.parameter.dataType === "object" || props.parameter.dataType === "interface") {
    return (
      <Field label={props.parameter.dataType === "object" ? "참조 Object Type" : "참조 Interface"}>
        <Input aria-label={`${props.parameter.apiName || "파라미터"} 참조 타입`} value={props.parameter.referenceType} onChange={(event) => props.onChange({ referenceType: event.target.value })} placeholder={props.parameter.dataType === "object" ? "Order" : "Asset"} />
      </Field>
    );
  }
  if (props.parameter.dataType === "array" || props.parameter.dataType === "objectSet") {
    return (
      <div className="space-y-2">
        <Field label="항목 타입">
          <Select value={props.parameter.itemType} onValueChange={(itemType) => props.onChange({ itemType })}>
            <SelectTrigger className="max-w-52"><SelectValue /></SelectTrigger>
            <SelectContent>{ACTION_BUILDER_PARAMETER_TYPES.filter((type) => !["array", "objectSet", "struct"].includes(type)).map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent>
          </Select>
        </Field>
        {["media", "attachment"].includes(props.parameter.itemType) ? (
          <MediaParameterDetails value={props.parameter} onChange={props.onChange} />
        ) : null}
      </div>
    );
  }
  if (props.parameter.dataType !== "struct") return null;
  return (
    <StructFieldsEditor
      path={props.parameter.apiName || "struct"}
      fields={props.parameter.fields}
      onChange={(fields) => props.onChange({ fields })}
    />
  );
}

type MediaConfiguration = Pick<
  ActionBuilderParameter,
  "mediaSet" | "allowedMimeTypes" | "maxBytes" | "render"
>;

function MediaParameterDetails(props: {
  value: MediaConfiguration;
  onChange: (values: Partial<MediaConfiguration>) => void;
}) {
  return (
    <div className="grid gap-2 rounded border border-dashed bg-background p-2 md:grid-cols-2">
      <Field label="Media Set (namespace.name)">
        <Input aria-label="파라미터 Media Set" value={props.value.mediaSet} onChange={(event) => props.onChange({ mediaSet: event.target.value })} placeholder="reservations.attachments" />
      </Field>
      <Field label="허용 MIME (쉼표 구분)">
        <Input aria-label="허용 MIME 타입" value={props.value.allowedMimeTypes} onChange={(event) => props.onChange({ allowedMimeTypes: event.target.value })} placeholder="image/*, application/pdf" />
      </Field>
      <Field label="최대 byte">
        <Input aria-label="미디어 최대 byte" type="number" min={1} value={props.value.maxBytes} onChange={(event) => props.onChange({ maxBytes: event.target.value })} placeholder="209715200" />
      </Field>
      <Field label="입력 방식">
        <Select value={props.value.render} onValueChange={(render) => props.onChange({ render: render as "filePicker" | "textInput" })}>
          <SelectTrigger aria-label="미디어 입력 방식"><SelectValue /></SelectTrigger>
          <SelectContent><SelectItem value="filePicker">파일 선택</SelectItem><SelectItem value="textInput">버전 ID 입력</SelectItem></SelectContent>
        </Select>
      </Field>
    </div>
  );
}

function FormSectionEditor(props: {
  draft: ActionBuilderDraft;
  properties: ObjectProperties;
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const addSection = () => props.onChange({
    ...props.draft,
    sections: [...props.draft.sections, newActionBuilderFormSection(props.draft.sections.length)],
  });
  return (
    <div className="space-y-2 border-t pt-3">
      <div className="flex items-start justify-between gap-3">
        <div><h3 className="text-xs font-semibold">폼 섹션</h3><p className="text-[10px] text-muted-foreground">여러 섹션, 1·2열, 접기, 입력값 기반 표시 조건을 정의합니다.</p></div>
        <Button size="sm" variant="outline" onClick={addSection}><Plus />섹션</Button>
      </div>
      {props.draft.sections.map((section, index) => (
        <SectionCard
          key={section.key}
          section={section}
          index={index}
          draft={props.draft}
          onChange={props.onChange}
        />
      ))}
    </div>
  );
}

function SectionCard(props: {
  section: ActionBuilderFormSection;
  index: number;
  draft: ActionBuilderDraft;
  onChange: (draft: ActionBuilderDraft) => void;
}) {
  const update = (values: Partial<ActionBuilderFormSection>) => props.onChange({
    ...props.draft,
    sections: props.draft.sections.map((item) => item.key === props.section.key ? { ...item, ...values } : item),
  });
  const assign = (parameterKey: string, isAssigned: boolean) => props.onChange({
    ...props.draft,
    sections: props.draft.sections.map((item) => ({
      ...item,
      parameterKeys: item.key === props.section.key && isAssigned
        ? [...new Set([...item.parameterKeys, parameterKey])]
        : item.parameterKeys.filter((key) => key !== parameterKey),
    })),
  });
  const remove = () => {
    const removedKeys = props.section.parameterKeys;
    const remaining = props.draft.sections.filter((item) => item.key !== props.section.key);
    if (remaining[0]) remaining[0] = { ...remaining[0], parameterKeys: [...new Set([...remaining[0].parameterKeys, ...removedKeys])] };
    props.onChange({ ...props.draft, sections: remaining });
  };
  const parameterNames = props.draft.parameters.map((parameter) => parameter.apiName).filter(Boolean);
  return (
    <div className="space-y-3 rounded border border-dashed bg-background/70 p-2">
      <div className="flex items-start justify-between gap-2">
        <div className="grid flex-1 gap-2 md:grid-cols-[1fr_1.5fr_110px]">
          <Field label="섹션 ID"><Input aria-label={`폼 섹션 ${props.index + 1} ID`} value={props.section.id} onChange={(event) => update({ id: event.target.value })} /></Field>
          <Field label="제목"><Input aria-label={`폼 섹션 ${props.index + 1} 제목`} value={props.section.title} onChange={(event) => update({ title: event.target.value })} /></Field>
          <Field label="열"><Select value={String(props.section.columns)} onValueChange={(value) => update({ columns: value === "2" ? 2 : 1 })}><SelectTrigger aria-label={`폼 섹션 ${props.index + 1} 열`}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="1">1열</SelectItem><SelectItem value="2">2열</SelectItem></SelectContent></Select></Field>
        </div>
        <Button size="icon-sm" variant="ghost" aria-label={`폼 섹션 ${props.index + 1} 삭제`} disabled={props.draft.sections.length === 1} onClick={remove}><Trash2 /></Button>
      </div>
      <Field label="설명"><Textarea rows={2} value={props.section.description} onChange={(event) => update({ description: event.target.value })} /></Field>
      <div className="flex flex-wrap gap-4 text-[11px]">
        <label className="flex items-center gap-1.5"><Checkbox checked={props.section.isCollapsible} onCheckedChange={(value) => update({ isCollapsible: value === true, isInitiallyCollapsed: value === true ? props.section.isInitiallyCollapsed : false })} />접기 허용</label>
        <label className="flex items-center gap-1.5"><Checkbox disabled={!props.section.isCollapsible} checked={props.section.isInitiallyCollapsed} onCheckedChange={(value) => update({ isInitiallyCollapsed: value === true })} />처음에는 접힘</label>
      </div>
      <div className="space-y-1"><Label className="text-[10px]">포함 파라미터</Label><div className="flex flex-wrap gap-2">{props.draft.parameters.map((parameter) => <label key={parameter.key} className="flex items-center gap-1 rounded border px-2 py-1 text-[10px]"><Checkbox checked={props.section.parameterKeys.includes(parameter.key)} onCheckedChange={(value) => assign(parameter.key, value === true)} />{parameter.apiName || "이름 미지정"}</label>)}</div></div>
      <div className="space-y-2 border-t pt-2">
        <div className="flex items-center justify-between"><span className="text-[10px] font-medium">표시 조건</span>{props.section.visibleWhen ? <Button size="sm" variant="ghost" onClick={() => update({ visibleWhen: null })}><Trash2 />조건 제거</Button> : <Button size="sm" variant="outline" disabled={!parameterNames.length} onClick={() => update({ visibleWhen: newActionBuilderCondition(`section-${props.index}`) })}><Plus />조건</Button>}</div>
        {props.section.visibleWhen ? <ConditionNodeEditor value={props.section.visibleWhen} parameters={parameterNames} properties={[]} isParameterOnly onChange={(visibleWhen) => update({ visibleWhen })} /> : <p className="text-[10px] text-muted-foreground">조건이 없으면 항상 표시됩니다.</p>}
      </div>
    </div>
  );
}

function StructFieldsEditor(props: {
  path: string;
  fields: ActionBuilderStructField[];
  onChange: (fields: ActionBuilderStructField[]) => void;
}) {
  const add = () => props.onChange([...props.fields, newActionBuilderStructField(props.fields.length)]);
  return (
    <div className="space-y-2 rounded border border-dashed p-2">
      <div className="flex items-center justify-between"><span className="text-[10px] font-medium">{props.path} 중첩 필드</span><Button size="sm" variant="outline" onClick={add}><Plus />필드</Button></div>
      {props.fields.map((field) => <StructFieldCard key={field.key} field={field} path={props.path} onChange={(next) => props.onChange(props.fields.map((item) => item.key === field.key ? next : item))} onDelete={() => props.onChange(props.fields.filter((item) => item.key !== field.key))} />)}
    </div>
  );
}

function StructFieldCard(props: { field: ActionBuilderStructField; path: string; onChange: (field: ActionBuilderStructField) => void; onDelete: () => void }) {
  const update = (values: Partial<ActionBuilderStructField>) => props.onChange({ ...props.field, ...values });
  const allowedTypes = ACTION_BUILDER_PARAMETER_TYPES.filter((type) => !["object", "interface", "objectSet", "array"].includes(type));
  return (
    <div className="space-y-2 rounded bg-muted/30 p-2">
      <div className="grid items-end gap-2 md:grid-cols-[1fr_140px_1fr_auto_auto]">
        <Field label="필드 API name"><Input aria-label={`${props.path} struct 필드 API name`} value={props.field.apiName} onChange={(event) => update({ apiName: event.target.value })} /></Field>
        <Field label="타입"><Select value={props.field.dataType} onValueChange={(dataType) => update({ dataType })}><SelectTrigger aria-label={`${props.path} struct 필드 타입`}><SelectValue /></SelectTrigger><SelectContent>{allowedTypes.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select></Field>
        <Field label="설명"><Input aria-label={`${props.path} struct 필드 설명`} value={props.field.description} onChange={(event) => update({ description: event.target.value })} /></Field>
        <label className="flex h-9 items-center gap-1 text-[10px]"><Checkbox checked={props.field.isRequired} onCheckedChange={(value) => update({ isRequired: value === true })} />필수</label>
        <Button size="icon-sm" variant="ghost" aria-label="struct 필드 삭제" onClick={props.onDelete}><Trash2 /></Button>
      </div>
      {props.field.dataType === "struct" ? <StructFieldsEditor path={`${props.path}.${props.field.apiName || "field"}`} fields={props.field.fields} onChange={(fields) => update({ fields })} /> : null}
      {props.field.dataType === "media" || props.field.dataType === "attachment" ? (
        <MediaParameterDetails value={props.field} onChange={update} />
      ) : null}
    </div>
  );
}

function ParameterTypeSelect(props: { value: string; onChange: (value: string) => void }) {
  return <Select value={props.value} onValueChange={props.onChange}><SelectTrigger aria-label="파라미터 타입"><SelectValue /></SelectTrigger><SelectContent>{ACTION_BUILDER_PARAMETER_TYPES.map((type) => <SelectItem key={type} value={type}>{type}</SelectItem>)}</SelectContent></Select>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="space-y-1"><Label className="text-[10px]">{label}</Label>{children}</div>;
}
