import type {
  OntologyCatalog,
  OntologyCatalogAction,
  OntologyCatalogFunction,
  OntologyCatalogInterface,
  OntologyCatalogLink,
  OntologyCatalogObject,
  OntologyCatalogProperty,
} from "./generated";

/** Free-form JSON object used for YAML fragments the draft carries verbatim. */
export type OntologyDraftRecord = Record<string, unknown>;

/**
 * One editable property row of a draft object type. Field names follow the
 * ontology YAML property schema (`type`, `column`, `editPolicy`, ...), so a
 * serialized draft round-trips through `client.ontology.validate` /
 * `client.ontology.proposals.submit` without translation. Unknown keys read
 * from an existing definition are preserved in `extras`.
 */
export type OntologyDraftProperty = {
  apiName: string;
  type: string;
  column?: string | null;
  displayName?: string | null;
  nullable?: boolean;
  indexed?: boolean;
  searchable?: boolean;
  editable?: boolean;
  classification?: string | null;
  source?: string | null;
  editPolicy?: string | null;
  derivation?: OntologyDraftRecord | null;
  extras?: OntologyDraftRecord;
};

/** One editable object type of an ontology draft (YAML `objectTypes[]` item). */
export type OntologyDraftObjectType = {
  apiName: string;
  displayName?: string | null;
  description?: string | null;
  implements?: string[];
  primaryKey: string;
  titleProperty?: string | null;
  backing: OntologyDraftRecord;
  materialization?: OntologyDraftRecord | null;
  rowPolicies?: OntologyDraftRecord[];
  properties: OntologyDraftProperty[];
  extras?: OntologyDraftRecord;
};

/** One editable link type of an ontology draft (YAML `linkTypes[]` item). */
export type OntologyDraftLinkType = {
  apiName: string;
  displayName?: string | null;
  from: string;
  to: string;
  cardinality?: string | null;
  backing: OntologyDraftRecord;
  extras?: OntologyDraftRecord;
};

/** One editable action type of an ontology draft (YAML `actionTypes[]` item). */
export type OntologyDraftActionType = {
  apiName: string;
  displayName?: string | null;
  target: string;
  parameters?: OntologyDraftRecord[];
  permissions?: OntologyDraftRecord | null;
  preconditions?: OntologyDraftRecord[];
  mutations?: OntologyDraftRecord[];
  writebacks?: OntologyDraftRecord[];
  sideEffects?: OntologyDraftRecord[];
  extras?: OntologyDraftRecord;
};

/** One editable interface type of an ontology draft (YAML `interfaces[]` item). */
export type OntologyDraftInterfaceType = {
  apiName: string;
  displayName?: string | null;
  properties?: OntologyDraftRecord[];
  extras?: OntologyDraftRecord;
};

/** One editable function type of an ontology draft (YAML `functionTypes[]` item). */
export type OntologyDraftFunctionType = {
  apiName: string;
  displayName?: string | null;
  runtime?: string | null;
  inputs?: OntologyDraftRecord[];
  output?: OntologyDraftRecord | null;
  permissions?: OntologyDraftRecord | null;
  definition?: OntologyDraftRecord | null;
  extras?: OntologyDraftRecord;
};

/** Client-side editable model of one full ontology definition document. */
export type OntologyDraft = {
  interfaces: OntologyDraftInterfaceType[];
  objectTypes: OntologyDraftObjectType[];
  linkTypes: OntologyDraftLinkType[];
  functionTypes: OntologyDraftFunctionType[];
  actionTypes: OntologyDraftActionType[];
};

/** One dataset column surfaced for column -> property mapping UIs. */
export type OntologyDatasetColumn = {
  name: string;
  dataType: string | null;
  raw: OntologyDraftRecord;
};

export type OntologyColumnPropertyMatchStatus = "mapped" | "suggested" | "unmapped";

