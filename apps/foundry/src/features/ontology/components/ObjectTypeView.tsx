import type { OntologyCatalog } from "@foundry-lite/sdk";
import type {
  OntologyDraft,
  OntologyDraftProperty,
} from "@foundry-lite/sdk/ontology-draft";
import {
  removeOntologyDraftProperty,
  setOntologyDraftObjectTypeImplements,
  updateOntologyDraftObjectType,
  updateOntologyDraftProperty,
  upsertOntologyDraftProperty,
} from "@foundry-lite/sdk/ontology-draft";
import type {
  FoundryLiteOntologyBranchMutationsState,
  FoundryLiteOntologyBranchState,
  FoundryLiteOntologyObjectView,
} from "@foundry-lite/sdk/react";
import { useFoundryLiteProvidedOntologyResourceInsights } from "@foundry-lite/sdk/react";
import { AlertTriangle, Pencil, PlusCircle, Puzzle, Star } from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { Checkbox } from "@/components/ui/checkbox";
import { Separator } from "@/components/ui/separator";

import {
  backingFromDatasources,
  buildEditableDraft,
  findDraftObjectType,
  objectTypeDatasources,
  serializeObjectTypeIntoText,
} from "../lib/object-type-draft";
import { AdvancedSection } from "./AdvancedSection";
import { DatasourceMappingTable } from "./DatasourceMappingTable";
import { NewPropertyDialog } from "./NewPropertyDialog";
import { ObjectTypeOverviewForm } from "./ObjectTypeOverviewForm";
import type { ObjectTypeSection } from "./ObjectTypeSectionNav";
import { ObjectTypeSectionNav } from "./ObjectTypeSectionNav";
import { PropertyEditorForm } from "./PropertyEditorForm";
import { ResourceKindIcon } from "./ResourceTable";

interface ObjectTypeViewProps {
  apiName: string;
  objectView: FoundryLiteOntologyObjectView | null;
  catalog: OntologyCatalog | null;
  versionLabel: string | null;
  yamlText: string;
  /** 브랜치가 열려 있어야 폼 편집이 저장으로 이어진다. */
  isEditable: boolean;
  /** 상위 draft override가 base와 다르면 dirty (Advanced JSON 편집기용). */
  isDraftDirty: boolean;
  /** 선택된 브랜치가 있으면 색인 상태 배지가 색인됨으로 바뀐다. */
  isIndexedOnBranch: boolean;
  branchDetail: FoundryLiteOntologyBranchState;
  updateMutation: FoundryLiteOntologyBranchMutationsState["update"];
  onDraftChange: (yamlText: string) => void;
  onSaveToBranch: (yamlText: string) => void;
  onOntologyChanged: () => void;
  onBack: () => void;
}

/**
 * 객체 타입 편집 뷰: 좌 섹션 네비 + 우 콘텐츠(Overview/Properties/Datasources/
 * Interfaces/Usage/Advanced). 편집은 편집 가능한 드래프트에 SDK 헬퍼로 적용한 뒤
 * 직렬화해 상위 draft override로 전달한다 (validate/branch 파이프라인 재사용).
 */
