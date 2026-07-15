import type { SourceTemplate } from "@foundry-lite/sdk";
import type { LucideIcon } from "lucide-react";
import {
  Building2,
  Cable,
  Database,
  FileSpreadsheet,
  Files,
  FolderOpen,
  Globe,
  Image,
  Radio,
  Search,
  Webhook,
} from "lucide-react";
import { useMemo, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Input } from "@/components/ui/input";

import { capabilityLabel, sourceTypeLabel } from "../source-model";

export type WizardFlowKind =
  | "csv"
  | "managed"
  | "batch_file"
  | "webhook_listener"
  | "debezium_cdc"
  | "media_upload"
  | "future";

export interface WizardTemplate {
  sourceType: string;
  displayName: string;
  category: "protocol" | "alternative";
  description: string;
  capabilities: readonly string[];
  networkModes: readonly string[];
  flow: WizardFlowKind;
  isRecommended: boolean;
  executionStatus: "active" | "definition_only" | "future";
}

/** managed 위저드(자격증명→네트워크→sync→run)를 지원하는 소스 타입. */
const MANAGED_SOURCE_TYPES = new Set([
  "rest_api",
  "postgres_jdbc",
  "sap_odata",
  "sharepoint_graph",
  "kafka",
]);

/** sources.* 온보딩 API로 바로 생성되는 소스 타입 → 전용 flow. */
const ONBOARDING_FLOWS: Record<string, WizardFlowKind> = {
  batch_file: "batch_file",
  webhook_listener: "webhook_listener",
  debezium_cdc: "debezium_cdc",
  media_upload: "media_upload",
};

const TEMPLATE_ICONS: Record<string, LucideIcon> = {
  csv_upload: FileSpreadsheet,
  rest_api: Globe,
  postgres_jdbc: Database,
  sap_odata: Building2,
  sharepoint_graph: FolderOpen,
  kafka: Cable,
  webhook_listener: Webhook,
  debezium_cdc: Radio,
  media_upload: Image,
  batch_file: Files,
};

/** CSV 업로드는 템플릿 API에 없지만 sources.csv.upload로 항상 동작하는 내장 타입이다. */
const CSV_TEMPLATE: WizardTemplate = {
  sourceType: "csv_upload",
  displayName: "CSV 업로드",
  category: "alternative",
  description: "CSV 파일을 업로드해 바로 탐색 가능한 데이터셋으로 만듭니다.",
  capabilities: ["batch"],
  networkModes: ["direct"],
  flow: "csv",
  isRecommended: true,
  executionStatus: "active",
};

export function buildWizardTemplates(
  templates: readonly SourceTemplate[],
): WizardTemplate[] {
  const mapped = templates.map((template) => ({
    sourceType: template.sourceType,
    displayName: sourceTypeLabel(template.sourceType),
    category: template.category,
    description: template.description,
    capabilities: template.capabilities,
    networkModes: template.networkModes,
    flow: MANAGED_SOURCE_TYPES.has(template.sourceType)
      ? ("managed" as const)
      : (ONBOARDING_FLOWS[template.sourceType] ?? ("future" as const)),
    isRecommended: template.isRecommended,
    executionStatus: template.executionStatus,
  }));
  return [CSV_TEMPLATE, ...mapped];
}

interface TemplatePickerStepProps {
  templates: readonly SourceTemplate[] | null;
  isLoading: boolean;
  error: unknown;
  onRetry: () => void;
  onSelect: (template: WizardTemplate) => void;
}