/** Mapping suggestion between one dataset column and one draft property. */
export type OntologyColumnPropertyMatch = {
  column: OntologyDatasetColumn;
  columnName: string;
  status: OntologyColumnPropertyMatchStatus;
  property: OntologyDraftProperty | null;
  propertyApiName: string | null;
  suggestedPropertyApiName: string;
  suggestedDataType: string;
};

/**
 * Object-type config keys the backend derives at activation time
 * (reindex serving contracts). They are read-only evidence, never valid
 * YAML input, so catalog seeding strips them instead of round-tripping them.
 */
const ONTOLOGY_SYSTEM_CONFIG_KEYS = new Set([
  "servingRecordScope",
  "servingContractStatus",
  "objectReindexPlan",
  "sourceOntologyVersionId",
]);

const ONTOLOGY_OBJECT_CONFIG_MAPPED_KEYS = new Set([
  "titleProperty",
  "materialization",
  "rowPolicies",
  "implements",
]);

const ONTOLOGY_ACTION_DEFINITION_MAPPED_KEYS = new Set([
  "apiName",
  "target",
  "displayName",
  "parameters",
  "permissions",
  "preconditions",
  "mutations",
  "writebacks",
  "sideEffects",
]);

/** Return a new, completely empty ontology draft. */
export function emptyOntologyDraft(): OntologyDraft {
  return {
    interfaces: [],
    objectTypes: [],
    linkTypes: [],
    functionTypes: [],
    actionTypes: [],
  };
}

/**
 * Seed an editable draft from the active ontology catalog.
 *
 * Fidelity notes:
 * - Catalog objects nest their actions; the draft lifts them back to the
 *   top-level `actionTypes` list the YAML schema expects.
 * - Known object config keys (`titleProperty`, `materialization`,
 *   `rowPolicies`, `implements`) map onto typed draft fields; every other
 *   non-system config key is preserved verbatim in `extras` so a round-trip
 *   does not drop features the editor does not know about.
 * - The catalog does not expose `functionTypes[].definition` (logic DAG), so
 *   catalog-seeded function drafts keep the declared surface (inputs/output/
 *   runtime/permissions) but the `definition` block must be re-authored or
 *   pasted from the source YAML before the draft can be applied.
 */
export function ontologyDraftFromCatalog(catalog: OntologyCatalog): OntologyDraft {
  return {
    interfaces: (catalog.interfaces ?? []).map(ontologyDraftInterfaceFromCatalog),
    objectTypes: catalog.objectTypes.map(ontologyDraftObjectTypeFromCatalog),
    linkTypes: catalog.linkTypes.map(ontologyDraftLinkTypeFromCatalog),
    functionTypes: (catalog.functionTypes ?? []).map(ontologyDraftFunctionFromCatalog),
    actionTypes: catalog.objectTypes.flatMap((objectType) =>
      objectType.actions.map(ontologyDraftActionTypeFromCatalog),
    ),
  };
}

/**
 * Convert a draft into the plain definition document (`interfaces`,
 * `objectTypes`, `linkTypes`, `functionTypes`, `actionTypes`) the backend
 * YAML loader consumes. `extras` entries are spread first so explicit draft
 * fields always win.
 */
export function ontologyDraftToDefinition(draft: OntologyDraft): OntologyDraftRecord {
  const definition: OntologyDraftRecord = {};
  if (draft.interfaces.length > 0) {
    definition.interfaces = draft.interfaces.map(serializeOntologyDraftInterface);
  }
  definition.objectTypes = draft.objectTypes.map(serializeOntologyDraftObjectType);
  if (draft.linkTypes.length > 0) {
    definition.linkTypes = draft.linkTypes.map(serializeOntologyDraftLinkType);
  }
  if (draft.functionTypes.length > 0) {
    definition.functionTypes = draft.functionTypes.map(serializeOntologyDraftFunction);
  }
  if (draft.actionTypes.length > 0) {
    definition.actionTypes = draft.actionTypes.map(serializeOntologyDraftActionType);
  }
  return definition;
}

