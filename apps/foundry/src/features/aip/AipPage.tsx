import { useState } from "react";

import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AgentRunConsole } from "./components/AgentRunConsole";
import { BuilderPanel } from "./components/BuilderPanel";
import { EvalReleasePanel } from "./components/EvalReleasePanel";
import { useAipWorkspace } from "./use-aip-workspace";

const FUTURE_GAPS = [
  "multi-model registry",
  "human feedback loop",
  "AIP Logic / App Builder parity",
] as const;

/**
 * AIP 워크스페이스: 에이전트 실행(citation + 운영 근거) / 빌더(validate·run) /
 * 평가·릴리스. 백엔드에 없는 기능은 future_gap 배지로 정직하게 표기한다.
 */
export default function AipPage() {
  const workspace = useAipWorkspace();
  const [tab, setTab] = useState("agent");

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        title="AIP"
        description="온톨로지·데이터·운영 근거를 바탕으로 AI 에이전트와 빌더 플로우를 실행하고, 평가·릴리스를 운영 evidence와 연결합니다."
        meta={
          <StatusPill intent="info">agent-runtime · builder · evals</StatusPill>
        }
      />

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList
          variant="line"
          className="h-9 w-full justify-start gap-4 border-b"
        >
          <TabsTrigger value="agent" className="grow-0 px-1 text-[13px]">
            에이전트 실행
          </TabsTrigger>
          <TabsTrigger value="builder" className="grow-0 px-1 text-[13px]">
            빌더
          </TabsTrigger>
          <TabsTrigger value="evals" className="grow-0 px-1 text-[13px]">
            평가 · 릴리스
          </TabsTrigger>
        </TabsList>

        <TabsContent value="agent" className="mt-3">
          <AgentRunConsole workspace={workspace} />
        </TabsContent>
        <TabsContent value="builder" className="mt-3">
          <BuilderPanel workspace={workspace} />
        </TabsContent>
        <TabsContent value="evals" className="mt-3">
          <EvalReleasePanel workspace={workspace} />
        </TabsContent>
      </Tabs>

      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <span className="section-label">future gap</span>
        {FUTURE_GAPS.map((gap) => (
          <StatusPill key={gap} intent="neutral">
            {gap}
          </StatusPill>
        ))}
        <span className="text-[11px] text-muted-foreground">
          Palantir급 model governance와 full prompt evidence UX는 일부 gap이
          있습니다.
        </span>
      </div>
    </div>
  );
}
