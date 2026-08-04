import { useState } from "react";

import { PageHeader } from "@/components/shared/PageHeader";
import { StatusPill } from "@/components/shared/StatusPill";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

import { AgentRunConsole } from "./components/AgentRunConsole";
import { AiFdePanel } from "./components/AiFdePanel";
import { BuilderPanel } from "./components/BuilderPanel";
import { EvalReleasePanel } from "./components/EvalReleasePanel";
import { useAipWorkspace } from "./use-aip-workspace";

const GOVERNED_BOUNDARIES = [
  "production merge는 사람 승인",
  "임의 사용자 코드 실행은 별도 Functions 런타임",
  "생성된 Pilot 앱은 proposal 활성화 전 preview 전용",
] as const;

/**
 * AIP 워크스페이스: 에이전트 실행(citation + 운영 근거) / 빌더(validate·run) /
 * 평가·릴리스. AI FDE의 사람이 통제하는 production 경계를 화면에 명시한다.
 */
export default function AipPage() {
  const workspace = useAipWorkspace();
  const [tab, setTab] = useState("fde");

  return (
    <div className="space-y-4 p-4">
      <PageHeader
        title="AIP"
        description="온톨로지·데이터·운영 근거를 바탕으로 AI 에이전트와 빌더 플로우를 실행하고, 평가·릴리스를 운영 evidence와 연결합니다."
        meta={
          <StatusPill intent="info">AI FDE · agent-runtime · builder · evals</StatusPill>
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
          <TabsTrigger value="fde" className="grow-0 px-1 text-[13px]">
            AI FDE
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
        <TabsContent value="fde" className="mt-3">
          <AiFdePanel workspace={workspace} />
        </TabsContent>
        <TabsContent value="builder" className="mt-3">
          <BuilderPanel workspace={workspace} />
        </TabsContent>
        <TabsContent value="evals" className="mt-3">
          <EvalReleasePanel workspace={workspace} />
        </TabsContent>
      </Tabs>

      <div className="flex flex-wrap items-center gap-2 border-t pt-3">
        <span className="section-label">governed boundary</span>
        {GOVERNED_BOUNDARIES.map((boundary) => (
          <StatusPill key={boundary} intent="neutral">
            {boundary}
          </StatusPill>
        ))}
        <span className="text-[11px] text-muted-foreground">
          9개 AI FDE mode는 현재 catalog에서 실행되며, 승인·활성화·외부 효과의 최종 통제권은 사람과 기존 정책 계층에 남습니다.
        </span>
      </div>
    </div>
  );
}
