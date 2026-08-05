import { useState } from "react";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { StatusPill } from "@/components/shared/StatusPill";
import { cn } from "@/lib/utils";

import { AppOverviewTab } from "./AppOverviewTab";
import { ClientsTab } from "./ClientsTab";
import { McpServerTab } from "./McpServerTab";
import { OAuthDemoTab } from "./OAuthDemoTab";
import { SdkVersionsTab } from "./SdkVersionsTab";
import { statusIntent } from "./developer-model";
import { useOsdkApplicationDetail } from "./use-developer-queries";

interface AppDetailPanelProps {
  appId: string;
}

type DetailTab = "overview" | "clients" | "mcp" | "sdk" | "oauth";

const DETAIL_TABS: readonly { id: DetailTab; label: string }[] = [
  { id: "overview", label: "개요 · 리소스" },
  { id: "clients", label: "클라이언트" },
  { id: "mcp", label: "Ontology MCP · Hub" },
  { id: "sdk", label: "SDK 버전" },
  { id: "oauth", label: "OAuth 데모" },
];

/** 앱 상세 컨테이너: 서브 탭(개요/클라이언트/SDK/OAuth). */
export function AppDetailPanel({ appId }: AppDetailPanelProps) {
  const detailQuery = useOsdkApplicationDetail(appId);
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");

  const detail = detailQuery.data;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="border-b px-4 pt-3">
        <div className="flex items-center gap-2">
          <h2 className="truncate text-[15px] font-semibold">
            {detail?.application.displayName ?? "애플리케이션"}
          </h2>
          {detail ? (
            <StatusPill intent={statusIntent(detail.application.status)}>
              {detail.application.status}
            </StatusPill>
          ) : null}
          <span className="truncate font-mono text-[11px] text-muted-foreground">
            {detail?.application.appApiName}
          </span>
        </div>
        <div className="mt-2 flex gap-4">
          {DETAIL_TABS.map((tab) => (
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

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        {detailQuery.isLoading ? (
          <LoadingState rowCount={6} />
        ) : detailQuery.error ? (
          <ErrorState
            error={detailQuery.error}
            onRetry={() => void detailQuery.reload()}
          />
        ) : detail ? (
          <>
            {activeTab === "overview" ? (
              <AppOverviewTab
                application={detail.application}
                resources={detail.resources}
                onResourcesSaved={() => void detailQuery.reload()}
              />
            ) : null}
            {activeTab === "clients" ? (
              <ClientsTab
                appId={appId}
                availableScopes={detail.resources.flatMap(
                  (resource) => resource.scopes,
                )}
                onClientsChanged={() => void detailQuery.reload()}
              />
            ) : null}
            {activeTab === "sdk" ? <SdkVersionsTab appId={appId} /> : null}
            {activeTab === "mcp" ? <McpServerTab appId={appId} /> : null}
            {activeTab === "oauth" ? (
              <OAuthDemoTab clients={detail.clients} />
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
