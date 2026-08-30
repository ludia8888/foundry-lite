"""Portable React Workshop runtime emitted for an AI FDE application bundle."""

# ruff: noqa: E501 -- Embedded TypeScript/CSS stays byte-readable in the emitted app bundle.

from __future__ import annotations

import json
from collections.abc import Mapping

JsonObject = Mapping[str, object]


def portable_workshop_application_source(
    package_name: str,
    workshop: JsonObject,
    record_fields_json: str,
    policies_json: str,
    action_forms: str,
) -> str:
    """Render an external SaaS shell from the same Workshop contract as GPT."""

    replacements = {
        "__PACKAGE_NAME__": json.dumps(f"{package_name}/react"),
        "__WORKSHOP__": json.dumps(dict(workshop), ensure_ascii=False),
        "__RECORD_FIELDS__": record_fields_json,
        "__POLICIES__": policies_json,
        "__ACTION_FORMS__": action_forms,
    }
    source = _APPLICATION_SOURCE
    for marker, value in replacements.items():
        source = source.replace(marker, value)
    return source


def portable_workshop_styles() -> str:
    """Return the responsive CSS used by the portable Workshop renderer."""

    return _STYLES


_APPLICATION_SOURCE = r"""import { useState, type CSSProperties } from "react";
import { usePilotApplicationScreen } from __PACKAGE_NAME__;

type WorkshopWidget = { id:string; kind:string; config:Record<string,unknown> };
type WorkshopSection = { id:string; title:string; layout:string; span:number; widgets:WorkshopWidget[] };
type WorkshopPage = { id:string; name:string; isDefault:boolean; sections:WorkshopSection[] };
type WorkshopApp = { name:string; purpose:string; version:number; theme:{preset:string;brandName:string;logoText:string}; shell:{navigation:string}; pages:WorkshopPage[] };
const workshop = __WORKSHOP__ as unknown as WorkshopApp;
const recordFields = __RECORD_FIELDS__ as readonly {apiName:string;displayName:string}[];
const policies = __POLICIES__ as readonly {name:string;statement:string;automationStatus:string}[];

function groupBy<Item extends {properties:object}>(items:readonly Item[], property:string) {
  const groups = new Map<string,Item[]>();
  for (const item of items) { const properties=item.properties as Record<string,unknown>;const key=String(properties[property] ?? "미지정"); groups.set(key,[...(groups.get(key) ?? []),item]); }
  return [...groups.entries()];
}
function value(config:Record<string,unknown>, key:string, fallback="") { const item=config[key]; return typeof item==="string" ? item : fallback; }
function themeVariables(preset:string):CSSProperties { const palettes:Record<string,string[]>={ocean:["#0b7285","#e6f6f8","#0d2b36"],indigo:["#4f46e5","#eeedff","#1e1b4b"],emerald:["#087f5b","#e7f7f0","#12372a"],amber:["#b45309","#fff4df","#422006"],graphite:["#475569","#eef2f6","#111827"]}; const colors=palettes[preset] ?? palettes.ocean; return {"--accent":colors[0],"--accent-soft":colors[1],"--nav":colors[2]} as CSSProperties; }

export default function App() {
  const screen = usePilotApplicationScreen();
  const firstPage=workshop.pages.find((page)=>page.isDefault) ?? workshop.pages[0];
  const [pageId,setPageId]=useState(firstPage?.id ?? ""); const [selectedId,setSelectedId]=useState("");
  const [query,setQuery]=useState(""); const [statusFilter,setStatusFilter]=useState("");
  const [message,setMessage]=useState(""); const [isRunning,setIsRunning]=useState(false);
  type Item=(typeof screen.items)[number];
  const property=(item:Item,name:string)=>(item.properties as unknown as Record<string,unknown>)[name];
  const allItems=screen.items.map((item)=>item); const filtered=allItems.filter((item)=>!query || Object.values(item.properties).some((itemValue)=>String(itemValue).toLowerCase().includes(query.toLowerCase()))).filter((item)=>!statusFilter || String(item.properties.status)===statusFilter);
  const selected=screen.items.find((item)=>item.objectId===selectedId) ?? screen.items[0];
  const currentPage=workshop.pages.find((page)=>page.id===pageId) ?? firstPage;
  async function run(action:()=>Promise<{status:string}>) { if(isRunning)return; setIsRunning(true);setMessage("처리 중입니다…");try{const result=await action();if(result.status!=="succeeded")throw new Error("업무가 완료되지 않았습니다.");setMessage("업무를 완료했습니다.");await screen.refresh();}catch(reason){setMessage(reason instanceof Error?reason.message:"업무를 처리하지 못했습니다.");}finally{setIsRunning(false);} }
  function actionPanel(item:Item) { return <div className="action-stack">__ACTION_FORMS__</div>; }
  function objectRows() { return <div className="object-list">{filtered.length?filtered.map((item)=><button type="button" key={`${item.objectType}:${item.objectId}`} className={item.objectId===(selected?.objectId ?? "")?"object-row selected":"object-row"} onClick={()=>setSelectedId(item.objectId)}><strong>{String(item.properties.name ?? item.objectId)}</strong><small>{String(item.properties.status ?? "확인 필요")}</small></button>):<p className="empty">현재 조건에 맞는 업무가 없습니다.</p>}</div>; }
  function detail() { return selected?<details open><summary>업무 정보 자세히 보기</summary><dl>{recordFields.map((field)=><div key={field.apiName}><dt>{field.displayName}</dt><dd>{String(property(selected,field.apiName) ?? "입력되지 않음")}</dd></div>)}</dl></details>:<p className="empty">목록에서 업무를 선택하세요.</p>; }
  function statusTracker(property:string) { return <div className="tracker">{groupBy(filtered,property).map(([name,items])=><div className="track" key={name}><span>{name}</span><strong>{items.length}<small>건</small></strong></div>)}</div>; }
  function kanban(property:string) { return <div className="kanban">{groupBy(filtered,property).map(([name,items])=><section className="lane" key={name}><h3>{name}<small>{items.length}</small></h3>{items.slice(0,30).map((item)=><button type="button" key={item.objectId} onClick={()=>setSelectedId(item.objectId)}>{String(item.properties.name ?? item.objectId)}</button>)}</section>)}</div>; }
  function bars(property:string) { const groups=groupBy(filtered,property);const maximum=Math.max(1,...groups.map(([,items])=>items.length));return <div className="bars">{groups.map(([name,items])=><div className="bar" key={name}><span>{name}</span><i style={{width:`${items.length/maximum*100}%`}}/><b>{items.length}</b></div>)}</div>; }
  function pivot(rowProperty:string,columnProperty:string) { const rows=groupBy(filtered,rowProperty);const columns=columnProperty?[...new Set(filtered.map((item)=>String(property(item,columnProperty) ?? "미지정")))]:["전체"];return <table className="pivot"><thead><tr><th>{rowProperty}</th>{columns.map((column)=><th key={column}>{column}</th>)}<th>합계</th></tr></thead><tbody>{rows.map(([name,items])=><tr key={name}><td>{name}</td>{columns.map((column)=><td key={column}>{columnProperty?items.filter((item)=>String(property(item,columnProperty) ?? "미지정")===column).length:items.length}</td>)}<td><b>{items.length}</b></td></tr>)}</tbody></table>; }
  function renderWidget(widget:WorkshopWidget) { const objectApiName=value(widget.config,"objectApiName");if(objectApiName && objectApiName!==screen.items[0]?.objectType)return <p className="empty">이 업무 기록의 데이터 연결을 확인한 뒤 표시됩니다.</p>;switch(widget.kind){case"objectTable":case"objectList":return objectRows();case"objectDetail":return detail();case"objectSetTitle":case"metricCard":return <div className="metric">{filtered.length}<small>{value(widget.config,"unit","건")}</small></div>;case"searchBar":return <label className="search"><span>업무 검색</span><input value={query} onChange={(event)=>setQuery(event.target.value)} placeholder="이름, 상태, 담당자 검색"/></label>;case"filterList":case"objectDropdown":case"stringSelector":return <div className="filters"><button type="button" className={!statusFilter?"active":""} onClick={()=>setStatusFilter("")}>전체</button>{groupBy(screen.items,"status").map(([name,items])=><button type="button" key={name} className={statusFilter===name?"active":""} onClick={()=>setStatusFilter(name)}>{name} {items.length}</button>)}</div>;case"buttonGroup":case"actionForm":return selected?actionPanel(selected):<p className="empty">업무를 선택하세요.</p>;case"statusTracker":return statusTracker(value(widget.config,"groupByProperty","status"));case"kanban":return kanban(value(widget.config,"groupByProperty","status"));case"barChart":case"pieChart":return bars(value(widget.config,"groupByProperty","status"));case"pivotTable":return pivot(value(widget.config,"groupByProperty","status"),value(widget.config,"seriesProperty"));case"calendar":case"timeline":{const dateProperty=value(widget.config,"dateProperty");return <div className="timeline">{filtered.filter((item)=>property(item,dateProperty)).sort((a,b)=>String(property(a,dateProperty)).localeCompare(String(property(b,dateProperty)))).slice(0,30).map((item)=><button type="button" key={item.objectId} onClick={()=>setSelectedId(item.objectId)}><time>{String(property(item,dateProperty))}</time>{String(item.properties.name ?? item.objectId)}</button>)}</div>;}case"markdown":case"sectionHeader":return <p className="markdown">{value(widget.config,"text")}</p>;default:return <p className="empty">이 컴포넌트는 Workshop에서 연결 상태를 확인하세요.</p>;}}
  if(screen.isLoading)return <main className="loading"><h1>{workshop.name}</h1><p>업무 기록을 불러오는 중입니다.</p></main>;
  if(screen.error)return <main className="loading"><h1>{workshop.name}</h1><p>연결을 확인한 뒤 다시 시도하세요.</p></main>;
  return <main className="app-shell" style={themeVariables(workshop.theme.preset)}><aside className="side"><div className="brand"><span>{workshop.theme.logoText}</span><div><strong>{workshop.theme.brandName}</strong><small>Operational workspace</small></div></div><nav aria-label="업무 화면">{workshop.pages.map((page)=><button type="button" key={page.id} className={page.id===currentPage?.id?"active":""} onClick={()=>setPageId(page.id)}>{page.name}</button>)}</nav><footer>권한과 Action이 보호됨</footer></aside><div className="workspace"><header className="context"><div><small>Live work pulse</small><h1>{currentPage?.name}</h1><p>{workshop.purpose}</p></div><div className="pulses"><span>필터 {statusFilter?"1개":"전체"}</span><span>{selected?"업무 선택됨":"선택 전"}</span><span className="live">동기화 v{workshop.version}</span></div></header><div className="canvas">{currentPage?.sections.map((section)=><section className={`section ${section.layout}`} style={{gridColumn:`span ${section.span || 12}`}} key={section.id}><h2>{section.title}</h2><div className="widgets">{section.widgets.map((widget)=><article className="widget" data-workshop-widget={widget.kind} key={widget.id}><header><strong>{value(widget.config,"title",widget.kind)}</strong></header><div className="widget-body">{renderWidget(widget)}</div></article>)}</div></section>)}</div>{policies.length?<details className="policy"><summary>적용 중인 업무 규칙</summary>{policies.map((policy)=><article key={policy.name}><strong>{policy.name}</strong><p>{policy.statement}</p></article>)}</details>:null}{message?<p role="status">{message}</p>:null}</div></main>;
}
"""

