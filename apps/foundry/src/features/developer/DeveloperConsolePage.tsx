import { AppWindow } from "lucide-react";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/shared/EmptyState";
import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";

import { AppDetailPanel } from "./AppDetailPanel";
import { AppListPanel } from "./AppListPanel";
import { CreateApplicationDialog } from "./CreateApplicationDialog";
import type { OsdkApplicationRow } from "./developer-model";
import { useOsdkApplications } from "./use-developer-queries";

/**
 * OSDK / Developer Console (/developer).
 * OSDK 앱/클라이언트/리소스 연결 · SDK 버전/아티팩트/채널 · OAuth PKCE 데모.
 */
export default function DeveloperConsolePage() {
  const appsQuery = useOsdkApplications();
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [isCreateOpen, setIsCreateOpen] = useState(false);

  const apps = appsQuery.data ?? [];

  // 첫 로드 시 첫 앱을 자동 선택한다.
  useEffect(() => {
    if (selectedAppId === null && apps.length > 0) {
      setSelectedAppId(apps[0].id);
    }
  }, [apps, selectedAppId]);

  const handleCreated = (app: OsdkApplicationRow) => {
    setSelectedAppId(app.id);
    void appsQuery.reload();
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 pt-3 pb-3">
        <PageHeader
          title="Developer Console"
          description="온톨로지 위에 OSDK 앱을 발급하고 클라이언트·SDK 버전·OAuth를 관리합니다"
          actions={<RequestTelemetry />}
        />
      </div>

      <div className="flex min-h-0 flex-1">
        <AppListPanel
          apps={appsQuery.data}
          isLoading={appsQuery.isLoading}
          error={appsQuery.error}
          selectedAppId={selectedAppId}
          onSelect={setSelectedAppId}
          onCreate={() => setIsCreateOpen(true)}
          onRetry={() => void appsQuery.reload()}
        />

        {selectedAppId ? (
          <AppDetailPanel key={selectedAppId} appId={selectedAppId} />
        ) : (
          <div className="flex flex-1 items-center justify-center p-6">
            <EmptyState
              icon={AppWindow}
              title="애플리케이션을 선택하세요"
              description="좌측에서 애플리케이션을 선택하거나 새로 만들어 SDK와 OAuth를 구성하세요."
            />
          </div>
        )}
      </div>

      <CreateApplicationDialog
        open={isCreateOpen}
        onOpenChange={setIsCreateOpen}
        onCreated={handleCreated}
      />
    </div>
  );
}
