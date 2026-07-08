import type { SourceTemplate } from "@foundry-lite/sdk";
import type { LucideIcon } from "lucide-react";
import {
  Building2,
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
  capabilities: readonly string[];
  networkModes: readonly string[];
  flow: WizardFlowKind;
  isRecommended: boolean;
}

/** managed 위저드(자격증명→네트워크→sync→run)를 지원하는 소스 타입. */
const MANAGED_SOURCE_TYPES = new Set([
  "rest_api",
  "postgres_jdbc",
  "sap_odata",
  "sharepoint_graph",
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
  webhook_listener: Webhook,
  debezium_cdc: Radio,
  media_upload: Image,
  batch_file: Files,
};

/** CSV 업로드는 템플릿 API에 없지만 sources.csv.upload로 항상 동작하는 내장 타입이다. */
const CSV_TEMPLATE: WizardTemplate = {
  sourceType: "csv_upload",
  displayName: "CSV 업로드",
  capabilities: ["batch"],
  networkModes: ["direct"],
  flow: "csv",
  isRecommended: true,
};

export function buildWizardTemplates(
  templates: readonly SourceTemplate[],
): WizardTemplate[] {
  const mapped = templates.map((template) => ({
    sourceType: template.sourceType,
    displayName: sourceTypeLabel(template.sourceType),
    capabilities: template.capabilities,
    networkModes: template.networkModes,
    flow: MANAGED_SOURCE_TYPES.has(template.sourceType)
      ? ("managed" as const)
      : (ONBOARDING_FLOWS[template.sourceType] ?? ("future" as const)),
    isRecommended: false,
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
        template.sourceType.includes(query),
    );
  }, [allTemplates, search]);

  if (error) return <ErrorState error={error} onRetry={onRetry} />;
  if (isLoading && !templates) return <LoadingState rowCount={6} />;

  return (
    <div className="space-y-4">
      <h2 className="text-base font-semibold">소스 유형 선택</h2>
      <div className="space-y-2">
        <div className="section-label">소스</div>
        <p className="text-xs text-muted-foreground">
          인터넷 또는 온프레미스의 데이터를 연결하려면 아래 소스 유형 중 하나를
          선택하세요.
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
      {visibleTemplates.length === 0 ? (
        <EmptyState
          title="검색 결과가 없습니다"
          description="다른 키워드로 검색하거나 검색어를 지워보세요."
        />
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(230px,1fr))] gap-3">
          {visibleTemplates.map((template) => (
            <TemplateCard
              key={template.sourceType}
              template={template}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </div>
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
  const isFuture = template.flow === "future";
  return (
    <button
      type="button"
      disabled={isFuture}
      onClick={() => onSelect(template)}
      className={
        isFuture
          ? "cursor-not-allowed rounded border bg-card text-left opacity-60"
          : "rounded border bg-card text-left transition-colors hover:border-primary/50 hover:bg-accent/40"
      }
    >
      <div className="flex items-center gap-2 border-b p-3">
        <span className="flex size-7 shrink-0 items-center justify-center rounded bg-primary/10">
          <Icon className="size-4 text-primary" />
        </span>
        <span className="min-w-0 truncate text-[13px] font-semibold">
          {template.displayName}
        </span>
        {template.isRecommended ? (
          <StatusPill intent="info" className="ml-auto">
            추천
          </StatusPill>
        ) : null}
        {isFuture ? (
          <StatusPill intent="neutral" className="ml-auto">
            future
          </StatusPill>
        ) : null}
      </div>
      <div className="flex flex-wrap gap-1.5 p-3">
        {template.capabilities.map((capability) => (
          <span
            key={capability}
            className="rounded-full bg-muted px-2 py-0.5 text-[11px] text-muted-foreground"
          >
            {capabilityLabel(capability)}
          </span>
        ))}
      </div>
    </button>
  );
}
