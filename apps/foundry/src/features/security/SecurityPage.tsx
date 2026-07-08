import { useState } from "react";
import { useSearchParams } from "react-router";

import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AuditPanel } from "./AuditPanel";
import { AuthSessionPanel } from "./AuthSessionPanel";
import { MarkingsFuturePanel } from "./MarkingsFuturePanel";
import { ProjectGrantsPanel } from "./ProjectGrantsPanel";

const TABS = [
  { value: "grants", label: "프로젝트 권한" },
  { value: "auth", label: "인증 · 세션" },
  { value: "audit", label: "감사 관점" },
  { value: "markings", label: "Markings · 민감 데이터" },
] as const;

type TabValue = (typeof TABS)[number]["value"];

function isTabValue(value: string | null): value is TabValue {
  return value !== null && TABS.some((tab) => tab.value === value);
}

/**
 * Security & Governance (/security).
 *
 * 실동작(current): 프로젝트 권한(project grant), 인증/세션(header-trust principal
 * + OSDK OAuth 앱/스코프), 감사 관점(operations audit run).
 * future_gap: Palantir markings 엔진, 조직/스페이스 admin, 전체 정책/감사 엔진은
 * Markings 탭에서 reference/future로 정직하게 구분해 표현한다.
 */
export default function SecurityPage() {
  const [searchParams] = useSearchParams();
  const initialTab = searchParams.get("tab");
  const [tab, setTab] = useState<TabValue>(
    isTabValue(initialTab) ? initialTab : "grants",
  );

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="border-b px-4 py-3">
        <PageHeader
          title="Security & Governance"
          description="프로젝트 역할 권한, 인증/OAuth 세션, 감사 관점을 관리합니다. 마킹/조직/정책 엔진은 future입니다."
          meta={<StatusPill intent="warning">P2 · partial</StatusPill>}
        />
      </div>

      <Tabs
        value={tab}
        onValueChange={(value) => setTab(value as TabValue)}
        className="flex min-h-0 flex-1 flex-col gap-0"
      >
        <TabsList variant="line" className="h-auto shrink-0 gap-4 overflow-x-auto border-b px-4">
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
          <TabsContent value="grants" className="mt-0">
            <ProjectGrantsPanel />
          </TabsContent>
          <TabsContent value="auth" className="mt-0">
            <AuthSessionPanel />
          </TabsContent>
          <TabsContent value="audit" className="mt-0">
            <AuditPanel />
          </TabsContent>
          <TabsContent value="markings" className="mt-0">
            <MarkingsFuturePanel />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}
