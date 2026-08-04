import type { AipFdeMode } from "@foundry-lite/sdk";
import { GitBranch, Play, Rocket, Search, Send, ShieldCheck, UserRoundCheck } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";

import { EmptyState } from "@/components/shared/EmptyState";
import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { operationsRunHref } from "@/lib/operations-links";

import { phaseTone } from "../aip-model";
import type { AipWorkspace } from "../use-aip-workspace";
import { EvidenceRow, MonoChip, SectionLabel } from "./Evidence";

const DEFAULT_PROMPT =
  "외국인 여행자를 위한 Restaurant, AvailabilitySlot, Hold, Booking 객체와 예약 Action을 설계하고 검증해줘.";

export function AiFdePanel({ workspace }: { workspace: AipWorkspace }) {
  const { fdeCatalog, fdeCatalogError, runFde, executeFde } = workspace;
  const modes = useMemo(
    () => fdeCatalog?.modes.filter((mode) => mode.availability === "current") ?? [],
    [fdeCatalog],
  );
  const [workspaceRef, setWorkspaceRef] = useState("ontology-branch:");
  const [mode, setMode] = useState("ontology_editing");
  const [toolDiscovery, setToolDiscovery] = useState<"eager" | "lazy">("lazy");
  const [message, setMessage] = useState(DEFAULT_PROMPT);
  const [contextRefs, setContextRefs] = useState("");
  const [approvedToolIds, setApprovedToolIds] = useState<string[]>([]);

  const mutationTools = useMemo(
    () =>
      fdeCatalog?.tools.filter(
        (tool) => tool.modeIds.includes(mode) && tool.effect !== "READ",
      ) ?? [],
    [fdeCatalog, mode],
  );
  const handleRun = () => {
    if (!workspaceRef.trim() || !message.trim() || runFde.isRunning) return;
    void executeFde({
      workspaceRef: workspaceRef.trim(),
      userMessage: message.trim(),
      mode,
      toolDiscovery,
      approvedToolIds,
      attachedContextRefs: contextRefs
        .split(",")
        .map((ref) => ref.trim())
        .filter(Boolean),
    });
  };

  return (
    <div className="grid gap-3 xl:grid-cols-[minmax(360px,470px)_minmax(0,1fr)]">
      <div className="space-y-3">
        <div className="overflow-hidden rounded border bg-card">
          <div className="border-b bg-muted/25 px-3 py-2.5">
            <SectionLabel right={<StatusPill intent="info">branch first</StatusPill>}>
              AI FDE 설계 요청
            </SectionLabel>
            <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
              자연어 요구를 권한이 확인된 플랫폼 도구로 처리합니다. Ontology·Pipeline 변경은 branch에만 쓰고 production 반영은 사람이 결정합니다.
            </p>
          </div>
          <div className="space-y-3 p-3">
            <label className="block space-y-1.5">
              <span className="section-label">작업 리소스</span>
              <Input
                value={workspaceRef}
                onChange={(event) => setWorkspaceRef(event.target.value)}
                placeholder="ontology-branch:… / pipeline-branch:… / source:…"
                className="font-mono text-[12px]"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="section-label">도구 로딩</span>
              <Select
                value={toolDiscovery}
                onValueChange={(value) => setToolDiscovery(value as "eager" | "lazy")}
              >
                <SelectTrigger className="w-full text-[12px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="lazy">필요한 도구만 검색 (권장)</SelectItem>
                  <SelectItem value="eager">모드의 모든 도구 제공</SelectItem>
                </SelectContent>
              </Select>
            </label>
            <label className="block space-y-1.5">
              <span className="section-label">작업 mode</span>
              <Select
                value={mode}
                onValueChange={(value) => {
                  setMode(value);
                  setApprovedToolIds([]);
                }}
              >
                <SelectTrigger className="w-full text-[12px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {(modes.length > 0 ? modes : fallbackModes()).map((item) => (
                    <SelectItem key={item.modeId} value={item.modeId} className="text-[12px]">
                      {item.title}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </label>
            <label className="block space-y-1.5">
              <span className="section-label">비즈니스 요구</span>
              <Textarea
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                rows={7}
                className="resize-none text-[12px] leading-relaxed"
              />
            </label>
            <label className="block space-y-1.5">
              <span className="section-label">참고 리소스 (선택)</span>
              <Input
                value={contextRefs}
                onChange={(event) => setContextRefs(event.target.value)}
                placeholder="dataset:clean.restaurants, ontology-branch:…"
                className="font-mono text-[12px]"
              />
              <span className="text-[10px] text-muted-foreground">
                Dataset, branch, Source, Function, OSDK app, Project, Resource, Model을 권한·버전·토큰 한도 확인 후 전달합니다.
              </span>
            </label>
            <div className="rounded border border-dashed bg-muted/25 p-2.5">
              <SectionLabel>이번 실행에만 허용</SectionLabel>
              {mutationTools.length === 0 ? (
                <div className="mt-2 text-[10px] text-muted-foreground">이 모드는 읽기 전용입니다.</div>
              ) : mutationTools.map((tool) => (
                <ApprovalRow
                  key={tool.toolId}
                  checked={approvedToolIds.includes(tool.toolId)}
                  onCheckedChange={(checked) =>
                    setApprovedToolIds((current) =>
                      checked
                        ? [...new Set([...current, tool.toolId])]
                        : current.filter((item) => item !== tool.toolId),
                    )
                  }
                  title={tool.toolId}
                  detail={`${tool.description} · ${tool.confirmationPolicy}`}
                />
              ))}
            </div>
            <Button
              className="w-full"
              onClick={handleRun}
              disabled={runFde.isRunning || !workspaceRef.trim() || !message.trim()}
            >
              <Play />
              AI FDE 실행
            </Button>
          </div>
        </div>

        <PilotPanel workspace={workspace} />

        {fdeCatalog ? <ModeBoundary modes={fdeCatalog.modes} /> : null}
        {fdeCatalogError ? (
          <div className="rounded border border-destructive/30 bg-destructive/5 p-2 text-[11px] text-destructive">
            AI FDE catalog를 읽지 못했습니다. 사용자에게 ontology:validate 권한이 있는지 확인하세요.
          </div>
        ) : null}
      </div>

      <div className="space-y-3">
        <SafetyRail approvedCount={approvedToolIds.length} />
        {runFde.error ? <ErrorState error={runFde.error} onRetry={handleRun} /> : null}
        {runFde.result ? (
          <div className="rounded border bg-card p-3">
            <SectionLabel
              right={
                <StatusPill intent={phaseTone(runFde.result.runStatus)}>
                  {runFde.result.runStatus}
                </StatusPill>
              }
            >
              설계 실행 결과
            </SectionLabel>
            <div className="mt-2 space-y-0">
              <EvidenceRow label="workspace" value={<MonoChip>{runFde.result.workspaceRef}</MonoChip>} />
              <EvidenceRow label="mode" value={<MonoChip>{runFde.result.mode}</MonoChip>} />
              <EvidenceRow label="tool discovery" value={<MonoChip>{runFde.result.toolDiscovery}</MonoChip>} />
              <EvidenceRow
                label="approved tools"
                value={<MonoChip>{runFde.result.approvedToolIds.join(", ") || "read only"}</MonoChip>}
              />
              {runFde.result.operations ? (
                <EvidenceRow
                  label="operations evidence"
                  value={
                    <Link
                      to={operationsRunHref(
                        runFde.result.operations.runId,
                        runFde.result.operations.runType,
                      )}
                      className="hover:underline"
                    >
                      <MonoChip>{runFde.result.operations.runId}</MonoChip>
                    </Link>
                  }
                />
              ) : null}
            </div>
            {runFde.result.answer ? (
              <div className="mt-3 rounded border bg-muted/20 p-3 text-[12px] leading-relaxed">
                {runFde.result.answer}
              </div>
            ) : null}
            {runFde.result.structuredOperations.map((operation, index) => (
              <StructuredOperation key={`${String(operation.operationType)}-${index}`} operation={operation} />
            ))}
            {runFde.result.error ? (
              <div className="mt-3 rounded border border-destructive/30 bg-destructive/5 p-3 text-[11px]">
                <div className="font-medium text-destructive">
                  {String(runFde.result.error.reason ?? "AI FDE 실행 실패")}
                </div>
                <div className="mt-1 font-mono text-muted-foreground">
                  {String(runFde.result.error.detail ?? "Operations evidence를 확인하세요.")}
                </div>
              </div>
            ) : null}
          </div>
        ) : (
          <EmptyState
            icon={GitBranch}
            title="아직 AI FDE 실행 기록이 없습니다"
            description="왼쪽에서 모드와 명시적 작업 리소스를 선택하면 AI가 검색·설계·검증하고 모든 도구 결과를 Operations 원장에 남깁니다."
          />
        )}
      </div>
    </div>
  );
}

