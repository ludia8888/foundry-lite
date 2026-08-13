import type { AipPilotDomainBrief, AipPilotPlan } from "@foundry-lite/sdk";
import { ArrowRight, Blocks, CircleCheckBig, FileClock, Rocket, Search, ShieldCheck, UsersRound } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { StatusPill } from "@/components/shared/StatusPill";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

import type { AipWorkspace } from "../use-aip-workspace";
import { SectionLabel } from "./Evidence";

const DEFAULT_DESCRIPTION =
  "고객이 원하는 시간과 인원을 선택하면 좌석 운영 규칙을 확인하고, 예약 확정·결제·취소와 변경 이력을 한곳에서 처리합니다.";
const DEFAULT_RECORDS = [
  "예약 (Reservation) | 고객명, 인원수:숫자, 방문시간:날짜시간",
  "테이블 (DiningTable) | 좌석수:숫자, 구역",
].join("\n");
const DEFAULT_ACTIONS = [
  "예약 접수 (RequestReservation) | 요청됨 → 확인중 | 고객 연락처 | 고객, 홀 직원",
  "예약 확정 (ConfirmReservation) | 확인중 → 확정됨 | 확인 메모 | 매니저, 홀 직원",
  "예약 취소 (CancelReservation) | 확정됨 → 취소됨 | 취소 사유 | 고객, 매니저 | 사람 확인",
].join("\n");
const DEFAULT_POLICIES = [
  "차단 | 운영 시간 중복 | 같은 테이블의 이용 시간이 겹치면 예약할 수 없습니다. | 예약 변경 기록",
  "사람 확인 | 큰 모임 | 8명 이상 예약은 매니저가 한 번 확인합니다. | 확인 담당자와 시각",
].join("\n");