/**
 * Serialize a draft to text accepted by `client.ontology.validate`,
 * `client.ontology.apply`, and `client.ontology.proposals.submit`.
 *
 * The output is pretty-printed JSON on purpose: YAML 1.2 is a strict JSON
 * superset and the backend loads `yamlText` with a YAML loader, so JSON is
 * valid YAML and needs no YAML emitter dependency in the browser.
 * Hand-authored YAML files remain equally valid input for the same endpoints.
 */
export function ontologyDraftToYamlText(draft: OntologyDraft): string {
  return `${JSON.stringify(ontologyDraftToDefinition(draft), null, 2)}\n`;
}

/** Insert or replace one object type by apiName; returns a new draft. */
export function upsertOntologyDraftObjectType(
  draft: OntologyDraft,
  objectType: OntologyDraftObjectType,
): OntologyDraft {
  return {
    ...draft,
    objectTypes: upsertByApiName(draft.objectTypes, objectType),
  };
}

/** Patch one object type by apiName; returns the draft unchanged when absent. */
export function updateOntologyDraftObjectType(
  draft: OntologyDraft,
  apiName: string,
  patch: Partial<Omit<OntologyDraftObjectType, "apiName">>,
): OntologyDraft {
  return {
    ...draft,
    objectTypes: draft.objectTypes.map((objectType) =>
      objectType.apiName === apiName ? { ...objectType, ...patch, apiName } : objectType,
    ),
  };
}

/** Remove one object type by apiName; link/action references are left for validate() to flag. */
export function removeOntologyDraftObjectType(draft: OntologyDraft, apiName: string): OntologyDraft {
  return {
    ...draft,
    objectTypes: draft.objectTypes.filter((objectType) => objectType.apiName !== apiName),
  };
}

/** Replace the `implements` interface list of one object type. */
export function setOntologyDraftObjectTypeImplements(
  draft: OntologyDraft,
  apiName: string,
  implementsApiNames: string[],
): OntologyDraft {
  return updateOntologyDraftObjectType(draft, apiName, { implements: [...implementsApiNames] });
}

/** Insert or replace one property of an object type by apiName. */
export function upsertOntologyDraftProperty(
  draft: OntologyDraft,
  objectApiName: string,
  property: OntologyDraftProperty,
): OntologyDraft {
  return {
    ...draft,
    objectTypes: draft.objectTypes.map((objectType) =>
      objectType.apiName === objectApiName
        ? { ...objectType, properties: upsertByApiName(objectType.properties, property) }
        : objectType,
    ),
  };
}

/** Patch one property of an object type by apiName. */
export function updateOntologyDraftProperty(
  draft: OntologyDraft,
  objectApiName: string,
  propertyApiName: string,
  patch: Partial<Omit<OntologyDraftProperty, "apiName">>,
): OntologyDraft {
  return {
    ...draft,
    objectTypes: draft.objectTypes.map((objectType) =>
      objectType.apiName === objectApiName
        ? {
            ...objectType,
            properties: objectType.properties.map((property) =>
              property.apiName === propertyApiName
                ? { ...property, ...patch, apiName: propertyApiName }
                : property,
            ),
          }
        : objectType,
    ),
  };
}

/** Remove one property of an object type by apiName. */
export function removeOntologyDraftProperty(
  draft: OntologyDraft,
  objectApiName: string,
  propertyApiName: string,
): OntologyDraft {
  return {
    ...draft,
    objectTypes: draft.objectTypes.map((objectType) =>
      objectType.apiName === objectApiName
        ? {
            ...objectType,
            properties: objectType.properties.filter(
              (property) => property.apiName !== propertyApiName,
            ),
          }
        : objectType,
    ),
  };
}

/** Insert or replace one link type by apiName. */
export function upsertOntologyDraftLinkType(
  draft: OntologyDraft,
  linkType: OntologyDraftLinkType,
): OntologyDraft {
  return { ...draft, linkTypes: upsertByApiName(draft.linkTypes, linkType) };
}