function ApprovalRow({
  checked,
  onCheckedChange,
  title,
  detail,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  title: string;
  detail: string;
}) {
  return (
    <label className="mt-2 flex cursor-pointer items-start gap-2 text-[11px]">
      <Checkbox checked={checked} onCheckedChange={(value) => onCheckedChange(value === true)} />
      <span>
        <span className="block font-medium text-foreground">{title}</span>
        <span className="block leading-relaxed text-muted-foreground">{detail}</span>
      </span>
    </label>
  );
}

function SafetyRail({ approvedCount }: { approvedCount: number }) {
  const stages = [
    { icon: UserRoundCheck, title: "현재 사용자 권한", detail: "별도 AI service account 없이 호출자의 권한을 적용" },
    { icon: GitBranch, title: "격리된 working copy", detail: "active Ontology 대신 명시한 branch만 변경" },
    {
      icon: ShieldCheck,
      title: "쓰기 확인",
      detail: approvedCount > 0 ? `이번 실행에 ${approvedCount}개 mutation 허용` : "읽기·검증만 허용",
    },
    {
      icon: Send,
      title: "사람의 merge 결정",
      detail: "proposal은 만들 수 있어도 승인·merge·deploy는 AI에게 미노출",
    },
  ];
  return (
    <div className="rounded border bg-card p-3">
      <SectionLabel right={<MonoChip>production unchanged</MonoChip>}>실행 안전선</SectionLabel>
      <ol className="mt-3 grid gap-0 sm:grid-cols-4">
        {stages.map((stage, index) => (
          <li key={stage.title} className="relative border-l pb-4 pl-7 last:pb-0 sm:border-l-0 sm:border-t sm:pb-0 sm:pl-0 sm:pt-7">
            <span className="absolute -left-3 top-0 grid size-6 place-items-center rounded-full border bg-background font-mono text-[10px] sm:-top-3 sm:left-0">
              {index + 1}
            </span>
            <stage.icon className="mb-1.5 size-4 text-primary" />
            <div className="text-[11px] font-medium">{stage.title}</div>
            <div className="mt-0.5 pr-3 text-[10px] leading-relaxed text-muted-foreground">{stage.detail}</div>
          </li>
        ))}
      </ol>
    </div>
  );
}