export function DomainOsStudioPanel({ workspace }: { workspace: AipWorkspace }) {
  const [applicationName, setApplicationName] = useState("Dining Concierge");
  const [domainDescription, setDomainDescription] = useState(DEFAULT_DESCRIPTION);
  const [actors, setActors] = useState("고객, 매니저, 홀 직원");
  const [records, setRecords] = useState(DEFAULT_RECORDS);
  const [states, setStates] = useState("요청됨 → 확인중 → 확정됨 → 방문완료 → 취소됨");
  const [actions, setActions] = useState(DEFAULT_ACTIONS);
  const [policies, setPolicies] = useState(DEFAULT_POLICIES);
  const [evidence, setEvidence] = useState("요청 시각, 규칙 판정 결과, 담당자, 상태 변경 전후 값");
  const [integrations, setIntegrations] = useState("예약 DB, 결제 서비스, 문자 알림");
  const [successMeasures, setSuccessMeasures] = useState("중복 예약 0건, 예약 처리 2분 이내");
  const { planPilot, generatePilot } = workspace;
  const domainBrief = useMemo(
    () => buildDomainBrief({ actors, records, states, actions, policies, evidence, integrations, successMeasures }),
    [actions, actors, evidence, integrations, policies, records, states, successMeasures],
  );
  const blueprint = blueprintView(planPilot.result);
  const canPlan = applicationName.trim().length > 0 && domainDescription.trim().length >= 20;
  const canGenerate = blueprint?.readiness.isReady === true;

  return (
    <section className="overflow-hidden rounded-xl border border-slate-300/70 bg-card shadow-[0_12px_30px_-24px_rgba(15,23,42,0.65)] dark:border-slate-700">
      <header className="border-b border-slate-200 bg-slate-950 px-4 py-4 text-slate-50 dark:border-slate-800">
        <SectionLabel right={<StatusPill intent="info">Domain OS Studio</StatusPill>}>
          내 업무를 앱으로 설계하기
        </SectionLabel>
        <p className="mt-2 max-w-[68ch] text-[11px] leading-5 text-slate-300">
          개발 용어 대신 실제 업무를 적으세요. 먼저 사람이 검토할 업무 지도를 만들고, 빈칸이 없을 때만 테스트 앱을 생성합니다.
        </p>
      </header>

      <div className="space-y-4 p-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="앱 이름" hint="팀에서 바로 알아볼 이름">
            <Input value={applicationName} onChange={(event) => setApplicationName(event.target.value)} />
          </Field>
          <Field label="이 앱이 끝내야 하는 일" hint="누가, 무엇을, 어떤 결과까지 처리하는지">
            <Textarea
              value={domainDescription}
              onChange={(event) => setDomainDescription(event.target.value)}
              rows={3}
              className="resize-none text-[11px]"
            />
          </Field>
        </div>

        <OperatingMap />

        <div className="grid gap-3 lg:grid-cols-2">
          <Field label="이 업무에 참여하는 사람" hint="쉼표로 구분 · 예: 고객, 담당자, 승인자">
            <Input value={actors} onChange={(event) => setActors(event.target.value)} />
          </Field>
          <Field label="기록이 거치는 상태" hint="처음부터 끝까지 화살표로 연결">
            <Input value={states} onChange={(event) => setStates(event.target.value)} />
          </Field>
          <Field label="계속 추적할 업무 기록" hint="한 줄에 하나 · 이름 | 꼭 필요한 정보">
            <Textarea value={records} onChange={(event) => setRecords(event.target.value)} rows={4} className="text-[11px]" />
          </Field>
          <Field label="사람이 누를 업무 버튼" hint="버튼 이름 | 이전 상태 → 다음 상태 | 받을 정보 | 누를 사람 | 사람 확인">
            <Textarea value={actions} onChange={(event) => setActions(event.target.value)} rows={4} className="text-[11px]" />
          </Field>
          <Field label="반드시 지킬 규칙과 예외" hint="차단·경고·사람 확인 | 규칙 이름 | 설명 | 남길 증거">
            <Textarea value={policies} onChange={(event) => setPolicies(event.target.value)} rows={4} className="text-[11px]" />
          </Field>
          <div className="space-y-3">
            <Field label="나중에 확인할 증거" hint="누가 언제 무엇을 바꿨는지 판단할 정보">
              <Input value={evidence} onChange={(event) => setEvidence(event.target.value)} />
            </Field>
            <Field label="연결할 기존 시스템" hint="없으면 비워도 됩니다">
              <Input value={integrations} onChange={(event) => setIntegrations(event.target.value)} />
            </Field>
            <Field label="잘 작동한다고 판단할 기준" hint="예: 누락 0건, 처리 시간 5분 이내">
              <Input value={successMeasures} onChange={(event) => setSuccessMeasures(event.target.value)} />
            </Field>
          </div>
        </div>

        <Button
          variant="outline"
          className="w-full border-sky-300 bg-sky-50 text-sky-950 hover:bg-sky-100 dark:border-sky-900 dark:bg-sky-950/40 dark:text-sky-100"
          disabled={planPilot.isRunning || !canPlan}
          onClick={() =>
            void planPilot.execute({
              applicationName: applicationName.trim(),
              domainDescription: domainDescription.trim(),
              domainBrief,
            })
          }
        >
          <Search /> {planPilot.isRunning ? "업무 지도를 정리하는 중…" : "업무 설계 검토하기"}
        </Button>

        {blueprint ? <BlueprintReview blueprint={blueprint} /> : null}
        {planPilot.result ? (
          <Button
            className="w-full"
            disabled={generatePilot.isRunning || !canGenerate}
            onClick={() => {
              if (planPilot.result) void generatePilot.execute(planPilot.result);
            }}
          >
            <Rocket /> {generatePilot.isRunning ? "안전한 테스트 앱을 만드는 중…" : "검토한 설계로 테스트 앱 만들기"}
          </Button>
        ) : null}
        {generatePilot.result ? (
          <div className="rounded-lg border border-emerald-300 bg-emerald-50 p-3 text-[11px] text-emerald-950 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
            <div className="flex items-center gap-2 font-semibold"><CircleCheckBig className="size-4" /> 테스트 앱이 준비되었습니다</div>
            <p className="mt-1 text-emerald-800 dark:text-emerald-300">실제 업무에 연결하기 전까지는 안전하게 분리된 테스트 공간과 예시 데이터만 사용합니다.</p>
            <Button asChild variant="link" className="mt-1 h-auto p-0 text-emerald-900 dark:text-emerald-200">
              <Link to={generatePilot.result.applicationPath}>생성된 업무 앱 확인하기 <ArrowRight /></Link>
            </Button>
          </div>
        ) : null}
        {planPilot.error ? <ErrorState error={planPilot.error} /> : null}
        {generatePilot.error ? <ErrorState error={generatePilot.error} /> : null}
      </div>
    </section>
  );
}