/** Remove one link type by apiName. */
export function removeOntologyDraftLinkType(draft: OntologyDraft, apiName: string): OntologyDraft {
  return { ...draft, linkTypes: draft.linkTypes.filter((linkType) => linkType.apiName !== apiName) };
}

/** Insert or replace one action type by apiName. */
export function upsertOntologyDraftActionType(
  draft: OntologyDraft,
  actionType: OntologyDraftActionType,
): OntologyDraft {
  return { ...draft, actionTypes: upsertByApiName(draft.actionTypes, actionType) };
}

/** Remove one action type by apiName. */
export function removeOntologyDraftActionType(draft: OntologyDraft, apiName: string): OntologyDraft {
  return {
    ...draft,
    actionTypes: draft.actionTypes.filter((actionType) => actionType.apiName !== apiName),
  };
}

/** Insert or replace one interface type by apiName. */
export function upsertOntologyDraftInterfaceType(
  draft: OntologyDraft,
  interfaceType: OntologyDraftInterfaceType,
): OntologyDraft {
  return { ...draft, interfaces: upsertByApiName(draft.interfaces, interfaceType) };
}

/** Remove one interface type by apiName. */
export function removeOntologyDraftInterfaceType(
  draft: OntologyDraft,
  apiName: string,
): OntologyDraft {
  return {
    ...draft,
    interfaces: draft.interfaces.filter((interfaceType) => interfaceType.apiName !== apiName),
  };
}

/** Insert or replace one function type by apiName. */
export function upsertOntologyDraftFunctionType(
  draft: OntologyDraft,
  functionType: OntologyDraftFunctionType,
): OntologyDraft {
  return { ...draft, functionTypes: upsertByApiName(draft.functionTypes, functionType) };
}

/** Remove one function type by apiName. */
export function removeOntologyDraftFunctionType(
  draft: OntologyDraft,
  apiName: string,
): OntologyDraft {
  return {
    ...draft,
    functionTypes: draft.functionTypes.filter((functionType) => functionType.apiName !== apiName),
  };
}

/**
 * Read the column list from a dataset inspection schema
 * (`DatasetInspectionPayload.schema.columns`).
 */
export function ontologyDatasetColumnsFromSchema(
  schema: Record<string, unknown> | null | undefined,
): OntologyDatasetColumn[] {
  const rawColumns = schema?.columns;
  if (!Array.isArray(rawColumns)) return [];
  const columns: OntologyDatasetColumn[] = [];
  for (const rawColumn of rawColumns) {
    if (!isOntologyDraftRecord(rawColumn)) continue;
    const name = rawColumn.name;
    if (typeof name !== "string" || name.length === 0) continue;
    const dataType = rawColumn.type;
    columns.push({
      name,
      dataType: typeof dataType === "string" ? dataType : null,
      raw: rawColumn,
    });
  }
  return columns;
}

/** Convert a snake_case (or kebab-case) column name to a camelCase property apiName. */
export function ontologyPropertyApiNameForColumn(columnName: string): string {
  return columnName
    .trim()
    .replace(/[-\s]+/g, "_")
    .replace(/_+([a-zA-Z0-9])/g, (_match, char: string) => char.toUpperCase())
    .replace(/^_+|_+$/g, "");
}

/** Map a dataset column type onto the closest ontology property data type. */
export function ontologyDataTypeForColumnType(columnType: string | null | undefined): string {
  switch ((columnType ?? "").toLowerCase()) {
    case "int":
    case "integer":
    case "smallint":
    case "bigint":
    case "long":
      return "integer";
    case "float":
    case "double":
    case "decimal":
    case "real":
    case "number":
      return "float";
    case "bool":
    case "boolean":
      return "boolean";
    case "date":
      return "date";
    case "datetime":
    case "timestamp":
      return "timestamp";
    default:
      return "string";
  }
}

/**
 * Suggest column -> property mappings for one draft object type.
 *
 * A column is `mapped` when a draft property already declares it via
 * `column`, `suggested` when a property matches by name equality or
 * snake_case/camelCase conversion, and `unmapped` otherwise (with a
 * camelCase apiName and data type suggestion ready for `upsertProperty`).
 */
