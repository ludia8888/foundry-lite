import { useEffect, useMemo, useState } from "react";
import { useLocation, useSearchParams } from "react-router";

import { PageHeader } from "@/components/shared/PageHeader";
import { RequestTelemetry } from "@/components/shared/RequestTelemetry";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { runTypeFromRunId } from "@/lib/operations-links";

import { MaintenancePanel } from "./MaintenancePanel";
import { OperationsHome } from "./OperationsHome";
import { RecordDlqQueue } from "./RecordDlqQueue";
import { RecoveryPanel } from "./RecoveryPanel";
import { RunInvestigation } from "./RunInvestigation";
import { WritebackReconciliation } from "./WritebackReconciliation";

const TABS = [
  { value: "home", label: "운영 홈" },
  { value: "runs", label: "Run 조사" },
  { value: "dlq", label: "Record DLQ" },
  { value: "reconciliation", label: "Writeback 조정" },
  { value: "maintenance", label: "유지보수" },
  { value: "recovery", label: "복구" },
] as const;

type TabValue = (typeof TABS)[number]["value"];

const RUN_TYPES = new Set([
  "sync",
  "transform",
  "index",
  "action",
  "action_writeback",
  "materialization",
  "outbox",
  "dead_letter",
  "workflow",
  "ai",
  "audit",
]);

function decodePathSegment(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

/**
 * operationsPath 딥링크를 해석한다.
 * 다른 화면의 "운영 증거 보기" 링크는 다음 형태로 도착한다:
 *   - ?path=/operations/runs/{runType}/{runId}  (또는 /api/... 프리픽스)
 *   - 경로 세그먼트 기반 문자열
 * runs 딥링크면 초기 탭/선택 run을 세팅한다.
 */
function parseOperationsDeepLink(rawPath: string | null): {
  tab: TabValue;
  runType: string | null;
  runId: string | null;
} {
  if (!rawPath) return { tab: "home", runType: null, runId: null };
  const normalized = rawPath.startsWith("/api/") ? rawPath.slice(4) : rawPath;
  const segments = normalized.split("?")[0].split("/").filter(Boolean);
  // .../operations/runs/{runType}/{runId}
  const runsIndex = segments.indexOf("runs");
  if (runsIndex >= 0) {
    const runType = decodePathSegment(segments[runsIndex + 1]);
    const runId = decodePathSegment(segments[runsIndex + 2]);
    if (runType && RUN_TYPES.has(runType) && runId) {
      return { tab: "runs", runType, runId };
    }
    return { tab: "runs", runType: null, runId: null };
  }
  if (segments.includes("dead-letter-records") || segments.includes("dlq")) {
    return { tab: "dlq", runType: null, runId: null };
  }
  if (segments.includes("reconciliation")) {
    return { tab: "reconciliation", runType: null, runId: null };
  }
  if (segments.includes("recovery") || segments.includes("backup-restore")) {
    return { tab: "recovery", runType: null, runId: null };
  }
  if (segments.includes("maintenance")) {
    return { tab: "maintenance", runType: null, runId: null };
  }
  return { tab: "home", runType: null, runId: null };
}

export default function OperationsPage() {
  const [searchParams] = useSearchParams();
  const { pathname } = useLocation();

  const deepLink = useMemo(() => {
    const explicitTab = searchParams.get("tab");
    const explicitRunId = searchParams.get("runId");
    const explicitRunType =
      searchParams.get("runType") ??
      (explicitRunId ? runTypeFromRunId(explicitRunId) : null);
    if (explicitRunId && explicitRunType) {
      return {
        tab: "runs" as const,
        runType: explicitRunType,
        runId: explicitRunId,
      };
    }
    const path = searchParams.get("path") ?? pathname;
    const parsed = parseOperationsDeepLink(path);
    if (explicitTab && TABS.some((tab) => tab.value === explicitTab)) {
      return { ...parsed, tab: explicitTab as TabValue };
    }
    return parsed;
  }, [pathname, searchParams]);

  const [tab, setTab] = useState<TabValue>(deepLink.tab);

  useEffect(() => {
    setTab(deepLink.tab);
  }, [deepLink.tab]);

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <PageHeader
          title="Platform Operations"
          description="run 조사, Record DLQ, Writeback 조정, 유지보수, 복구를 한 곳에서 운영합니다."
          actions={<RequestTelemetry />}
        />
      </div>

      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as TabValue)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList
          variant="line"
          className="h-auto shrink-0 gap-4 border-b px-4"
        >
          {TABS.map((item) => (
            <TabsTrigger
              key={item.value}
              value={item.value}
              className="px-0 pb-2 text-[13px]"
            >
              {item.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <div className="min-h-0 flex-1 overflow-auto bg-canvas p-4">
          <TabsContent value="home" className="mt-0">
            <OperationsHome
              onOpenRuns={() => setTab("runs")}
              onOpenDlq={() => setTab("dlq")}
              onOpenRecovery={() => setTab("recovery")}
            />
          </TabsContent>
          <TabsContent value="runs" className="mt-0 h-full">
            <RunInvestigation
              initialRunType={deepLink.runType}
              initialRunId={deepLink.runId}
            />
          </TabsContent>
          <TabsContent value="dlq" className="mt-0 h-full">
            <RecordDlqQueue />
          </TabsContent>
          <TabsContent value="reconciliation" className="mt-0 h-full">
            <WritebackReconciliation />
          </TabsContent>
          <TabsContent value="maintenance" className="mt-0">
            <MaintenancePanel />
          </TabsContent>
          <TabsContent value="recovery" className="mt-0">
            <RecoveryPanel />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