function OperatingMap() {
  const steps = [
    { icon: UsersRound, label: "사람" },
    { icon: Blocks, label: "업무 기록" },
    { icon: ShieldCheck, label: "규칙" },
    { icon: Rocket, label: "업무 버튼" },
    { icon: FileClock, label: "증거" },
  ];
  return (
    <div className="grid grid-cols-5 overflow-hidden rounded-lg border border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-950/30">
      {steps.map(({ icon: Icon, label }, index) => (
        <div key={label} className="relative flex min-h-16 flex-col items-center justify-center gap-1 border-r border-slate-200 px-1 text-center last:border-r-0 dark:border-slate-800">
          <Icon className="size-4 text-sky-700 dark:text-sky-300" />
          <span className="text-[9px] font-semibold text-slate-700 dark:text-slate-200">{label}</span>
          {index < steps.length - 1 ? <ArrowRight className="absolute -right-2 z-10 size-3 rounded-full bg-slate-50 text-slate-400 dark:bg-slate-950" /> : null}
        </div>
      ))}
    </div>
  );
}

function BlueprintReview({ blueprint }: { blueprint: BlueprintView }) {
  return (
    <div className="rounded-lg border border-slate-300 bg-slate-50/70 p-3 dark:border-slate-700 dark:bg-slate-950/20">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-[11px] font-semibold">업무 설계 검토 결과</div>
        <StatusPill intent={blueprint.readiness.isReady ? "success" : "warning"}>
          {blueprint.readiness.isReady ? "앱 생성 가능" : `${blueprint.readiness.missingCount}가지 보완 필요`}
        </StatusPill>
      </div>
      <div className="mt-3 grid grid-cols-4 gap-2 text-center">
        <Count label="참여자" value={blueprint.actors.length} />
        <Count label="업무 기록" value={blueprint.records.length} />
        <Count label="업무 버튼" value={blueprint.actionCount} />
        <Count label="규칙" value={blueprint.policies.length} />
      </div>
      {blueprint.readiness.questions.length > 0 ? (
        <div className="mt-3 rounded-md border border-amber-300 bg-amber-50 p-2 text-[10px] text-amber-950 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-100">
          <div className="font-semibold">이 질문에 답하면 앱을 만들 수 있습니다</div>
          <ul className="mt-1 list-disc space-y-1 pl-4">
            {blueprint.readiness.questions.map((item) => <li key={item.field}>{item.question}</li>)}
          </ul>
        </div>
      ) : (
        <p className="mt-3 text-[10px] leading-4 text-muted-foreground">업무 순서와 규칙을 확인했습니다. 다음 단계는 실제 데이터가 아닌 예시 데이터로 테스트 앱을 만드는 일입니다.</p>
      )}
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="block text-[10px] font-semibold text-foreground">{label}</span>
      <span className="block text-[9px] leading-4 text-muted-foreground">{hint}</span>
      {children}
    </label>
  );
}

function Count({ label, value }: { label: string; value: number }) {
  return <div className="rounded-md bg-white px-2 py-2 shadow-sm dark:bg-slate-900"><div className="text-base font-semibold tabular-nums">{value}</div><div className="text-[8px] text-muted-foreground">{label}</div></div>;
}

function buildDomainBrief(values: Record<string, string>): AipPilotDomainBrief {
  return {
    actors: splitList(values.actors),
    records: parseRecords(values.records),
    lifecycleStates: splitStates(values.states),
    actions: parseActions(values.actions),
    policies: parsePolicies(values.policies),
    evidence: splitList(values.evidence),
    integrations: splitList(values.integrations),
    successMeasures: splitList(values.successMeasures),
  };
}