export function matchColumnsToProperties(
  columns: OntologyDatasetColumn[],
  draftObject: OntologyDraftObjectType | null | undefined,
): OntologyColumnPropertyMatch[] {
  const properties = draftObject?.properties ?? [];
  return columns.map((column) => {
    const suggestedPropertyApiName = ontologyPropertyApiNameForColumn(column.name);
    const suggestedDataType = ontologyDataTypeForColumnType(column.dataType);
    const mappedProperty = properties.find((property) => property.column === column.name) ?? null;
    if (mappedProperty) {
      return ontologyColumnPropertyMatch(column, "mapped", mappedProperty, {
        suggestedPropertyApiName,
        suggestedDataType,
      });
    }
    const namedProperty =
      properties.find(
        (property) =>
          property.apiName === column.name || property.apiName === suggestedPropertyApiName,
      ) ?? null;
    if (namedProperty) {
      return ontologyColumnPropertyMatch(column, "suggested", namedProperty, {
        suggestedPropertyApiName,
        suggestedDataType,
      });
    }
    return ontologyColumnPropertyMatch(column, "unmapped", null, {
      suggestedPropertyApiName,
      suggestedDataType,
    });
  });
}

/** Build a ready-to-upsert draft property for one unmapped dataset column. */
export function ontologyDraftPropertyForColumn(
  column: OntologyDatasetColumn,
): OntologyDraftProperty {
  return {
    apiName: ontologyPropertyApiNameForColumn(column.name),
    type: ontologyDataTypeForColumnType(column.dataType),
    column: column.name,
  };
}

function ontologyColumnPropertyMatch(
  column: OntologyDatasetColumn,
  status: OntologyColumnPropertyMatchStatus,
  property: OntologyDraftProperty | null,
  suggestion: { suggestedPropertyApiName: string; suggestedDataType: string },
): OntologyColumnPropertyMatch {
  return {
    column,
    columnName: column.name,
    status,
    property,
    propertyApiName: property?.apiName ?? null,
    suggestedPropertyApiName: suggestion.suggestedPropertyApiName,
    suggestedDataType: suggestion.suggestedDataType,
  };
}

function upsertByApiName<TItem extends { apiName: string }>(
  items: TItem[],
  item: TItem,
): TItem[] {
  const index = items.findIndex((existing) => existing.apiName === item.apiName);
  if (index === -1) return [...items, item];
  return items.map((existing, position) => (position === index ? item : existing));
}

function isOntologyDraftRecord(value: unknown): value is OntologyDraftRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function recordListOrUndefined(value: unknown): OntologyDraftRecord[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter(isOntologyDraftRecord).map((record) => ({ ...record }));
}

function recordOrUndefined(value: unknown): OntologyDraftRecord | undefined {
  return isOntologyDraftRecord(value) ? { ...value } : undefined;
}

function stringListOrUndefined(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined;
  return value.filter((item): item is string => typeof item === "string");
}

function ontologyDraftInterfaceFromCatalog(
  interfaceType: OntologyCatalogInterface,
): OntologyDraftInterfaceType {
  return {
    apiName: interfaceType.apiName,
    displayName: interfaceType.displayName,
    properties: interfaceType.properties.map((property) => ({ ...property })),
  };
}

