import { Database, Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { AgentHealthPanel } from "./AgentHealthPanel";
import { NetworkPolicyPanel } from "./NetworkPolicyPanel";
import { SourceDetailPanel } from "./SourceDetailPanel";
import { SourceListPanel } from "./SourceListPanel";
import { SyncsPanel } from "./SyncsPanel";
import { useSourceConnections } from "./use-source-queries";
import type { WizardCompletion } from "./wizard/CsvUploadFlow";
import { SourceWizard } from "./wizard/SourceWizard";

type PanelTab = "sources" | "syncs" | "agents" | "policies";

const PANEL_TABS: readonly { id: PanelTab; label: string }[] = [
  { id: "sources", label: "소스" },
  { id: "syncs", label: "동기화" },
  { id: "agents", label: "에이전트" },
  { id: "policies", label: "네트워크 정책" },
];

/**
 * Data Connection (소스 온보딩): 소스 목록/상세, 관리형 sync run 증거,
 * 에이전트 상태, 네트워크 정책 + 새 소스 위저드.
 */
export default function DataConnectionPage() {
  const [isWizardOpen, setIsWizardOpen] = useState(false);
  const [initialWizardSourceType, setInitialWizardSourceType] = useState<
    string | null
  >(null);
  const [activeTab, setActiveTab] = useState<PanelTab>("sources");
  const [selectedSourceName, setSelectedSourceName] = useState<string | null>(
    null,
  );
  const [focusedSyncName, setFocusedSyncName] = useState<string | null>(null);
  const sourcesQuery = useSourceConnections();
  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    const sourceType = searchParams.get("newSourceType");
    if (!sourceType) return;
    setInitialWizardSourceType(sourceType);
    setIsWizardOpen(true);

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("newSourceType");
    setSearchParams(nextParams, { replace: true });
  }, [searchParams, setSearchParams]);

  const openWizard = (sourceType: string | null = null) => {
    setInitialWizardSourceType(sourceType);
    setIsWizardOpen(true);
  };

  const closeWizard = () => {
    setInitialWizardSourceType(null);
    setIsWizardOpen(false);
  };

  const handleWizardComplete = (completion: WizardCompletion) => {
    closeWizard();
    void sourcesQuery.reload();
    if (completion.syncName) {
      setFocusedSyncName(completion.syncName);
      setActiveTab("syncs");
      return;
    }
    if (completion.sourceName) {
      setSelectedSourceName(completion.sourceName);
      setActiveTab("sources");
    }
  };

  if (isWizardOpen) {
    return (
      <SourceWizard
        initialSourceType={initialWizardSourceType}
        onCancel={closeWizard}
        onComplete={handleWizardComplete}
      />
    );
  }

  const sources = sourcesQuery.data ?? [];
  const selectedSource =
    sources.find((source) => source.sourceName === selectedSourceName) ??
    sources[0] ??
    null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 pt-3">
        <PageHeader
          title="Data Connection"
          description="외부 시스템을 연결하고 소스·자격 증명·sync 실행 증거를 관리합니다"
          actions={
            <>
              <RequestTelemetry />
              <Button size="sm" onClick={() => openWizard()}>
                <Plus className="size-3.5" /> 새 소스
              </Button>
            </>
          }
        />
        <div className="mt-2 flex flex-wrap gap-x-4 gap-y-2">
          {PANEL_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "-mb-px border-b-2 pb-2 text-xs font-medium",
                activeTab === tab.id
                  ? "border-primary text-primary"
                  : "border-transparent text-muted-foreground hover:text-foreground",
              )}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1">
        {activeTab === "sources" ? (
          <div className="flex h-full min-h-0 flex-col lg:flex-row">
            <SourceListPanel
              sources={sourcesQuery.data}
              isLoading={sourcesQuery.isLoading}
              error={sourcesQuery.error}
              onRetry={() => void sourcesQuery.reload()}
              selectedSourceName={selectedSource?.sourceName ?? null}
              onSelect={setSelectedSourceName}
              onCreate={() => openWizard()}
            />
            {selectedSource ? (
              <SourceDetailPanel
                source={selectedSource}
                onSourceUpdated={async () => {
                  await sourcesQuery.reload();
                }}
                onSyncCreated={(syncName) => {
                  setFocusedSyncName(syncName);
                  setActiveTab("syncs");
                }}
              />
            ) : (
              <div className="flex flex-1 items-center justify-center p-6">
                <EmptyState
                  icon={Database}
                  title="소스를 선택하세요"
                  description="좌측 목록에서 소스를 선택하거나 새 소스를 만들어 외부 데이터를 연결하세요."
                  action={
                    <Button size="sm" onClick={() => openWizard()}>
                      <Plus className="size-3.5" /> 새 소스
                    </Button>
                  }
                />
              </div>
            )}
          </div>
        ) : null}
        {activeTab === "syncs" ? (
          <div className="h-full overflow-y-auto">
            <SyncsPanel initialSyncName={focusedSyncName} />
          </div>
        ) : null}
        {activeTab === "agents" ? (
          <div className="h-full overflow-y-auto">
            <AgentHealthPanel onCreateSource={() => openWizard()} />
          </div>
        ) : null}
        {activeTab === "policies" ? (
          <div className="h-full overflow-y-auto">
            <NetworkPolicyPanel />
          </div>
        ) : null}
      </div>
    </div>
  );
}