function parseRecords(value: string): AipPilotDomainBrief["records"] {
  return lines(value).map((line) => {
    const [rawName, rawFields = ""] = line.split("|").map((item) => item.trim());
    const named = displayAndApiName(rawName);
    return {
      name: named.displayName,
      apiName: named.apiName,
      fields: splitList(rawFields).map((field) => {
        const [name, type] = field.split(":").map((item) => item.trim());
        return { name, type: fieldType(type), required: true };
      }),
    };
  });
}

function parseActions(value: string): AipPilotDomainBrief["actions"] {
  return lines(value).map((line) => {
    const [rawName, transition = "", information = "", actors = "", approval = ""] = line.split("|").map((item) => item.trim());
    const [fromState = "", toState = ""] = transition.split(/→|->/).map((item) => item.trim());
    const named = displayAndApiName(rawName);
    return {
      name: named.displayName,
      apiName: named.apiName,
      fromStates: fromState ? [fromState] : [],
      toState,
      requiredInformation: splitList(information),
      allowedActors: splitList(actors),
      requiresApproval: /사람|승인|확인/.test(approval),
    };
  }).filter((item) => item.name && item.toState);
}

function parsePolicies(value: string): AipPilotDomainBrief["policies"] {
  return lines(value).map((line) => {
    const [kind = "차단", name = "업무 규칙", statement = "", evidence = ""] = line.split("|").map((item) => item.trim());
    return { name, statement, evidence, enforcement: policyEnforcement(kind) };
  }).filter((item) => item.statement);
}

function displayAndApiName(value: string) {
  const matched = value.match(/^(.*?)\s*\(([A-Za-z][A-Za-z0-9]*)\)\s*$/);
  return { displayName: matched?.[1]?.trim() || value.trim(), apiName: matched?.[2] };
}

function splitList(value: string) { return value.split(/,|\n/).map((item) => item.trim()).filter(Boolean); }
function lines(value: string) { return value.split("\n").map((item) => item.trim()).filter(Boolean); }
function splitStates(value: string) { return value.split(/→|->|,/).map((item) => item.trim()).filter(Boolean); }
function fieldType(value?: string): "string" | "integer" | "float" | "boolean" | "date" | "timestamp" {
  if (!value) return "string";
  if (/숫자|integer|number/.test(value)) return "integer";
  if (/금액|소수|float/.test(value)) return "float";
  if (/예아니오|boolean/.test(value)) return "boolean";
  if (/날짜시간|timestamp/.test(value)) return "timestamp";
  if (/날짜|date/.test(value)) return "date";
  return "string";
}
function policyEnforcement(value: string): "blocking" | "warning" | "manual_review" {
  if (/사람|승인|확인/.test(value)) return "manual_review";
  if (/경고/.test(value)) return "warning";
  return "blocking";
}

type BlueprintView = {
  actors: string[];
  records: Array<Record<string, unknown>>;
  policies: Array<Record<string, unknown>>;
  actionCount: number;
  readiness: { isReady: boolean; missingCount: number; questions: Array<{ field: string; question: string }> };
};

function blueprintView(plan: AipPilotPlan | null): BlueprintView | null {
  if (!plan) return null;
  const value = plan.domainOsBlueprint as Record<string, unknown>;
  const workflow = (value.workflow ?? {}) as Record<string, unknown>;
  const readiness = (value.readiness ?? {}) as Record<string, unknown>;
  return {
    actors: Array.isArray(value.actors) ? value.actors.map(String) : [],
    records: Array.isArray(value.records) ? value.records as Array<Record<string, unknown>> : [],
    policies: Array.isArray(value.policies) ? value.policies as Array<Record<string, unknown>> : [],
    actionCount: Array.isArray(workflow.actions) ? workflow.actions.length : 0,
    readiness: {
      isReady: readiness.isReady === true,
      missingCount: typeof readiness.missingCount === "number" ? readiness.missingCount : 0,
      questions: Array.isArray(readiness.questions) ? readiness.questions as Array<{ field: string; question: string }> : [],
    },
  };
}