function ontologyDraftObjectTypeFromCatalog(
  objectType: OntologyCatalogObject,
): OntologyDraftObjectType {
  const config = objectType.config ?? {};
  const extras: OntologyDraftRecord = {};
  for (const [key, value] of Object.entries(config)) {
    if (ONTOLOGY_OBJECT_CONFIG_MAPPED_KEYS.has(key)) continue;
    if (ONTOLOGY_SYSTEM_CONFIG_KEYS.has(key)) continue;
    extras[key] = value;
  }
  const draftObject: OntologyDraftObjectType = {
    apiName: objectType.apiName,
    displayName: objectType.displayName,
    primaryKey: objectType.primaryKeyProperty,
    backing: { ...objectType.backing },
    properties: objectType.properties.map(ontologyDraftPropertyFromCatalog),
  };
  if (objectType.description !== null) draftObject.description = objectType.description;
  const implementsApiNames =
    stringListOrUndefined(objectType.implements) ?? stringListOrUndefined(config.implements);
  if (implementsApiNames && implementsApiNames.length > 0) {
    draftObject.implements = implementsApiNames;
  }
  const titleProperty =
    objectType.titleProperty ??
    (typeof config.titleProperty === "string" ? config.titleProperty : null);
  if (titleProperty !== null) draftObject.titleProperty = titleProperty;
  const materialization = recordOrUndefined(config.materialization);
  if (materialization) draftObject.materialization = materialization;
  const rowPolicies = recordListOrUndefined(config.rowPolicies);
  if (rowPolicies && rowPolicies.length > 0) draftObject.rowPolicies = rowPolicies;
  if (Object.keys(extras).length > 0) draftObject.extras = extras;
  return draftObject;
}

function ontologyDraftPropertyFromCatalog(
  property: OntologyCatalogProperty,
): OntologyDraftProperty {
  const draftProperty: OntologyDraftProperty = {
    apiName: property.apiName,
    type: property.dataType,
    displayName: property.displayName,
    nullable: property.nullable,
    indexed: property.indexed,
    searchable: property.searchable,
    editable: property.editable,
    source: property.source,
    editPolicy: property.editPolicy,
  };
  if (property.columnName !== null) draftProperty.column = property.columnName;
  if (property.classification !== null) draftProperty.classification = property.classification;
  if (property.derivation !== null) draftProperty.derivation = { ...property.derivation };
  return draftProperty;
}

function ontologyDraftLinkTypeFromCatalog(linkType: OntologyCatalogLink): OntologyDraftLinkType {
  return {
    apiName: linkType.apiName,
    displayName: linkType.displayName,
    from: linkType.fromObjectType,
    to: linkType.toObjectType,
    cardinality: linkType.cardinality,
    backing: { ...linkType.backing },
  };
}

function ontologyDraftActionTypeFromCatalog(
  action: OntologyCatalogAction,
): OntologyDraftActionType {
  const definition = action.definition ?? {};
  const extras: OntologyDraftRecord = {};
  for (const [key, value] of Object.entries(definition)) {
    if (ONTOLOGY_ACTION_DEFINITION_MAPPED_KEYS.has(key)) continue;
    extras[key] = value;
  }
  const draftAction: OntologyDraftActionType = {
    apiName: action.apiName,
    displayName: action.displayName,
    target: action.target,
  };
  const parameters = recordListOrUndefined(definition.parameters);
  if (parameters && parameters.length > 0) draftAction.parameters = parameters;
  const permissions = recordOrUndefined(definition.permissions);
  if (permissions) draftAction.permissions = permissions;
  const preconditions = recordListOrUndefined(definition.preconditions);
  if (preconditions && preconditions.length > 0) draftAction.preconditions = preconditions;
  const mutations = recordListOrUndefined(definition.mutations);
  if (mutations) draftAction.mutations = mutations;
  const writebacks = recordListOrUndefined(definition.writebacks);
  if (writebacks && writebacks.length > 0) draftAction.writebacks = writebacks;
  const sideEffects = recordListOrUndefined(definition.sideEffects);
  if (sideEffects && sideEffects.length > 0) draftAction.sideEffects = sideEffects;
  if (Object.keys(extras).length > 0) draftAction.extras = extras;
  return draftAction;
}

function ontologyDraftFunctionFromCatalog(
  functionType: OntologyCatalogFunction,
): OntologyDraftFunctionType {
  const draftFunction: OntologyDraftFunctionType = {
    apiName: functionType.apiName,
    displayName: functionType.displayName,
    runtime: functionType.runtime,
    inputs: functionType.inputs.map((input) => ({ ...input })),
    output: { ...functionType.output },
  };
  if (functionType.allowedRoles !== null) {
    draftFunction.permissions = { allowedRoles: [...functionType.allowedRoles] };
  }
  return draftFunction;
}