_STYLES = r""":root{font-family:"Avenir Next","Pretendard",system-ui,sans-serif;color:#172033;background:#f4f6f8;line-height:1.5}*{box-sizing:border-box}body{margin:0}button,input{font:inherit}.app-shell{--accent:#0b7285;--accent-soft:#e6f6f8;--nav:#0d2b36;display:grid;min-height:100vh;grid-template-columns:248px minmax(0,1fr);background:#f4f6f8}.side{display:flex;min-height:100vh;flex-direction:column;padding:20px 14px;color:white;background:var(--nav)}.brand{display:flex;align-items:center;gap:12px;padding:0 5px 24px}.brand>span{display:grid;width:38px;height:38px;place-items:center;border-radius:12px;background:var(--accent);font-size:12px;font-weight:900}.brand strong,.brand small{display:block}.brand strong{font-size:13px}.brand small{margin-top:2px;color:#ffffff70;font-size:8px;letter-spacing:.13em;text-transform:uppercase}.side nav{display:grid;gap:4px}.side nav button{border:0;border-radius:9px;padding:10px 12px;text-align:left;color:#ffffffa8;background:transparent;font-size:12px;font-weight:650;cursor:pointer}.side nav button:hover{color:white;background:#ffffff0d}.side nav button.active{color:var(--nav);background:white;box-shadow:0 4px 18px #0000001f}.side footer{margin-top:auto;border-top:1px solid #ffffff18;padding:16px 5px 0;color:#ffffff78;font-size:10px}.workspace{min-width:0}.context{display:flex;align-items:center;gap:28px;border-bottom:1px solid #dde3e9;background:white;padding:20px 28px}.context>div:first-child{min-width:0;flex:1}.context small{color:var(--accent);font-size:9px;font-weight:850;letter-spacing:.14em;text-transform:uppercase}.context h1{margin:3px 0 0;font-size:26px;letter-spacing:-.035em}.context p{overflow:hidden;margin:4px 0 0;text-overflow:ellipsis;white-space:nowrap;color:#748195;font-size:11px}.pulses{display:flex;gap:8px}.pulses span{border:1px solid #dde3e9;border-radius:8px;padding:8px 10px;color:#657386;background:#f7f9fb;font-size:10px;font-weight:700}.pulses .live::before{content:"";display:inline-block;width:6px;height:6px;margin-right:5px;border-radius:50%;background:#22a06b}.canvas{display:grid;grid-template-columns:repeat(12,minmax(0,1fr));gap:16px;padding:24px 28px}.section{min-width:0}.section>h2{margin:0 0 9px;color:#657386;font-size:10px;letter-spacing:.1em;text-transform:uppercase}.widgets{display:grid;gap:12px}.section.columns .widgets{grid-template-columns:repeat(3,minmax(0,1fr))}.section.toolbar .widgets{display:flex;flex-wrap:wrap;align-items:stretch}.section.tabs .widget:not(:first-child){display:none}.widget{min-width:0;overflow:hidden;border:1px solid #dde3e9;border-radius:12px;background:white;box-shadow:0 10px 30px -28px #0f172a}.widget>header{border-bottom:1px solid #edf0f3;padding:10px 13px}.widget>header strong{font-size:11px}.widget-body{padding:13px}.metric{font-size:30px;font-weight:760;letter-spacing:-.05em}.metric small{margin-left:5px;color:#748195;font-size:10px;font-weight:500}.object-list,.action-stack{display:grid;gap:7px}.object-row,.lane button,.timeline button{width:100%;border:1px solid #edf0f3;border-radius:8px;padding:10px;text-align:left;color:#172033;background:white;cursor:pointer}.object-row.selected{border-color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft)}.object-row strong{font-size:11px}.object-row small{float:right;color:#748195;font-size:9px}.search{display:grid;gap:6px;color:#657386;font-size:10px}.search input,label input{min-width:0;border:1px solid #cbd3dc;border-radius:8px;padding:9px;color:#172033;background:white}.filters{display:flex;flex-wrap:wrap;gap:6px}.filters button{border:1px solid #dde3e9;border-radius:999px;padding:6px 9px;color:#657386;background:white;cursor:pointer;font-size:9px}.filters button.active{border-color:var(--accent);color:var(--accent);background:var(--accent-soft)}.tracker,.kanban{display:flex;gap:9px;overflow:auto}.track,.lane{min-width:130px;flex:1;border-radius:9px;padding:10px;background:#f7f9fb}.track span,.lane h3{margin:0;color:#657386;font-size:10px}.track strong{display:block;margin-top:7px;font-size:23px}.track strong small,.lane h3 small{margin-left:4px;color:#8994a5;font-size:9px}.lane button{margin-top:7px;padding:8px;font-size:10px}.bars{display:grid;gap:8px}.bar{display:grid;grid-template-columns:90px minmax(0,1fr) 35px;align-items:center;gap:8px;font-size:10px}.bar i{display:block;height:8px;border-radius:99px;background:var(--accent)}.pivot{width:100%;border-collapse:collapse;font-size:10px}.pivot th,.pivot td{border-bottom:1px solid #edf0f3;padding:7px;text-align:right}.pivot th:first-child,.pivot td:first-child{text-align:left}.timeline{display:grid;gap:6px}.timeline button{display:grid;grid-template-columns:130px minmax(0,1fr);gap:10px;font-size:10px}.timeline time{color:var(--accent);font-weight:650}dl{display:grid;gap:8px}dl div{display:grid;grid-template-columns:minmax(110px,1fr) 2fr;gap:12px;font-size:11px}dt{color:#657386}dd{margin:0;overflow-wrap:anywhere}details summary{cursor:pointer;font-size:10px;font-weight:700}.action-stack form{display:grid;gap:9px;border:1px solid #edf0f3;border-radius:9px;padding:11px}.action-stack h3{margin:0;font-size:12px}.permission,.empty,.markdown{margin:0;color:#748195;font-size:10px}.action-stack label{display:grid;gap:5px;color:#657386;font-size:10px}.action-stack button{justify-self:start;border:0;border-radius:8px;padding:9px 13px;color:white;background:var(--accent);font-weight:750;cursor:pointer}.policy{margin:0 28px 24px;border:1px solid #dde3e9;border-radius:10px;background:white;padding:13px}.policy article{margin-top:10px}.policy article p{margin:3px 0;color:#657386;font-size:10px}[role=status]{position:sticky;bottom:16px;margin:0 28px 20px;border-radius:9px;background:#111827;color:white;padding:11px 14px;font-size:11px}.loading{max-width:720px;margin:80px auto;padding:24px}.loading h1{font-size:32px}@media(max-width:900px){.app-shell{grid-template-columns:1fr}.side{min-height:auto;padding:10px}.brand{padding-bottom:8px}.side nav{display:flex;overflow:auto}.side nav button{white-space:nowrap}.side footer{display:none}.context{padding:16px}.pulses{display:none}.canvas{grid-template-columns:1fr;padding:16px}.section{grid-column:1!important}.section.columns .widgets{grid-template-columns:1fr}.policy,[role=status]{margin-right:16px;margin-left:16px}}button:focus-visible,input:focus-visible,summary:focus-visible{outline:3px solid var(--accent-soft);outline-offset:2px}@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;transition:none!important}}"""


__all__ = ["portable_workshop_application_source", "portable_workshop_styles"]
