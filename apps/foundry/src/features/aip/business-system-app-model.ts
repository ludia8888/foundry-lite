import type {
  AipPilotOperatingApplicationBundle,
  BusinessSystemComponent,
  BusinessSystemDefinition,
  BusinessSystemScreen,
  GenericObject,
} from "@foundry-lite/sdk";

export type BusinessRecord = {
  apiName: string;
  displayName: string;
  primaryKey: string;
  fields: Array<{ apiName: string; displayName: string }>;
};

export type BusinessAction = {
  apiName: string;
  displayName: string;
  fromStates: string[];
  toState: string;
  requiresApproval: boolean;
  parameters: Array<{ apiName: string; displayName: string }>;
};

export type BusinessPolicy = { name: string; statement: string };

export function definition(bundle: AipPilotOperatingApplicationBundle): BusinessSystemDefinition {
  return bundle.businessSystemDefinition;
}

export function primaryRecord(value: BusinessSystemDefinition): BusinessRecord {
  return records(value)[0] ?? { apiName: "", displayName: "업무", primaryKey: "id", fields: [] };
}

export function records(value: BusinessSystemDefinition): BusinessRecord[] {
  return objectItems(model(value).records).map((item) => ({
    apiName: text(item.apiName),
    displayName: text(item.displayName),
    primaryKey: text(item.primaryKey),
    fields: objectItems(item.fields).map((field) => ({
      apiName: text(field.apiName),
      displayName: text(field.displayName),
    })),
  }));
}

export function actions(value: BusinessSystemDefinition): BusinessAction[] {
  return objectItems(model(value).actions).map((item) => ({
    apiName: text(item.apiName),
    displayName: text(item.displayName),
    fromStates: textItems(item.fromStates),
    toState: text(item.toState),
    requiresApproval: item.requiresApproval === true,
    parameters: objectItems(item.parameters).map((parameter) => ({
      apiName: text(parameter.apiName),
      displayName: text(parameter.displayName),
    })),
  }));
}

export function policies(value: BusinessSystemDefinition): BusinessPolicy[] {
  return objectItems(model(value).policies).map((item) => ({
    name: text(item.name),
    statement: text(item.statement),
  }));
}

export function visibleFields(record: BusinessRecord) {
  return record.fields.filter((field) => ![record.primaryKey, "name", "status"].includes(field.apiName));
}

export function allowedActions(value: BusinessSystemDefinition, item: GenericObject): BusinessAction[] {
  const current = text(item.properties.status);
  return actions(value).filter((action) => action.fromStates.length === 0 || action.fromStates.includes(current));
}

export function screenComponents(screen: BusinessSystemScreen | undefined): BusinessSystemComponent[] {
  return screen?.components ?? [];
}

export function title(item: GenericObject): string {
  return text(item.properties.name) || item.objectId;
}

export function status(item: GenericObject): string {
  return text(item.properties.status) || "확인 필요";
}

function model(value: BusinessSystemDefinition): Record<string, unknown> {
  return value.businessModel;
}

function objectItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value) ? value.filter(isObject) : [];
}

function textItems(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function text(value: unknown): string {
  return typeof value === "string" ? value : String(value ?? "");
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