export function ObjectTypeView({
  apiName,
  objectView,
  catalog,
  versionLabel,
  yamlText,
  isEditable,
  isDraftDirty,
  isIndexedOnBranch,
  branchDetail,
  updateMutation,
  onDraftChange,
  onSaveToBranch,
  onOntologyChanged,
  onBack,
}: ObjectTypeViewProps) {
  const [section, setSection] = useState<ObjectTypeSection>("overview");
  const [selectedProperty, setSelectedProperty] = useState<string | null>(null);
  const [isNewPropertyOpen, setIsNewPropertyOpen] = useState(false);

  const draft = useMemo(
    () => buildEditableDraft(catalog, yamlText),
    [catalog, yamlText],
  );
  const draftObject = findDraftObjectType(draft, apiName);

  /**
   * 드래프트 변형자를 적용하고, 편집된 객체 타입만 raw JSON 기준선에 병합해
   * 상위로 올린다. 다른 객체 타입/속성 필드(예 property.datasource)는 보존된다.
   */
  const applyDraft = (next: OntologyDraft) => {
    const edited = findDraftObjectType(next, apiName);
    if (!edited) return;
    onDraftChange(serializeObjectTypeIntoText(edited, yamlText));
  };

  const handleUpdateObjectType = (
    patch: Parameters<typeof updateOntologyDraftObjectType>[2],
  ) => {
    applyDraft(updateOntologyDraftObjectType(draft, apiName, patch));
  };
  const handleUpsertProperty = (property: OntologyDraftProperty) => {
    applyDraft(upsertOntologyDraftProperty(draft, apiName, property));
  };
  const handleUpdateProperty = (
    propertyApiName: string,
    patch: Partial<Omit<OntologyDraftProperty, "apiName">>,
  ) => {
    applyDraft(
      updateOntologyDraftProperty(draft, apiName, propertyApiName, patch),
    );
  };
  const handleRenameProperty = (
    oldApiName: string,
    property: OntologyDraftProperty,
  ) => {
    // apiName 변경은 remove + upsert로 처리한다.
    const removed = removeOntologyDraftProperty(draft, apiName, oldApiName);
    applyDraft(upsertOntologyDraftProperty(removed, apiName, property));
    if (selectedProperty === oldApiName) setSelectedProperty(property.apiName);
  };
  const handleRemoveProperty = (propertyApiName: string) => {
    applyDraft(removeOntologyDraftProperty(draft, apiName, propertyApiName));
    if (selectedProperty === propertyApiName) setSelectedProperty(null);
  };
  const handleSetPrimaryKey = (propertyApiName: string) => {
    handleUpdateObjectType({ primaryKey: propertyApiName });
  };
  const handleSetImplements = (implementsApiNames: string[]) => {
    applyDraft(
      setOntologyDraftObjectTypeImplements(draft, apiName, implementsApiNames),
    );
  };

  if (!draftObject) {
    return (
      <div className="flex min-h-0 flex-1 items-start gap-3">
        <EmptyState
          title="객체 타입을 찾을 수 없습니다"
          description="선택한 객체 타입이 현재 드래프트에 없습니다."
        />
      </div>
    );
  }

  const datasources = objectTypeDatasources(draftObject.backing);
  // 매핑 표가 데이터셋 컬럼을 로드하므로, 폼 후보로는 이미 매핑된 draft 속성의
  // backing column을 사용한다.
  const backingColumnNames = draftObject.properties
    .map((property) => property.column)
    .filter((column): column is string => typeof column === "string");

  const objectCountLabel = objectView
    ? `${objectView.propertyCount} properties`
    : "object type";
  const displayName = draftObject.displayName ?? draftObject.apiName;

  return (
    <div className="flex min-h-0 flex-1 items-start gap-3">
      <ObjectTypeSectionNav
        displayName={displayName}
        objectCountLabel={objectCountLabel}
        section={section}
        counts={{
          properties: draftObject.properties.length,
          datasources: datasources.length,
          interfaces: draftObject.implements?.length ?? 0,
        }}
        onSelectSection={setSection}
        onBack={onBack}
      />

      <main className="min-w-0 flex-1 space-y-3">
        <div className="rounded border bg-card p-4">
          <div className="flex items-start gap-2.5">
            <ResourceKindIcon
              kind="objectType"
              className="size-8 rounded-[4px]"
            />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="truncate text-[22px] font-medium leading-tight">
                  {displayName}
                </span>
                <Star className="size-4 text-muted-foreground" />
              </div>
              <div className="text-xs text-muted-foreground">
                Object type · {objectView?.propertyCount ?? 0} properties
              </div>
              <div className="mt-2 flex items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded bg-[#eff0f2] px-1.5 py-0.5 text-[11px] text-foreground/80">
                  <span className="size-3 rounded-[2px] bg-primary" />
                  기본 온톨로지
                  <span className="rounded bg-card px-1 font-mono text-[10px]">
                    {catalog?.objectTypes.length ?? 0}
                  </span>
                </span>
                <button
                  type="button"
                  className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                >
                  <Pencil className="size-3" />
                  Edit groups
                </button>
              </div>
            </div>
          </div>
        </div>

        {section === "overview" ? (
          <ObjectTypeOverviewForm
            draftObject={draftObject}
            objectView={objectView}
            catalog={catalog}
            versionLabel={versionLabel}
            isEditable={isEditable}
            isIndexedOnBranch={isIndexedOnBranch}
            onUpdateObjectType={handleUpdateObjectType}
            onSelectProperty={(propertyApiName) => {
              setSelectedProperty(propertyApiName);
              setSection("properties");
            }}
            onRequestNewProperty={() => setIsNewPropertyOpen(true)}
          />
        ) : null}

        {section === "properties" ? (
          <div className="flex items-start gap-3">
            <div className="min-w-0 flex-1 rounded border bg-card">
              <div className="flex h-11 items-center gap-2 px-3">
                <span className="text-[13px] font-semibold">속성</span>
                <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                  {draftObject.properties.length}
                </span>
                <button
                  type="button"
                  onClick={() => setIsNewPropertyOpen(true)}
                  className="ml-auto flex items-center gap-1 text-xs font-medium text-[#5d88c5] hover:underline"
                >
                  <PlusCircle className="size-3.5" />
                  New
                </button>
              </div>
              <Separator />
              <ul className="p-1.5">
                {draftObject.properties.map((property) => {
                  const isSelected = property.apiName === selectedProperty;
                  return (
                    <li key={property.apiName}>
                      <button
                        type="button"
                        onClick={() => setSelectedProperty(property.apiName)}
                        className={`flex w-full items-center gap-2 rounded px-1.5 py-1.5 text-left ${
                          isSelected ? "bg-accent" : "hover:bg-muted/60"
                        }`}
                      >
                        <span className="min-w-0 flex-1 truncate text-xs">
                          {property.displayName ?? property.apiName}
                        </span>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {property.type}
                        </span>
                        {property.apiName === draftObject.primaryKey ? (
                          <span className="rounded bg-[#e6e1f5] px-1.5 py-0.5 text-[10px] font-medium text-[#5b4a9e]">
                            PK
                          </span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
            {selectedProperty
              ? (() => {
                  const property = draftObject.properties.find(
                    (item) => item.apiName === selectedProperty,
                  );
                  if (!property) return null;
                  return (
                    <PropertyEditorForm
                      property={property}
                      columnNames={backingColumnNames}
                      isPrimaryKey={property.apiName === draftObject.primaryKey}
                      isTitle={property.apiName === draftObject.titleProperty}
                      isEditable={isEditable}
                      onUpdate={(patch) =>
                        handleUpdateProperty(property.apiName, patch)
                      }
                      onSetPrimaryKey={() =>
                        handleSetPrimaryKey(property.apiName)
                      }
                      onSetTitle={(isTitle) =>
                        handleUpdateObjectType({
                          titleProperty: isTitle ? property.apiName : null,
                        })
                      }
                      onRemove={() => handleRemoveProperty(property.apiName)}
                      onClose={() => setSelectedProperty(null)}
                    />
                  );
                })()
              : null}
          </div>
        ) : null}

        {section === "datasources" ? (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-[13px] font-semibold">
                Backing datasources
              </span>
              <span className="rounded bg-[#eff0f2] px-1.5 py-0.5 font-mono text-[11px] text-foreground/70">
                {datasources.length}
              </span>
            </div>
            {datasources.length === 0 ? (
              <EmptyState
                title="백킹 데이터소스가 없습니다"
                description="객체 타입에 연결된 데이터소스가 없습니다."
              />
            ) : (
              datasources.map((datasource) => (
                <DatasourceMappingTable
                  key={datasource.name}
                  datasource={datasource}
                  draftObject={draftObject}
                  isEditable={isEditable}
                  onUpsertProperty={handleUpsertProperty}
                  onUpdateProperty={handleUpdateProperty}
                  onRenameProperty={handleRenameProperty}
                  onRemoveProperty={handleRemoveProperty}
                  onSetPrimaryKey={handleSetPrimaryKey}
                  onRemoveDatasource={
                    datasources.length > 1
                      ? () => {
                          const next = datasources.filter(
                            (item) => item.name !== datasource.name,
                          );
                          handleUpdateObjectType({
                            backing: backingFromDatasources(next),
                          });
                        }
                      : null
                  }
                />
              ))
            )}
          </div>
        ) : null}

        {section === "interfaces" ? (
          <InterfacesSection
            catalog={catalog}
            implementsApiNames={draftObject.implements ?? []}
            isEditable={isEditable}
            onSetImplements={handleSetImplements}
          />
        ) : null}

        {section === "usage" ? <UsageSection apiName={apiName} /> : null}

        {section === "advanced" ? (
          <AdvancedSection
            yamlText={yamlText}
            isDraftDirty={isDraftDirty}
            branchDetail={branchDetail}
            updateMutation={updateMutation}
            activeVersionNumber={catalog?.versionNumber ?? null}
            onYamlChange={onDraftChange}
            onSaveToBranch={onSaveToBranch}
            onOntologyChanged={onOntologyChanged}
          />
        ) : null}

        {section === "security" ||
        section === "capabilities" ||
        section === "objectViews" ||
        section === "automations" ? (
          <FutureSection />
        ) : null}
      </main>

      <NewPropertyDialog
        open={isNewPropertyOpen}
        onOpenChange={setIsNewPropertyOpen}
        columnNames={backingColumnNames}
        existingApiNames={draftObject.properties.map(
          (property) => property.apiName,
        )}
        onCreate={(property) => {
          handleUpsertProperty(property);
          setSelectedProperty(property.apiName);
          setSection("properties");
        }}
      />
    </div>
  );
}

function InterfacesSection({
  catalog,
  implementsApiNames,
  isEditable,
  onSetImplements,
}: {
  catalog: OntologyCatalog | null;
  implementsApiNames: string[];
  isEditable: boolean;
  onSetImplements: (implementsApiNames: string[]) => void;
}) {
  const interfaces = catalog?.interfaces ?? [];
  const selected = new Set(implementsApiNames);
  if (interfaces.length === 0) {
    return (
      <EmptyState
        icon={Puzzle}
        title="인터페이스가 없습니다"
        description="온톨로지에 정의된 인터페이스가 없습니다."
      />
    );
  }
  return (
    <div className="rounded border bg-card">
      <div className="flex h-11 items-center gap-2 px-3">
        <Puzzle className="size-3.5 text-primary" />
        <span className="text-[13px] font-semibold">구현 인터페이스</span>
      </div>
      <Separator />
      <ul className="p-1.5">
        {interfaces.map((item) => (
          <li
            key={item.apiName}
            className="flex items-center gap-2 rounded px-1.5 py-1.5 hover:bg-muted/60"
          >
            <Checkbox
              checked={selected.has(item.apiName)}
              disabled={!isEditable}
              onCheckedChange={(value) => {
                const next = new Set(selected);
                if (value === true) next.add(item.apiName);
                else next.delete(item.apiName);
                onSetImplements([...next]);
              }}
              aria-label={`${item.displayName} 구현`}
            />
            <span className="min-w-0 flex-1 truncate text-xs">
              {item.displayName}
            </span>
            <span className="font-mono text-[11px] text-muted-foreground">
              {item.apiName}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function UsageSection({ apiName }: { apiName: string }) {
  const insights = useFoundryLiteProvidedOntologyResourceInsights(
    "object_type",
    apiName,
    { windowDays: 30 },
  );
  return (
    <div className="rounded border bg-card p-4">
      <div className="section-label mb-2">사용량 (최근 30일)</div>
      {insights.isLoading ? (
        <p className="text-xs text-muted-foreground">로딩 중…</p>
      ) : (
        <div className="grid grid-cols-3 gap-3 text-center">
          <UsageStat label="액션 실행" value={insights.usage?.actionRuns} />
          <UsageStat label="인덱스 실행" value={insights.usage?.indexRuns} />
          <UsageStat label="감사 이벤트" value={insights.usage?.auditEvents} />
        </div>
      )}
    </div>
  );
}

function UsageStat({ label, value }: { label: string; value: unknown }) {
  const display =
    typeof value === "number"
      ? String(value)
      : value && typeof value === "object" && "count" in value
        ? String((value as { count: unknown }).count)
        : "—";
  return (
    <div className="rounded border bg-muted/20 p-3">
      <div className="font-mono text-lg font-semibold">{display}</div>
      <div className="text-[11px] text-muted-foreground">{label}</div>
    </div>
  );
}

function FutureSection() {
  return (
    <div className="rounded border bg-card p-6 text-center">
      <AlertTriangle className="mx-auto mb-2 size-6 text-[#bb9267]" />
      <div className="text-sm font-medium">아직 지원하지 않는 섹션입니다</div>
      <p className="mt-1 text-xs text-muted-foreground">
        이 섹션은 백엔드가 아직 지원하지 않아 향후 제공될 예정입니다.
      </p>
    </div>
  );
}
