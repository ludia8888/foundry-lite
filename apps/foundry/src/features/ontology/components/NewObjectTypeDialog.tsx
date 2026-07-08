import type {
  OntologyCatalog,
  OntologyValidateRequest,
} from "@foundry-lite/sdk";
import type {
  OntologyDraftObjectType,
  OntologyDraftProperty,
  OntologyDraftRecord,
} from "@foundry-lite/sdk/ontology-draft";
import {
  ontologyDraftPropertyForColumn,
  ontologyPropertyApiNameForColumn,
} from "@foundry-lite/sdk/ontology-draft";
import type { FoundryLiteOntologyBranchMutationsState } from "@foundry-lite/sdk/react";
import {
  foundryLiteOntologyDraftValidationView,
  useFoundryLiteClient,
  useFoundryLiteMutation,
  useFoundryLiteProvidedDatasetColumnMapping,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { Box, Check, Save, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";

import {
  ALLOWED_PROPERTY_TYPES,
  coercePropertyType,
  serializeObjectTypeIntoText,
} from "../lib/object-type-draft";
import { ValidationResults } from "./ValidationResults";

type WizardStep = 0 | 1 | 2;

const STEP_LABELS = ["기본 정보", "백킹 데이터소스", "컬럼 매핑"] as const;

interface NewObjectTypeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  catalog: OntologyCatalog | null;
  /** 현재 편집 컨텍스트 YAML(=JSON) — 있으면 여기에 병합해 함수 DAG 등을 보존한다. */
  yamlText: string;
  /** 열린 브랜치가 선택되어 있어야 저장할 수 있다. */
  canSaveToBranch: boolean;
  branchName: string | null;
  updateMutation: FoundryLiteOntologyBranchMutationsState["update"];
  /** 생성된 YAML을 드래프트(고급 편집기)에 반영한다. */
  onDraftGenerated: (yamlText: string) => void;
  onSaveToBranch: (yamlText: string) => void;
}

type ColumnSelection = {
  include: boolean;
  apiName: string;
  type: string;
};

/**
 * 새 객체 타입 생성 마법사 (3-스텝, YAML 미노출):
 * 1) 기본 정보(이름/설명 + API name 자동 파생)
 * 2) 백킹 데이터소스(데이터셋 선택 + PK 컬럼)
 * 3) 컬럼 → 속성 매핑(include/타입/기본 키 지정).
 * 통과 시 draft → YAML → validate → 브랜치 저장.
 */
export function NewObjectTypeDialog({
  open,
  onOpenChange,
  catalog,
  yamlText,
  canSaveToBranch,
  branchName,
  updateMutation,
  onDraftGenerated,
  onSaveToBranch,
}: NewObjectTypeDialogProps) {
  const client = useFoundryLiteClient();
  const [step, setStep] = useState<WizardStep>(0);
  const [name, setName] = useState("");
  const [apiName, setApiName] = useState("");
  const [isApiNameManual, setIsApiNameManual] = useState(false);
  const [description, setDescription] = useState("");
  const [datasetKey, setDatasetKey] = useState<string | null>(null);
  const [requiredRole, setRequiredRole] = useState("");
  const [selections, setSelections] = useState<Record<string, ColumnSelection>>(
    {},
  );
  const [pkColumn, setPkColumn] = useState<string | null>(null);
  const [titleColumn, setTitleColumn] = useState<string | null>(null);
  const [generatedYaml, setGeneratedYaml] = useState<string | null>(null);

  const datasetsQuery = useFoundryLiteQuery(
    ["ontology", "new-object-type", "datasets"],
    () => client.datasets.list(),
    { enabled: open },
  );
  const datasets = datasetsQuery.data ?? [];

  const selectedDataset = useMemo(() => {
    if (!datasetKey) return null;
    const [namespace, datasetName] = datasetKey.split("/");
    return namespace && datasetName ? { namespace, name: datasetName } : null;
  }, [datasetKey]);

  const mapping = useFoundryLiteProvidedDatasetColumnMapping(selectedDataset, {
    enabled: open && selectedDataset !== null,
  });

  const validateMutation = useFoundryLiteMutation(
    (payload: OntologyValidateRequest) => client.ontology.validate(payload),
    { lockKey: () => "ontology:validate:new-object-type" },
  );
  const validationView = foundryLiteOntologyDraftValidationView(
    validateMutation.result,
  );

  const existingApiNames = useMemo(
    () => new Set((catalog?.objectTypes ?? []).map((item) => item.apiName)),
    [catalog],
  );
  const isApiNameDuplicate =
    apiName.length > 0 && existingApiNames.has(apiName);

  const reset = () => {
    setStep(0);
    setName("");
    setApiName("");
    setIsApiNameManual(false);
    setDescription("");
    setDatasetKey(null);
    setRequiredRole("");
    setSelections({});
    setPkColumn(null);
    setTitleColumn(null);
    setGeneratedYaml(null);
  };

  const handleOpenChange = (isOpen: boolean) => {
    if (!isOpen) reset();
    onOpenChange(isOpen);
  };

  const handleNameChange = (value: string) => {
    setName(value);
    if (!isApiNameManual) {
      setApiName(pascalCaseApiName(value));
    }
  };

  // 컬럼이 로드되면 include=on/type prefill 기본 선택을 준비한다.
  const columnSelections = useMemo(() => {
    const next: Record<string, ColumnSelection> = {};
    for (const column of mapping.columns) {
      const existing = selections[column.name];
      next[column.name] = existing ?? {
        include: true,
        apiName: ontologyPropertyApiNameForColumn(column.name),
        type: coercePropertyType(ontologyDraftPropertyForColumn(column).type),
      };
    }
    return next;
  }, [mapping.columns, selections]);

  const includedCount = Object.values(columnSelections).filter(
    (item) => item.include,
  ).length;

  const setSelection = (
    columnName: string,
    patch: Partial<ColumnSelection>,
  ) => {
    setSelections((current) => ({
      ...current,
      [columnName]: { ...columnSelections[columnName], ...patch },
    }));
  };

  const canGoStep1 = apiName.length > 0 && !isApiNameDuplicate;
  const canGoStep2 =
    selectedDataset !== null && pkColumn !== null && mapping.hasColumns;
  const canGenerate =
    canGoStep1 &&
    canGoStep2 &&
    includedCount > 0 &&
    !validateMutation.isRunning;

  const buildDraftYaml = (): string => {
    const properties: OntologyDraftProperty[] = mapping.columns
      .filter((column) => columnSelections[column.name]?.include)
      .map((column) => {
        const selection = columnSelections[column.name];
        const base = ontologyDraftPropertyForColumn(column);
        return {
          ...base,
          apiName: selection.apiName,
          type: coercePropertyType(selection.type),
        };
      });
    const pkSelection = pkColumn ? columnSelections[pkColumn] : null;
    const primaryKey = pkSelection?.apiName ?? apiName;
    const titleSelection = titleColumn ? columnSelections[titleColumn] : null;

    const backing: OntologyDraftRecord = {
      dataset: selectedDataset
        ? `${selectedDataset.namespace}.${selectedDataset.name}`
        : "",
      mode: "snapshot",
    };
    if (pkColumn) backing.primaryKeyColumns = [pkColumn];
    if (requiredRole.trim().length > 0)
      backing.requiredRole = requiredRole.trim();

    const objectType: OntologyDraftObjectType = {
      apiName,
      displayName: name.trim() || apiName,
      description: description.trim().length > 0 ? description.trim() : null,
      primaryKey,
      titleProperty: titleSelection?.apiName ?? null,
      backing,
      properties,
    };
    // 새 객체 타입만 브랜치 JSON 기준선에 병합한다 — 기존 객체 타입/함수 DAG 등을
    // 그대로 보존한다. 빈 온톨로지면 이 객체 타입 하나만 담은 정의를 만든다.
    return serializeObjectTypeIntoText(objectType, yamlText);
  };

  const handleGenerateAndValidate = () => {
    const nextYaml = buildDraftYaml();
    setGeneratedYaml(nextYaml);
    onDraftGenerated(nextYaml);
    void validateMutation.execute({ yaml: nextYaml });
  };

  const canSave =
    generatedYaml !== null &&
    validateMutation.result !== null &&
    validateMutation.error === null &&
    !validationView.isBlocked &&
    !validateMutation.isRunning &&
    canSaveToBranch &&
    !updateMutation.isRunning;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Box className="size-4 text-primary" />새 객체 타입
          </DialogTitle>
          <DialogDescription>
            폼으로 객체 타입을 정의하고 컬럼을 속성에 매핑합니다. 내부적으로
            드래프트를 생성해 검증하고 브랜치에 저장합니다.
          </DialogDescription>
        </DialogHeader>

        <Stepper step={step} />

        <div className="min-h-[280px]">
          {step === 0 ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label className="text-xs">이름 (Display name)</Label>
                <Input
                  value={name}
                  onChange={(event) => handleNameChange(event.target.value)}
                  placeholder="예: Supplier"
                  className="h-8 text-xs"
                />
              </div>
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <Label className="text-xs">API name</Label>
                  {!isApiNameManual ? (
                    <button
                      type="button"
                      onClick={() => setIsApiNameManual(true)}
                      className="text-[11px] text-primary hover:underline"
                    >
                      편집
                    </button>
                  ) : null}
                </div>
                <Input
                  value={apiName}
                  disabled={!isApiNameManual}
                  onChange={(event) => setApiName(event.target.value)}
                  className="h-8 font-mono text-xs"
                />
                <p className="font-mono text-[10px] text-muted-foreground">
                  API name: {apiName || "—"}
                </p>
                {isApiNameDuplicate ? (
                  <p className="text-[11px] text-destructive">
                    이미 존재하는 객체 타입 API 이름입니다.
                  </p>
                ) : null}
              </div>
              <div className="space-y-1.5">
                <Label className="text-xs">설명 (선택)</Label>
                <Input
                  value={description}
                  onChange={(event) => setDescription(event.target.value)}
                  placeholder="객체 타입 설명"
                  className="h-8 text-xs"
                />
              </div>
            </div>
          ) : null}

          {step === 1 ? (
            <div className="space-y-3">
              <div className="space-y-1.5">
                <Label className="text-xs">백킹 데이터셋</Label>
                <Select
                  value={datasetKey ?? undefined}
                  onValueChange={(value) => {
                    setDatasetKey(value);
                    setPkColumn(null);
                    setTitleColumn(null);
                    setSelections({});
                  }}
                >
                  <SelectTrigger size="sm" className="w-full text-xs">
                    <SelectValue placeholder="데이터셋 선택" />
                  </SelectTrigger>
                  <SelectContent>
                    {datasets.map((dataset) => (
                      <SelectItem
                        key={dataset.id}
                        value={`${dataset.namespace}/${dataset.name}`}
                        className="font-mono text-xs"
                      >
                        {dataset.namespace}.{dataset.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label className="text-xs">기본 키 컬럼</Label>
                  <Select
                    value={pkColumn ?? undefined}
                    onValueChange={setPkColumn}
                    disabled={!selectedDataset || mapping.isLoading}
                  >
                    <SelectTrigger size="sm" className="w-full text-xs">
                      <SelectValue
                        placeholder={
                          !selectedDataset
                            ? "데이터셋 먼저 선택"
                            : mapping.isLoading
                              ? "컬럼 로딩 중…"
                              : "기본 키 컬럼"
                        }
                      />
                    </SelectTrigger>
                    <SelectContent>
                      {mapping.columns.map((column) => (
                        <SelectItem
                          key={column.name}
                          value={column.name}
                          className="font-mono text-xs"
                        >
                          {column.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1.5">
                  <Label className="text-xs">필수 역할 (선택)</Label>
                  <Input
                    value={requiredRole}
                    onChange={(event) => setRequiredRole(event.target.value)}
                    placeholder="예: finance"
                    className="h-8 text-xs"
                  />
                </div>
              </div>
              {selectedDataset && mapping.hasColumns ? (
                <p className="font-mono text-[10px] text-muted-foreground">
                  컬럼 {mapping.columns.length}개 감지됨 — 다음 단계에서
                  속성으로 매핑하세요.
                </p>
              ) : null}
            </div>
          ) : null}

          {step === 2 ? (
            <div className="space-y-3">
              <p className="text-xs text-muted-foreground">
                {mapping.columns.length} columns → {includedCount} properties
                auto-mapped
              </p>
              <div className="max-h-[320px] overflow-auto rounded border">
                <table className="w-full border-collapse text-xs">
                  <thead className="sticky top-0 bg-[#f6f7f9]">
                    <tr className="text-[10px] tracking-wide text-muted-foreground uppercase">
                      <th className="w-8 px-2 py-1.5" />
                      <th className="px-2 py-1.5 text-left">컬럼</th>
                      <th className="px-2 py-1.5 text-left">속성 apiName</th>
                      <th className="px-2 py-1.5 text-left">타입</th>
                      <th className="w-10 px-2 py-1.5 text-center">PK</th>
                      <th className="w-12 px-2 py-1.5 text-center">Title</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mapping.columns.map((column) => {
                      const selection = columnSelections[column.name];
                      return (
                        <tr key={column.name} className="border-t">
                          <td className="px-2 py-1.5 text-center">
                            <Checkbox
                              checked={selection.include}
                              onCheckedChange={(value) =>
                                setSelection(column.name, {
                                  include: value === true,
                                })
                              }
                              aria-label={`${column.name} 포함`}
                            />
                          </td>
                          <td className="px-2 py-1.5 font-mono text-[11px]">
                            {column.name}
                          </td>
                          <td className="px-2 py-1.5">
                            <Input
                              value={selection.apiName}
                              disabled={!selection.include}
                              onChange={(event) =>
                                setSelection(column.name, {
                                  apiName: event.target.value,
                                })
                              }
                              className="h-7 font-mono text-[11px]"
                            />
                          </td>
                          <td className="px-2 py-1.5">
                            <Select
                              value={selection.type}
                              disabled={!selection.include}
                              onValueChange={(value) =>
                                setSelection(column.name, { type: value })
                              }
                            >
                              <SelectTrigger
                                size="sm"
                                className="h-7 w-24 text-[11px]"
                              >
                                <SelectValue />
                              </SelectTrigger>
                              <SelectContent>
                                {ALLOWED_PROPERTY_TYPES.map((type) => (
                                  <SelectItem
                                    key={type}
                                    value={type}
                                    className="text-xs"
                                  >
                                    {type}
                                  </SelectItem>
                                ))}
                              </SelectContent>
                            </Select>
                          </td>
                          <td className="px-2 py-1.5 text-center">
                            <input
                              type="radio"
                              name="new-object-pk"
                              checked={pkColumn === column.name}
                              disabled={!selection.include}
                              onChange={() => setPkColumn(column.name)}
                              aria-label={`${column.name} 기본 키`}
                              className="accent-primary"
                            />
                          </td>
                          <td className="px-2 py-1.5 text-center">
                            <input
                              type="radio"
                              name="new-object-title"
                              checked={titleColumn === column.name}
                              disabled={!selection.include}
                              onChange={() =>
                                setTitleColumn(
                                  titleColumn === column.name
                                    ? null
                                    : column.name,
                                )
                              }
                              aria-label={`${column.name} 타이틀 속성`}
                              className="accent-primary"
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>

              {pkColumn === null ? (
                <p className="rounded border border-warning/40 bg-warning/10 p-2 text-[11px] text-warning">
                  기본 키 컬럼을 지정해야 저장할 수 있습니다.
                </p>
              ) : null}

              {generatedYaml !== null || validateMutation.isRunning ? (
                <ValidationResults
                  view={validationView}
                  result={validateMutation.result}
                  error={validateMutation.error}
                  isValidating={validateMutation.isRunning}
                  requestId={validateMutation.requestId}
                />
              ) : null}
              {!canSaveToBranch ? (
                <p className="text-[11px] text-muted-foreground">
                  브랜치에 저장하려면 먼저 좌측에서 열린 브랜치를 선택하세요.
                </p>
              ) : (
                <p className="text-[11px] text-muted-foreground">
                  저장 대상 브랜치:{" "}
                  <span className="font-mono">{branchName}</span>
                </p>
              )}
            </div>
          ) : null}
        </div>

        <DialogFooter className="justify-between sm:justify-between">
          <Button
            size="sm"
            variant="outline"
            onClick={() => {
              if (step === 0) onOpenChange(false);
              else setStep((step - 1) as WizardStep);
            }}
          >
            {step === 0 ? "취소" : "이전"}
          </Button>
          <div className="flex items-center gap-2">
            {step < 2 ? (
              <Button
                size="sm"
                disabled={step === 0 ? !canGoStep1 : !canGoStep2}
                onClick={() => setStep((step + 1) as WizardStep)}
              >
                다음
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  disabled={!canGenerate}
                  onClick={handleGenerateAndValidate}
                >
                  <ShieldCheck />
                  {validateMutation.isRunning
                    ? "검증 중…"
                    : "드래프트 생성 · 검증"}
                </Button>
                <Button
                  size="sm"
                  className="bg-success text-success-foreground hover:bg-success/90"
                  disabled={!canSave}
                  onClick={() => {
                    if (generatedYaml) onSaveToBranch(generatedYaml);
                    handleOpenChange(false);
                  }}
                >
                  <Save />
                  브랜치에 저장
                </Button>
              </>
            )}
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** 스텝 진행 표시기. */
function Stepper({ step }: { step: WizardStep }) {
  return (
    <div className="flex items-center gap-2">
      {STEP_LABELS.map((label, index) => {
        const isDone = index < step;
        const isActive = index === step;
        return (
          <div key={label} className="flex items-center gap-2">
            <div
              className={cn(
                "flex size-5 items-center justify-center rounded-full text-[10px] font-medium",
                isDone
                  ? "bg-success text-success-foreground"
                  : isActive
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted text-muted-foreground",
              )}
            >
              {isDone ? <Check className="size-3" /> : index + 1}
            </div>
            <span
              className={cn(
                "text-xs",
                isActive
                  ? "font-medium text-foreground"
                  : "text-muted-foreground",
              )}
            >
              {label}
            </span>
            {index < STEP_LABELS.length - 1 ? (
              <span className="mx-1 h-px w-6 bg-border" />
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

/** 'Display name' → PascalCase apiName ('Supplier account' → 'SupplierAccount'). */
function pascalCaseApiName(value: string): string {
  return value
    .trim()
    .split(/[\s_-]+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
}