function ModeBoundary({ modes }: { modes: AipFdeMode[] }) {
  const future = modes.filter((mode) => mode.availability !== "current");
  if (future.length === 0) return null;
  return (
    <div className="rounded border bg-card p-3">
      <SectionLabel right={<StatusPill intent="neutral">future gap</StatusPill>}>
        아직 자동 설계하지 않는 영역
      </SectionLabel>
      <div className="mt-2 flex flex-wrap gap-1">
        {future.map((mode) => (
          <StatusPill key={mode.modeId} intent="neutral">
            {mode.title}
          </StatusPill>
        ))}
      </div>
    </div>
  );
}

function fallbackModes(): AipFdeMode[] {
  return [
    {
      modeId: "ontology_editing",
      title: "Ontology editing",
      description: "Branch-only ontology authoring",
      availability: "current",
      capabilities: [],
      scopePrefixes: ["ontology-branch:"],
    },
  ];
}

function StructuredOperation({ operation }: { operation: Record<string, unknown> }) {
  const isClarification = operation.operationType === "clarification";
  return (
    <div className="mt-3 rounded border border-primary/25 bg-primary/5 p-3 text-[11px]">
      <div className="flex items-center gap-2 font-medium">
        {isClarification ? <Search className="size-3.5" /> : <ShieldCheck className="size-3.5" />}
        {isClarification ? "확인이 필요한 질문" : "구조화 실행 계획"}
      </div>
      <pre className="mt-2 overflow-auto whitespace-pre-wrap font-mono text-[10px] text-muted-foreground">
        {JSON.stringify(operation, null, 2)}
      </pre>
    </div>
  );
}

function PilotPanel({ workspace }: { workspace: AipWorkspace }) {
  const [applicationName, setApplicationName] = useState("Dining Concierge");
  const [domainDescription, setDomainDescription] = useState(
    "외국인 여행자가 식당을 검색하고 좌석을 hold한 뒤 예약·취소할 수 있는 운영 앱",
  );
  const { planPilot, generatePilot } = workspace;
  return (
    <div className="rounded border bg-card p-3">
      <SectionLabel right={<StatusPill intent="info">Pilot</StatusPill>}>
        실행 가능한 앱 생성
      </SectionLabel>
      <p className="mt-1 text-[10px] leading-relaxed text-muted-foreground">
        한 번의 명시적 생성으로 Project, seed Dataset, Ontology branch, OSDK app, React 소스와 CI 계약을 함께 만듭니다.
      </p>
      <div className="mt-3 space-y-2">
        <Input value={applicationName} onChange={(event) => setApplicationName(event.target.value)} />
        <Textarea
          value={domainDescription}
          onChange={(event) => setDomainDescription(event.target.value)}
          rows={3}
          className="resize-none text-[11px]"
        />
        <Button
          variant="outline"
          className="w-full"
          disabled={planPilot.isRunning || !applicationName.trim() || !domainDescription.trim()}
          onClick={() =>
            void planPilot.execute({
              applicationName: applicationName.trim(),
              domainDescription: domainDescription.trim(),
            })
          }
        >
          <Search /> 생성 계획 만들기
        </Button>
        {planPilot.result ? (
          <Button
            className="w-full"
            disabled={generatePilot.isRunning}
            onClick={() => {
              if (planPilot.result) void generatePilot.execute(planPilot.result);
            }}
          >
            <Rocket /> Branch-first 앱 생성 승인
          </Button>
        ) : null}
        {generatePilot.result ? (
          <div className="rounded border border-success/30 bg-success/5 p-2 text-[10px]">
            <div className="font-medium">{generatePilot.result.applicationName} 생성 완료</div>
            <div className="mt-1 font-mono text-muted-foreground">
              {generatePilot.result.applicationPath}
            </div>
          </div>
        ) : null}
        {planPilot.error ? <ErrorState error={planPilot.error} /> : null}
        {generatePilot.error ? <ErrorState error={generatePilot.error} /> : null}
      </div>
    </div>
  );
}