/** 소스 타입 선택 그리드 (Palantir New Source 화면 구조). */
export function TemplatePickerStep({
  templates,
  isLoading,
  error,
  onRetry,
  onSelect,
}: TemplatePickerStepProps) {
  const [search, setSearch] = useState("");
  const allTemplates = useMemo(
    () => buildWizardTemplates(templates ?? []),
    [templates],
  );
  const visibleTemplates = useMemo(() => {
    const query = search.trim().toLowerCase();
    if (!query) return allTemplates;
    return allTemplates.filter(
      (template) =>
        template.displayName.toLowerCase().includes(query) ||
        template.sourceType.includes(query) ||
        template.description.toLowerCase().includes(query) ||
        template.capabilities.some((capability) => capability.includes(query)),
    );
  }, [allTemplates, search]);
  const protocolTemplates = visibleTemplates.filter(
    (template) => template.category === "protocol",
  );
  const alternativeTemplates = visibleTemplates.filter(
    (template) => template.category === "alternative",
  );

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isLoading && !templates) return <LoadingState rowCount={6} />;

  return (
    <div className="mx-auto w-full max-w-[1050px] space-y-8 pb-10">
      <div className="space-y-4">
        <div>
          <h2 className="text-[18px] font-semibold tracking-[-0.01em]">
            소스 유형 선택
          </h2>
          <p className="mt-1 text-[13px] text-muted-foreground">
            연결할 시스템과 가장 가까운 connector를 선택하세요.
          </p>
        </div>
        <div className="relative max-w-xl">
          <Search className="absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="소스 유형 검색"
            className="h-8 pl-8 text-xs"
          />
        </div>
      </div>
      {visibleTemplates.length === 0 ? (
        <EmptyState
          title="검색 결과가 없습니다"
          description="다른 키워드로 검색하거나 검색어를 지워보세요."
        />
      ) : (
        <div className="space-y-10">
          <TemplateSection
            title="프로토콜 소스"
            description="데이터베이스, SaaS, API, 이벤트 시스템에 지속적으로 연결합니다."
            templates={protocolTemplates}
            onSelect={onSelect}
          />
          <TemplateSection
            title="연결하는 다른 방법"
            description="내 컴퓨터의 파일이나 미디어를 직접 가져옵니다."
            templates={alternativeTemplates}
            onSelect={onSelect}
          />
        </div>
      )}
    </div>
  );
}

function TemplateSection({
  title,
  description,
  templates,
  onSelect,
}: {
  title: string;
  description: string;
  templates: readonly WizardTemplate[];
  onSelect: (template: WizardTemplate) => void;
}) {
  if (templates.length === 0) return null;

  return (
    <section className="space-y-4" aria-labelledby={`source-section-${title}`}>
      <div>
        <h3
          id={`source-section-${title}`}
          className="text-[16px] font-semibold tracking-[-0.01em]"
        >
          {title}
        </h3>
        <p className="mt-1 text-[13px] text-muted-foreground">{description}</p>
      </div>
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {templates.map((template) => (
          <TemplateCard
            key={template.sourceType}
            template={template}
            onSelect={onSelect}
          />
        ))}
      </div>
    </section>
  );
}

function TemplateCard({
  template,
  onSelect,
}: {
  template: WizardTemplate;
  onSelect: (template: WizardTemplate) => void;
}) {
  const Icon = TEMPLATE_ICONS[template.sourceType] ?? Database;
  const isFuture =
    template.flow === "future" || template.executionStatus === "future";
  return (
    <button
      type="button"
      data-testid={`source-template-${template.sourceType}`}
      disabled={isFuture}
      onClick={() => onSelect(template)}
      className={
        isFuture
          ? "min-h-44 cursor-not-allowed rounded-[2px] border bg-card text-left opacity-60"
          : "group min-h-44 rounded-[2px] border bg-card text-left shadow-[0_1px_2px_rgba(17,20,24,0.04)] transition-[border-color,box-shadow,transform] hover:-translate-y-px hover:border-primary/60 hover:shadow-[0_3px_8px_rgba(17,20,24,0.1)] focus-visible:ring-2 focus-visible:ring-primary/30"
      }
    >
      <div className="flex items-center gap-3 border-b px-4 py-3.5">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-[2px] bg-[#E5F0FA] transition-colors group-hover:bg-[#DCEAF8]">
          <Icon className="size-[18px] text-[#137CBD]" />
        </span>
        <span className="min-w-0 truncate text-[14px] font-semibold">
          {template.displayName}
        </span>
        {template.isRecommended ? (
          <StatusPill intent="info" className="ml-auto">
            추천
          </StatusPill>
        ) : null}
        {!template.isRecommended && template.executionStatus === "active" ? (
          <StatusPill intent="success" className="ml-auto">
            실행 가능
          </StatusPill>
        ) : null}
        {template.executionStatus === "definition_only" ? (
          <StatusPill intent="warning" className="ml-auto">
            정의만
          </StatusPill>
        ) : null}
        {isFuture ? (
          <StatusPill intent="neutral" className="ml-auto">
            future
          </StatusPill>
        ) : null}
      </div>
      <div className="flex min-h-28 flex-col px-4 py-3.5">
        <p className="text-[13px] leading-5 text-muted-foreground">
          {template.description}
        </p>
        <div className="mt-auto flex flex-wrap gap-1.5 pt-4">
          {template.capabilities.map((capability) => (
            <span
              key={capability}
              className="rounded-full bg-[#EDF0F2] px-2 py-0.5 text-[11px] text-[#404854]"
            >
              {capabilityLabel(capability)}
            </span>
          ))}
        </div>
      </div>
    </button>
  );
}
