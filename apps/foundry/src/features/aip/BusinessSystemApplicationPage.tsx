import type {
  AipPilotOperatingApplicationBundle,
  BusinessSystemComponent,
  BusinessSystemDefinition,
  GenericObject,
} from "@foundry-lite/sdk";
import { idempotencyKey } from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { AlertTriangle, ArrowRight, Check, LoaderCircle, Search, ShieldCheck } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "react-router";

import { ErrorState } from "@/components/shared/ErrorState";
import { LoadingState } from "@/components/shared/LoadingState";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

import {
  type BusinessAction,
  actions,
  allowedActions,
  definition,
  policies,
  primaryRecord,
  records,
  screenComponents,
  status,
  title,
  visibleFields,
} from "./business-system-app-model";

type PendingAction = { item: GenericObject; action: BusinessAction };

export default function BusinessSystemApplicationPage() {
  const platform = useFoundryLiteClient();
  const { applicationId = "" } = useParams();
  const [bundle, setBundle] = useState<AipPilotOperatingApplicationBundle | null>(null);
  const [items, setItems] = useState<GenericObject[]>([]);
  const [selectedScreen, setSelectedScreen] = useState("");
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [lastResult, setLastResult] = useState<unknown>(null);
  const [search, setSearch] = useState("");
  const [isBusy, setIsBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState<unknown>(null);
  const refresh = useCallback(async (query = "") => {
    if (!bundle || bundle.operatingApplication.status !== "operating") return;
    const record = primaryRecord(definition(bundle));
    const result = await platform.aip.pilot.queryObjects(
      applicationId, record.apiName, { limit: 50, search: query || undefined },
    );
    setItems(result.items);
  }, [applicationId, bundle, platform]);

  useEffect(() => {
    let isCurrent = true;
    void platform.aip.pilot.getOperating(applicationId).then((result) => {
      if (!isCurrent) return;
      setBundle(result);
      setSelectedScreen(result.businessSystemDefinition.experience.screens[0]?.id ?? "today");
    }).catch((reason: unknown) => isCurrent && setError(reason));
    return () => { isCurrent = false; };
  }, [applicationId, platform]);

  useEffect(() => {
    void refresh().catch(setError);
  }, [refresh]);

  async function runAction(params: Record<string, string>) {
    if (!pending || isBusy) return;
    setIsBusy(true); setMessage("업무를 처리하고 있습니다.");
    try {
      const result = await platform.aip.pilot.startAction(applicationId, pending.action.apiName, {
        target: { objectType: pending.item.objectType, objectId: pending.item.objectId },
        expectedObjectVersion: pending.item.objectVersion,
        params,
      }, { idempotencyKey: idempotencyKey("business-app", `${pending.action.apiName}:${pending.item.objectId}`) });
      setLastResult(result); setPending(null); setMessage("업무를 완료했습니다."); await refresh(search);
    } catch (reason) {
      setMessage(reason instanceof Error ? reason.message : "업무를 완료하지 못했습니다.");
    } finally { setIsBusy(false); }
  }

  if (error) return <ErrorState error={error} />;
  if (!bundle) return <LoadingState rowCount={8} className="min-h-screen p-8" />;
  const contract = definition(bundle);
  if (bundle.operatingApplication.status !== "operating") return <ReleaseGate bundle={bundle} />;
  const screen = contract.experience.screens.find((item) => item.id === selectedScreen);
  return (
    <main className="min-h-screen bg-[#edf2f0] text-[#17211f] [font-family:'Pretendard','Avenir_Next',system-ui,sans-serif]">
      <header className="border-b border-[#cbd6d2] bg-[#132825] text-[#f7fbf9]">
        <div className="mx-auto grid max-w-[1440px] gap-8 px-5 py-8 lg:grid-cols-[minmax(0,1fr)_260px] lg:px-10">
          <div><p className="mb-3 font-mono text-[10px] tracking-[0.2em] text-[#86d6c4]">LIVE WORK SYSTEM</p>
            <h1 className="max-w-4xl text-3xl font-semibold leading-none tracking-[-0.04em] md:text-5xl">{contract.identity.name}</h1>
            <p className="mt-4 max-w-3xl text-sm leading-6 text-[#c7d8d3]">{contract.identity.summary}</p>
          </div>
          <div className="min-w-0 border-l border-[#41605a] pl-5 text-xs leading-5 text-[#c7d8d3]">
            <p className="flex items-center gap-2 font-semibold text-white"><ShieldCheck className="size-4 text-[#86d6c4]" />승인된 업무 정의 사용 중</p>
            <p className="mt-3 font-mono text-[10px]">ONTOLOGY {bundle.operatingApplication.ontologyVersionNumber}</p>
            <p className="truncate font-mono text-[9px]">{bundle.operatingApplication.definitionFingerprint}</p>
          </div>
        </div>
      </header>
      <nav className="sticky top-0 z-20 border-b border-[#cbd6d2] bg-[#f8fbfa]/95 backdrop-blur" aria-label="업무 화면">
        <div className="mx-auto flex max-w-[1440px] gap-1 overflow-auto px-5 py-3 lg:px-10">
          {contract.experience.screens.map((item) => <button key={item.id} onClick={() => setSelectedScreen(item.id)}
            className={`whitespace-nowrap border-b-2 px-4 py-2 text-xs font-semibold transition ${item.id === selectedScreen ? "border-[#176b70] text-[#123b3c]" : "border-transparent text-[#687875] hover:text-[#123b3c]"}`}>{item.title}</button>)}
        </div>
      </nav>
      <section className="mx-auto grid max-w-[1440px] gap-4 px-5 py-6 lg:grid-cols-12 lg:px-10">
        {screenComponents(screen).map((component) => <WorkComponent key={component.id} component={component}
          contract={contract} items={items} lastResult={lastResult} isBusy={isBusy} onSelectAction={(item, action) => setPending({ item, action })}
          search={search} onSearch={setSearch} onRefresh={() => void refresh(search).catch(setError)} />)}
      </section>
      {message ? <div className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2 border border-[#35504b] bg-[#132825] px-5 py-3 text-xs text-white shadow-xl" role="status">{message}</div> : null}
      {pending ? <ActionConfirmation pending={pending} isBusy={isBusy} onCancel={() => setPending(null)} onRun={runAction} /> : null}
    </main>
  );
}

function ReleaseGate({ bundle }: { bundle: AipPilotOperatingApplicationBundle }) {
  return <main className="grid min-h-screen place-items-center bg-[#edf2f0] p-6 text-[#17211f]"><section className="w-full max-w-2xl border border-[#cbd6d2] bg-white p-8 shadow-[0_24px_80px_-55px_#132825]">
    <p className="font-mono text-[10px] tracking-[0.18em] text-[#8d611e]">HUMAN RELEASE GATE</p>
    <h1 className="mt-4 text-3xl font-semibold tracking-[-0.04em]">{bundle.applicationName}</h1>
    <p className="mt-3 text-sm leading-6 text-[#5e6f6b]">앱 주소는 준비됐지만, 승인된 업무 구조와 권한이 모두 확인되기 전에는 실제 데이터를 열거나 Action을 실행하지 않습니다.</p>
    <ul className="mt-7 grid gap-3">{bundle.operatingApplication.blockers.map((blocker) => <li key={blocker.code} className="flex gap-3 border-l-2 border-[#c38a30] bg-[#fbf6eb] p-4 text-sm"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-[#a26812]" /><span>{blocker.message}</span></li>)}</ul>
    <p className="mt-7 font-mono text-[9px] text-[#71817d]">{bundle.operatingApplication.operatingPath} · rollback follows active Ontology</p>
  </section></main>;
}

type WorkComponentProps = {
  component: BusinessSystemComponent; contract: BusinessSystemDefinition; items: GenericObject[]; lastResult: unknown;
  isBusy: boolean; search: string; onSearch(value: string): void; onRefresh(): void;
  onSelectAction(item: GenericObject, action: BusinessAction): void;
};

function WorkComponent(props: WorkComponentProps) {
  const { component } = props;
  const wide = ["work_queue", "record_detail", "action_form", "evidence_panel", "audit_timeline"].includes(component.kind);
  return <article className={`${wide ? "lg:col-span-8" : "lg:col-span-4"} border border-[#cbd6d2] bg-[#f9fcfb] p-5 shadow-[0_16px_45px_-42px_#132825]`}>
    <div className="mb-4 flex items-center justify-between gap-3"><h2 className="text-sm font-semibold tracking-[-0.01em]">{component.title}</h2><span className="font-mono text-[8px] uppercase tracking-widest text-[#7b8b87]">{component.kind.replaceAll("_", " ")}</span></div>
    {component.kind === "work_queue" ? <WorkQueue {...props} /> : null}
    {["record_detail", "action_form"].includes(component.kind) ? <RecordCards {...props} /> : null}
    {component.kind === "action_panel" ? <NextAction {...props} /> : null}
    {component.kind === "ai_suggestion_panel" ? <Suggestion {...props} /> : null}
    {["policy_panel", "approval_inbox"].includes(component.kind) ? <PolicyList contract={props.contract} approvalsOnly={component.kind === "approval_inbox"} /> : null}
    {["evidence_panel", "audit_timeline", "status_timeline"].includes(component.kind) ? <Evidence result={props.lastResult} /> : null}
    {component.kind === "relationship_graph" ? <p className="text-sm text-[#52645f]">{records(props.contract).map((item) => item.displayName).join("  →  ")}</p> : null}
    {component.kind === "kpi_summary" ? <p className="text-4xl font-semibold tracking-[-0.05em]">{props.items.length}<span className="ml-2 text-xs font-normal text-[#657570]">현재 업무</span></p> : null}
  </article>;
}

function WorkQueue(props: WorkComponentProps) {
  return <><div className="mb-4 flex gap-2"><Input value={props.search} onChange={(event) => props.onSearch(event.target.value)} placeholder="업무 이름이나 내용 검색" className="rounded-none border-[#aebdb8] bg-white" /><Button variant="outline" onClick={props.onRefresh} disabled={props.isBusy} className="rounded-none"><Search />검색</Button></div><RecordCards {...props} /></>;
}

function RecordCards(props: WorkComponentProps) {
  const record = primaryRecord(props.contract);
  if (!props.items.length) return <p className="border border-dashed border-[#b8c5c1] p-8 text-center text-sm text-[#667672]">지금 처리할 업무가 없습니다.</p>;
  return <div className="grid gap-px bg-[#cbd6d2]">{props.items.map((item) => <section key={item.objectId} className="bg-white p-4"><div className="flex items-start justify-between gap-4"><div><h3 className="font-semibold">{title(item)}</h3><p className="mt-1 font-mono text-[9px] text-[#75847f]">{item.objectId}</p></div><span className="bg-[#dff0eb] px-2 py-1 text-[10px] font-semibold text-[#155a50]">{status(item)}</span></div>
    <dl className="mt-4 grid gap-2 text-xs">{visibleFields(record).slice(0, 5).map((field) => <div key={field.apiName} className="grid grid-cols-[120px_1fr] gap-3"><dt className="text-[#71807c]">{field.displayName}</dt><dd className="m-0 break-words">{String(item.properties[field.apiName] ?? "입력되지 않음")}</dd></div>)}</dl>
    <div className="mt-4 flex flex-wrap gap-2">{allowedActions(props.contract, item).map((action) => <Button key={action.apiName} size="sm" onClick={() => props.onSelectAction(item, action)} disabled={props.isBusy} className="rounded-none bg-[#176b70] hover:bg-[#12565a]">{action.displayName}<ArrowRight /></Button>)}</div>
  </section>)}</div>;
}

function NextAction(props: WorkComponentProps) {
  const item = props.items[0]; const action = item ? allowedActions(props.contract, item)[0] : null;
  return action && item ? <button onClick={() => props.onSelectAction(item, action)} className="group flex w-full items-center justify-between border-l-2 border-[#176b70] bg-[#e9f3f0] p-4 text-left"><span><strong className="block text-sm">{action.displayName}</strong><small className="mt-1 block text-[#5c6d68]">{title(item)}</small></span><ArrowRight className="size-4 transition group-hover:translate-x-1" /></button> : <p className="text-sm text-[#697975]">현재 상태에서 이어서 할 업무가 없습니다.</p>;
}

function Suggestion(props: WorkComponentProps) {
  const item = props.items[0]; const action = item ? allowedActions(props.contract, item)[0] : null;
  return <div className="border-l-2 border-[#c38a30] pl-4"><p className="text-sm font-medium">{action && item ? `${title(item)}의 다음 업무는 “${action.displayName}”입니다.` : "지금 제안할 다음 업무가 없습니다."}</p><p className="mt-2 text-xs leading-5 text-[#687874]">현재 상태와 허용된 업무 규칙만 근거로 제안했습니다. 실행 전에는 내용을 다시 확인할 수 있습니다.</p></div>;
}

function PolicyList({ contract, approvalsOnly }: { contract: BusinessSystemDefinition; approvalsOnly: boolean }) {
  const important = actions(contract).filter((action) => action.requiresApproval);
  const values = approvalsOnly ? important.map((item) => ({ name: item.displayName, statement: "실행 전에 사람이 내용을 확인합니다." })) : policies(contract);
  return <ul className="grid gap-3">{values.map((item) => <li key={item.name} className="border-t border-[#d8e1de] pt-3 first:border-0 first:pt-0"><strong className="text-xs">{item.name}</strong><p className="mt-1 text-xs leading-5 text-[#64746f]">{item.statement}</p></li>)}</ul>;
}

function Evidence({ result }: { result: unknown }) {
  return result ? <pre className="max-h-56 overflow-auto bg-[#132825] p-4 font-mono text-[10px] leading-5 text-[#d5e4df]">{JSON.stringify(result, null, 2)}</pre> : <p className="text-sm leading-6 text-[#64746f]">이 화면에서 처리한 결과와 변경 증거가 여기에 이어집니다.</p>;
}

function ActionConfirmation({ pending, isBusy, onCancel, onRun }: { pending: PendingAction; isBusy: boolean; onCancel(): void; onRun(values: Record<string, string>): void }) {
  function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); onRun(Object.fromEntries([...data].map(([key, value]) => [key, String(value)]))); }
  return <div className="fixed inset-0 z-50 grid place-items-end bg-[#0b1917]/45 p-0 backdrop-blur-sm md:place-items-center md:p-6"><form onSubmit={submit} className="w-full max-w-xl border border-[#9aaaa5] bg-[#f9fcfb] p-6 shadow-2xl"><p className="font-mono text-[9px] tracking-[0.18em] text-[#8d611e]">{pending.action.requiresApproval ? "HUMAN APPROVAL REQUIRED" : "CONFIRM WORK"}</p><h2 className="mt-3 text-2xl font-semibold tracking-[-0.04em]">{pending.action.displayName}</h2><p className="mt-2 text-sm text-[#60706c]">{title(pending.item)} · 현재 상태 {status(pending.item)}</p>
    <div className="mt-6 grid gap-4">{pending.action.parameters.map((parameter) => <label key={parameter.apiName} className="grid gap-2 text-xs font-medium">{parameter.displayName}<Input name={parameter.apiName} required className="rounded-none border-[#9facaa] bg-white" /></label>)}</div>
    <div className="mt-7 flex justify-end gap-2"><Button type="button" variant="outline" onClick={onCancel} disabled={isBusy} className="rounded-none">취소</Button><Button type="submit" disabled={isBusy} className="rounded-none bg-[#176b70] hover:bg-[#12565a]">{isBusy ? <LoaderCircle className="animate-spin" /> : <Check />}{pending.action.requiresApproval ? "확인하고 실행" : "업무 실행"}</Button></div>
  </form></div>;
}
