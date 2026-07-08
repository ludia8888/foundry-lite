import type { OntologyCatalog } from "@foundry-lite/sdk";
import type {
  OntologyDraft,
  OntologyDraftObjectType,
  OntologyDraftProperty,
  OntologyDraftRecord,
} from "@foundry-lite/sdk/ontology-draft";
import {
  emptyOntologyDraft,
  ontologyDraftFromCatalog,
  ontologyDraftToDefinition,
} from "@foundry-lite/sdk/ontology-draft";

/** 백엔드 property type 허용 enum — 벗어나는 타입은 string으로 강등한다. */
export const ALLOWED_PROPERTY_TYPES = [
  "string",
  "integer",
  "float",
  "boolean",
  "media_reference",
] as const;

export type AllowedPropertyType = (typeof ALLOWED_PROPERTY_TYPES)[number];

const ALLOWED_PROPERTY_TYPE_SET = new Set<string>(ALLOWED_PROPERTY_TYPES);

/** 허용 목록을 벗어나는 타입은 string으로 강등한다 (백엔드 계약 준수). */
export function coercePropertyType(type: string): AllowedPropertyType {
  return ALLOWED_PROPERTY_TYPE_SET.has(type)
    ? (type as AllowedPropertyType)
    : "string";
}

/** 드래프트 텍스트가 JSON이면 정의 레코드로 파싱한다 (수기 YAML이면 null). */
export function parseDefinitionText(
  yamlText: string,
): OntologyDraftRecord | null {
  if (yamlText.trim().length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(yamlText);
    if (
      typeof parsed === "object" &&
      parsed !== null &&
      !Array.isArray(parsed)
    ) {
      return parsed as OntologyDraftRecord;
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * 현재 편집 컨텍스트(브랜치 YAML 또는 카탈로그)에서 편집 가능한 드래프트를 만든다.
 * - 현재 텍스트가 JSON 정의면 그 정의를 카탈로그 파생 드래프트에 병합해 편집한다.
 * - 그렇지 않으면 카탈로그(없으면 빈 드래프트)에서 파생한다.
 *
 * 폼 편집은 카탈로그 파생 드래프트를 기준선으로 삼는다 — 카탈로그가 이미 전체
 * 정의를 가지고 있어 손실 없이 왕복(round-trip)한다.
 */
export function buildEditableDraft(
  catalog: OntologyCatalog | null,
  yamlText: string,
): OntologyDraft {
  const base = catalog
    ? ontologyDraftFromCatalog(catalog)
    : emptyOntologyDraft();
  const parsed = parseDefinitionText(yamlText);
  if (parsed === null) return base;
  return mergeDefinitionIntoDraft(base, parsed);
}

/**
 * JSON 정의 레코드의 objectTypes를 드래프트에 덮어쓴다. 편집 중인 브랜치
 * 드래프트가 카탈로그보다 최신일 때, 폼이 브랜치 상태를 반영하도록 한다.
 */
function mergeDefinitionIntoDraft(
  base: OntologyDraft,
  definition: OntologyDraftRecord,
): OntologyDraft {
  const rawObjectTypes = definition.objectTypes;
  if (!Array.isArray(rawObjectTypes)) return base;
  const overrides = rawObjectTypes
    .filter(isRecord)
    .filter((item) => typeof item.apiName === "string")
    .map(draftObjectTypeFromRecord);
  if (overrides.length === 0) return base;
  const overriddenApiNames = new Set(overrides.map((item) => item.apiName));
  return {
    ...base,
    objectTypes: [
      ...base.objectTypes.filter(
        (item) => !overriddenApiNames.has(item.apiName),
      ),
      ...overrides,
    ],
  };
}

/** 객체 타입 레코드의 typed 필드 — 나머지는 extras로 보존한다. */
const OBJECT_TYPE_KNOWN_KEYS = new Set([
  "apiName",
  "displayName",
  "description",
  "implements",
  "primaryKey",
  "titleProperty",
  "backing",
  "materialization",
  "rowPolicies",
  "properties",
]);

const PROPERTY_KNOWN_KEYS = new Set([
  "apiName",
  "type",
  "column",
  "displayName",
  "nullable",
  "indexed",
  "searchable",
  "editable",
  "classification",
  "source",
  "editPolicy",
  "derivation",
]);

/**
 * raw JSON objectType 레코드를 드래프트 객체 타입으로 변환한다.
 * 알려지지 않은 키(예 property.datasource)는 extras에 보존해 왕복 손실을 막는다.
 */
function draftObjectTypeFromRecord(
  raw: OntologyDraftRecord,
): OntologyDraftObjectType {
  const extras = extractExtras(raw, OBJECT_TYPE_KNOWN_KEYS);
  const objectType: OntologyDraftObjectType = {
    apiName: String(raw.apiName),
    primaryKey: typeof raw.primaryKey === "string" ? raw.primaryKey : "",
    backing: isRecord(raw.backing) ? raw.backing : {},
    properties: Array.isArray(raw.properties)
      ? raw.properties.filter(isRecord).map(draftPropertyFromRecord)
      : [],
  };
  if (typeof raw.displayName === "string")
    objectType.displayName = raw.displayName;
  if (typeof raw.description === "string")
    objectType.description = raw.description;
  if (Array.isArray(raw.implements)) {
    objectType.implements = raw.implements.filter(
      (item): item is string => typeof item === "string",
    );
  }
  if (typeof raw.titleProperty === "string") {
    objectType.titleProperty = raw.titleProperty;
  }
  if (isRecord(raw.materialization)) {
    objectType.materialization = raw.materialization;
  }
  if (Array.isArray(raw.rowPolicies)) {
    objectType.rowPolicies = raw.rowPolicies.filter(isRecord);
  }
  if (Object.keys(extras).length > 0) objectType.extras = extras;
  return objectType;
}

/** raw JSON property 레코드를 드래프트 속성으로 변환한다 (미지 키는 extras). */
function draftPropertyFromRecord(
  raw: OntologyDraftRecord,
): OntologyDraftProperty {
  const extras = extractExtras(raw, PROPERTY_KNOWN_KEYS);
  const property: OntologyDraftProperty = {
    apiName: String(raw.apiName),
    type: typeof raw.type === "string" ? raw.type : "string",
  };
  if (typeof raw.column === "string") property.column = raw.column;
  if (typeof raw.displayName === "string")
    property.displayName = raw.displayName;
  if (typeof raw.nullable === "boolean") property.nullable = raw.nullable;
  if (typeof raw.indexed === "boolean") property.indexed = raw.indexed;
  if (typeof raw.searchable === "boolean") property.searchable = raw.searchable;
  if (typeof raw.editable === "boolean") property.editable = raw.editable;
  if (typeof raw.classification === "string") {
    property.classification = raw.classification;
  }
  if (typeof raw.source === "string") property.source = raw.source;
  if (typeof raw.editPolicy === "string") property.editPolicy = raw.editPolicy;
  if (isRecord(raw.derivation)) property.derivation = raw.derivation;
  if (Object.keys(extras).length > 0) property.extras = extras;
  return property;
}

/** 알려진 키를 제외한 나머지 키를 extras 레코드로 뽑는다. */
function extractExtras(
  raw: OntologyDraftRecord,
  knownKeys: Set<string>,
): OntologyDraftRecord {
  const extras: OntologyDraftRecord = {};
  for (const [key, value] of Object.entries(raw)) {
    if (!knownKeys.has(key)) extras[key] = value;
  }
  return extras;
}

/**
 * 하나의 편집된 객체 타입만 raw JSON 기준선에 병합해 직렬화한다.
 *
 * 다른 객체 타입 레코드는 baseText에서 온 그대로 보존한다 — 카탈로그 파생 드래프트가
 * 노출하지 않는 속성 필드(예 property.datasource)를 손실 없이 왕복하기 위함.
 * baseText가 JSON이 아니면(빈 온톨로지) 편집본 하나만 담은 정의를 생성한다.
 */
export function serializeObjectTypeIntoText(
  objectType: OntologyDraftObjectType,
  baseText: string,
): string {
  const serializedObject = (
    ontologyDraftToDefinition({
      ...emptyOntologyDraft(),
      objectTypes: [objectType],
    }).objectTypes as OntologyDraftRecord[]
  )[0];

  const base = parseDefinitionText(baseText);
  if (base === null) {
    return `${JSON.stringify({ objectTypes: [serializedObject] }, null, 2)}\n`;
  }
  const existing = Array.isArray(base.objectTypes)
    ? (base.objectTypes as OntologyDraftRecord[])
    : [];
  const hasExisting = existing.some(
    (item) => item?.apiName === objectType.apiName,
  );
  const nextObjectTypes = hasExisting
    ? existing.map((item) =>
        item?.apiName === objectType.apiName ? serializedObject : item,
      )
    : [...existing, serializedObject];
  return `${JSON.stringify({ ...base, objectTypes: nextObjectTypes }, null, 2)}\n`;
}

/** 드래프트에서 하나의 객체 타입을 apiName으로 찾는다. */
export function findDraftObjectType(
  draft: OntologyDraft,
  apiName: string,
): OntologyDraftObjectType | null {
  return draft.objectTypes.find((item) => item.apiName === apiName) ?? null;
}

/**
 * 객체 타입 backing에서 데이터소스 목록을 평탄화한다. 축약형
 * ({ dataset, mode, primaryKeyColumns })과 확장형
 * ({ datasources: [{ name, dataset, ... }] }) 모두를 지원한다.
 */
export type ObjectTypeDatasource = {
  name: string;
  dataset: string | null;
  primaryKeyColumns: string[];
  requiredRole: string | null;
};

export function objectTypeDatasources(
  backing: OntologyDraftRecord | null | undefined,
): ObjectTypeDatasource[] {
  if (!backing) return [];
  const list = backing.datasources;
  if (Array.isArray(list)) {
    return list
      .filter((item): item is OntologyDraftRecord => isRecord(item))
      .map((item, index) => ({
        name: asString(item.name) ?? `datasource-${index + 1}`,
        dataset: asString(item.dataset),
        primaryKeyColumns: asStringList(item.primaryKeyColumns),
        requiredRole: asString(item.requiredRole),
      }));
  }
  const dataset = asString(backing.dataset);
  if (dataset === null) return [];
  return [
    {
      name: asString(backing.name) ?? "primary",
      dataset,
      primaryKeyColumns: asStringList(backing.primaryKeyColumns),
      requiredRole: asString(backing.requiredRole),
    },
  ];
}

/** 데이터소스 목록에서 backing 레코드를 조립한다 (단일이면 축약형). */
export function backingFromDatasources(
  datasources: ObjectTypeDatasource[],
  mode = "snapshot",
): OntologyDraftRecord {
  if (datasources.length === 1) {
    const only = datasources[0];
    const record: OntologyDraftRecord = { mode };
    if (only.dataset) record.dataset = only.dataset;
    if (only.primaryKeyColumns.length > 0) {
      record.primaryKeyColumns = only.primaryKeyColumns;
    }
    if (only.requiredRole) record.requiredRole = only.requiredRole;
    return record;
  }
  return {
    mode,
    datasources: datasources.map((item) => {
      const record: OntologyDraftRecord = { name: item.name };
      if (item.dataset) record.dataset = item.dataset;
      if (item.primaryKeyColumns.length > 0) {
        record.primaryKeyColumns = item.primaryKeyColumns;
      }
      if (item.requiredRole) record.requiredRole = item.requiredRole;
      return record;
    }),
  };
}

function isRecord(value: unknown): value is OntologyDraftRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string");
}