function serializeOntologyDraftInterface(
  interfaceType: OntologyDraftInterfaceType,
): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(interfaceType.extras ?? {}), apiName: interfaceType.apiName };
  assignIfPresent(item, "displayName", interfaceType.displayName);
  assignIfPresent(item, "properties", interfaceType.properties);
  return item;
}

function serializeOntologyDraftObjectType(
  objectType: OntologyDraftObjectType,
): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(objectType.extras ?? {}), apiName: objectType.apiName };
  assignIfPresent(item, "displayName", objectType.displayName);
  assignIfPresent(item, "description", objectType.description);
  if (objectType.implements && objectType.implements.length > 0) {
    item.implements = objectType.implements;
  }
  item.primaryKey = objectType.primaryKey;
  assignIfPresent(item, "titleProperty", objectType.titleProperty);
  item.backing = objectType.backing;
  assignIfPresent(item, "materialization", objectType.materialization);
  if (objectType.rowPolicies && objectType.rowPolicies.length > 0) {
    item.rowPolicies = objectType.rowPolicies;
  }
  item.properties = objectType.properties.map(serializeOntologyDraftProperty);
  return item;
}

function serializeOntologyDraftProperty(property: OntologyDraftProperty): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(property.extras ?? {}), apiName: property.apiName };
  assignIfPresent(item, "displayName", property.displayName);
  item.type = property.type;
  assignIfPresent(item, "column", property.column);
  assignIfPresent(item, "nullable", property.nullable);
  assignIfPresent(item, "indexed", property.indexed);
  assignIfPresent(item, "searchable", property.searchable);
  assignIfPresent(item, "editable", property.editable);
  assignIfPresent(item, "classification", property.classification);
  assignIfPresent(item, "source", property.source);
  assignIfPresent(item, "editPolicy", property.editPolicy);
  assignIfPresent(item, "derivation", property.derivation);
  return item;
}

function serializeOntologyDraftLinkType(linkType: OntologyDraftLinkType): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(linkType.extras ?? {}), apiName: linkType.apiName };
  assignIfPresent(item, "displayName", linkType.displayName);
  item.from = linkType.from;
  item.to = linkType.to;
  assignIfPresent(item, "cardinality", linkType.cardinality);
  item.backing = linkType.backing;
  return item;
}

function serializeOntologyDraftActionType(
  actionType: OntologyDraftActionType,
): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(actionType.extras ?? {}), apiName: actionType.apiName };
  assignIfPresent(item, "displayName", actionType.displayName);
  item.target = actionType.target;
  assignIfPresent(item, "parameters", actionType.parameters);
  assignIfPresent(item, "permissions", actionType.permissions);
  assignIfPresent(item, "preconditions", actionType.preconditions);
  assignIfPresent(item, "mutations", actionType.mutations);
  assignIfPresent(item, "writebacks", actionType.writebacks);
  assignIfPresent(item, "sideEffects", actionType.sideEffects);
  return item;
}

function serializeOntologyDraftFunction(
  functionType: OntologyDraftFunctionType,
): OntologyDraftRecord {
  const item: OntologyDraftRecord = { ...(functionType.extras ?? {}), apiName: functionType.apiName };
  assignIfPresent(item, "displayName", functionType.displayName);
  assignIfPresent(item, "runtime", functionType.runtime);
  assignIfPresent(item, "inputs", functionType.inputs);
  assignIfPresent(item, "output", functionType.output);
  assignIfPresent(item, "permissions", functionType.permissions);
  assignIfPresent(item, "definition", functionType.definition);
  return item;
}

function assignIfPresent(target: OntologyDraftRecord, key: string, value: unknown): void {
  if (value === undefined || value === null) return;
  target[key] = value;
}
