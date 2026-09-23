import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const template = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const runtime = readFileSync(new URL("./foundry-lite-mcp-osdk.js", import.meta.url), "utf8");

function plan({ isReady = true } = {}) {
  return {
    operationType: "pilot_generation_plan",
    applicationName: "Property Care Desk",
    domainDescription: "입주민의 시설 문제를 접수하고 수리 완료 증거까지 남깁니다.",
    slug: "property-care-desk",
    domainOsBlueprint: {
      schemaVersion: "foundry-lite-domain-os-blueprint/v1",
      summary: "입주민의 시설 문제를 접수하고 수리 완료 증거까지 남깁니다.",
      actors: ["입주민", "시설 담당자", "수리 업체"],
      records: [
        {
          apiName: "WorkOrder",
          displayName: "수리 요청",
          primaryKey: "workOrderId",
          fields: [
            { apiName: "workOrderId", displayName: "수리 요청 ID" },
            { apiName: "name", displayName: "이름" },
            { apiName: "status", displayName: "현재 상태" },
            { apiName: "location", displayName: "발생 위치" },
          ],
        },
      ],
      workflow: {
        states: ["접수됨", "분류됨", "방문예정", "완료됨"],
        actions: [
          {
            apiName: "TriageWorkOrder",
            displayName: "요청 분류",
            fromStates: ["접수됨"],
            toState: "분류됨",
            requiredInformation: ["우선순위"],
            allowedActors: ["시설 담당자"],
            requiresApproval: false,
          },
        ],
      },
      policies: [
        {
          name: "긴급 누수 우선 처리",
          statement: "긴급 누수는 일반 요청보다 먼저 배정합니다.",
          enforcement: "manual_review",
          automationStatus: "human_confirmation",
        },
      ],
      evidence: ["상태 변경 전후", "담당자", "완료 사진"],
      readiness: {
        isReady,
        missingCount: isReady ? 0 : 1,
        questions: isReady ? [] : [{ field: "evidence", question: "완료를 확인하려면 어떤 증거가 필요한가요?" }],
      },
    },
    consumerOsdk: { profile: "consumer_osdk_strict" },
    mcpExecution: { mode: "osdk_react", workspaceRef: "osdk-app:builder-app" },
  };
}

function bundle() {
  return {
    operationType: "pilot_application_bundle",
    applicationName: "Property Care Desk",
    applicationPath: "/projects/project-1/pilot/property-care-desk",
    status: "generated_on_branch",
    domainOsBlueprint: plan().domainOsBlueprint,
    workshopPreview: [{ name: "오늘 할 일", components: [{ kind: "statusTracker", name: "현재 상태" }] }],
    generatedFiles: { count: 2, names: ["src/App.tsx", "packages/application-osdk/src/generated.ts"] },
  };
}

function harness({
  callTool = async () => ({}),
  standard = false,
  output = plan(),
  includeToolOutput = true,
  toolInput = { mode: "osdk_react", workspaceRef: "osdk-app:builder-app", arguments: {} },
  sendFollowUpMessage,
  fakeIntervals = false,
} = {}) {
  const listeners = new Map();
  const elements = new Map();
  const calls = [];
  const timers = [];
  const root = element("app");
  elements.set("app", root);
  const context = {
    console,
    crypto: { randomUUID: () => "12345678-1234-1234-1234-123456789012" },
    document: {
      getElementById(id) {
        if (!elements.has(id)) elements.set(id, element(id));
        return elements.get(id);
      },
    },
    openai: {
      toolInput,
      ...(includeToolOutput ? { toolOutput: { structuredContent: output } } : {}),
      ...(standard ? {} : {
        async callTool(name, args) {
          calls.push({ name, args: structuredClone(args) });
          return callTool(name, args);
        },
      }),
      ...(sendFollowUpMessage ? { sendFollowUpMessage } : {}),
    },
    addEventListener(name, listener) {
      const values = listeners.get(name) || [];
      values.push(listener);
      listeners.set(name, values);
    },
    clearInterval: fakeIntervals
      ? (id) => { if (timers[id]) timers[id].active = false; }
      : clearInterval,
    clearTimeout,
    setInterval: fakeIntervals
      ? (fn) => {
        timers.push({ active: true, fn });
        return timers.length - 1;
      }
      : setInterval,
    setTimeout,
  };
  context.globalThis = context;
  context.parent = context;
  if (standard) {
    context.postMessage = function postMessage(message) {
      const source = this;
      queueMicrotask(async () => {
        let result = {};
        if (message.method === "ui/initialize") result = { protocolVersion: "2026-01-26" };
        if (message.method === "tools/call") {
          calls.push({ name: message.params.name, args: structuredClone(message.params.arguments) });
          result = await callTool(message.params.name, message.params.arguments);
        }
        dispatch(context, listeners, "message", { source, data: { jsonrpc: "2.0", id: message.id, result } });
      });
    };
  }
  const html = template.replace("/*__FOUNDRY_LITE_MCP_OSDK__*/", runtime);
  const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map((match) => match[1]);
  for (const source of scripts) vm.runInNewContext(source, context, { filename: "domain-os-widget.html" });
  return { calls, context, elements, listeners, root, timers };
}

function element(id) {
  return {
    id,
    innerHTML: "",
    listeners: {},
    addEventListener(name, listener) { this.listeners[name] = listener; },
  };
}

function dispatch(context, listeners, name, event) {
  for (const listener of listeners.get(name) || []) listener.call(context, event);
}

test("업무 설계 카드는 자연어에서 찾은 기록·상태·규칙·Action과 strict OSDK를 쉬운 말로 보여준다", () => {
  const view = harness({ callTool: async () => ({}) });

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.match(view.root.innerHTML, /수리 요청/);
  assert.match(view.root.innerHTML, /요청 분류/);
  assert.match(view.root.innerHTML, /누를 수 있는 사람: 시설 담당자/);
  assert.match(view.root.innerHTML, /긴급 누수 우선 처리/);
  assert.match(view.root.innerHTML, /실행 전 사람이 확인/);
  assert.match(view.root.innerHTML, /이 설계로 테스트 앱 만들기/);
  assert.match(view.root.innerHTML, /이 앱에 허용된 기능만 사용/);
  assert.doesNotMatch(template, /openai\.callTool|tools\/call|pilot\.application\.generate/);
  assert.match(runtime, /createFoundryLiteMcpAppsOsdk|DomainOsStudio/);
});

test("기록이 빠진 초안은 빈 화면이나 연결 완료 표시 없이 보완 질문을 보여준다", () => {
  const incomplete = plan({ isReady: false });
  incomplete.domainOsBlueprint.records = [];
  incomplete.mcpExecution.workspaceRef = "tenant:self";
  const view = harness({
    output: incomplete,
    toolInput: { mode: "osdk_react", workspaceRef: "tenant:self", arguments: {} },
  });

  assert.match(view.root.innerHTML, /설계의 빈칸을 확인하면 사용할 화면을 함께 제안합니다/);
  assert.match(view.root.innerHTML, /대화에서 질문에 답하기/);
  assert.match(view.root.innerHTML, /읽기 전용 설계 검토/);
  assert.doesNotMatch(view.root.innerHTML, /이 설계로 테스트 앱 만들기|테스트 공간 연결됨/);
});

test("압축된 Workshop 화면 요약도 같은 컴포넌트 이름으로 보여준다", () => {
  const compact = plan();
  compact.workshopPreview = [{ name: "접수 담당자 화면", components: ["업무 카드 목록", "검색", "업무 폼"] }];
  const view = harness({ output: compact });

  assert.match(view.root.innerHTML, /접수 담당자 화면/);
  assert.match(view.root.innerHTML, /업무 카드 목록 · 검색 · 업무 폼/);
  assert.doesNotMatch(view.root.innerHTML, /\[object Object\]/);
  assert.doesNotMatch(view.root.innerHTML, /설계의 빈칸을 확인하면/);
});

test("Workshop의 실제 화면 제목과 상태 번역을 보여주고 내부 코드를 숨긴다", () => {
  const compact = plan();
  compact.domainOsBlueprint.workflow.states = ["INQUIRY", "CONSENT_CONFIRMED"];
  compact.domainOsBlueprint.workflow.actions[0].fromStates = ["INQUIRY"];
  compact.domainOsBlueprint.workflow.actions[0].toState = "CONSENT_CONFIRMED";
  compact.workshopPresentation = {
    statusLabels: {
      INQUIRY: { label: "문의 접수" },
      CONSENT_CONFIRMED: { label: "동의 확인" },
    },
  };
  compact.workshopPreview = [{
    name: "접수 담당자 화면",
    components: [
      { kind: "statusTracker", name: "현재 단계" },
      { kind: "filterList", name: "빠른 필터" },
      { kind: "futureWidget", name: "맞춤 분석" },
    ],
  }];
  const view = harness({ output: compact });

  assert.match(view.root.innerHTML, /문의 접수.*동의 확인/);
  assert.match(view.root.innerHTML, /현재 단계 · 빠른 필터 · 맞춤 분석/);
  assert.doesNotMatch(view.root.innerHTML, /INQUIRY|CONSENT_CONFIRMED|statusTracker|filterList|futureWidget/);
  assert.ok(view.root.innerHTML.indexOf("테스트 앱을 만들 준비") < view.root.innerHTML.indexOf("업무 운영 지도"));
});

test("생성 버튼은 고수준 MCP OSDK에서 challenge→app-only 확인→exact retry를 수행한다", async () => {
  const view = harness({
    callTool: async (name) => {
      if (name === "pilot.application.generate" && view.calls.filter((item) => item.name === name).length === 1) {
        return {
          structuredContent: { status: "approval_required", challengeId: "challenge-1", toolId: name },
          _meta: { widgetApprovalToken: "widget-token" },
        };
      }
      if (name === "approve_builder_mutation") return { _meta: { confirmationReceipt: "receipt-1" } };
      return { structuredContent: bundle() };
    },
  });

  await view.context.__foundryDomainOsWidgetTest.generate();

  assert.deepEqual(view.calls.map((item) => item.name), [
    "pilot.application.generate",
    "approve_builder_mutation",
    "pilot.application.generate",
  ]);
  assert.equal(view.calls[0].args.mode, "osdk_react");
  assert.equal(view.calls[0].args.workspaceRef, "osdk-app:builder-app");
  assert.equal(view.calls[0].args.arguments.plan.applicationName, "Property Care Desk");
  assert.equal(view.calls[0].args.confirmationReceipt, undefined);
  assert.equal(view.calls[2].args.confirmationReceipt, "receipt-1");
  assert.match(view.root.innerHTML, /Property Care Desk 준비 완료/);
  assert.match(view.root.innerHTML, /화면은 이 앱에 허용된 기능만 사용/);
  assert.match(view.root.innerHTML, /오늘 할 일/);
  assert.match(view.root.innerHTML, /현재 상태/);
  assert.doesNotMatch(view.root.innerHTML, /설계의 빈칸을 확인하면/);
});

test("MCP Apps 표준 postMessage bridge가 window.openai callTool 없이 생성 흐름을 완료한다", async () => {
  const view = harness({
    standard: true,
    callTool: async (name) => name === "pilot.application.generate"
      ? { structuredContent: bundle() }
      : {},
  });
  await new Promise((resolve) => setTimeout(resolve, 0));

  await view.context.__foundryDomainOsWidgetTest.generate();

  assert.deepEqual(view.calls.map((item) => item.name), ["pilot.application.generate"]);
  assert.match(view.root.innerHTML, /준비 완료/);
});

test("빈 설계는 개발 용어 대신 대화에서 답할 한 가지 업무 질문을 보여준다", async () => {
  let prompt = "";
  const view = harness({
    output: plan({ isReady: false }),
    callTool: async () => ({}),
    sendFollowUpMessage: async (value) => { prompt = value.prompt; },
  });

  assert.match(view.root.innerHTML, /1가지만 더 알려주세요/);
  assert.match(view.root.innerHTML, /완료를 확인하려면 어떤 증거가 필요한가요/);
  await view.elements.get("ask").listeners.click();
  assert.match(prompt, /한 번에 하나씩 쉬운 말/);
});

test("과거 대화의 도구 결과가 늦게 준비돼도 저장된 업무 설계를 복원한다", () => {
  const view = harness({ includeToolOutput: false, fakeIntervals: true });

  assert.match(view.root.innerHTML, /업무 설계를 불러오고 있습니다/);
  assert.equal(view.timers.length, 1);

  view.context.openai.toolOutput = { structuredContent: plan() };
  view.timers[0].fn();

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.doesNotMatch(view.root.innerHTML, /업무 설계를 불러오고 있습니다/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "ready");
});

test("호스트의 빈 중간 응답 뒤에 도착한 설계 결과를 실패 화면 없이 복원한다", () => {
  const view = harness({ output: {}, fakeIntervals: true });

  assert.match(view.root.innerHTML, /업무 설계를 불러오고 있습니다/);
  assert.doesNotMatch(view.root.innerHTML, /불러오기 실패/);
  view.context.openai.toolOutput = { structuredContent: plan() };
  view.timers[0].fn();

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "ready");
});

test("호스트가 JSON 텍스트만 전달해도 설계 결과를 복원한다", () => {
  const view = harness({ includeToolOutput: false, fakeIntervals: true });

  view.context.openai.toolOutput = { content: [{ type: "text", text: JSON.stringify(plan()) }] };
  view.timers[0].fn();

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "ready");
});

test("도구 찾기 결과는 설계 실패가 아니라 준비 완료 단계로 표시한다", () => {
  const view = harness({
    output: {
      queryHash: "sha256:search",
      activatedTools: [{ toolId: "pilot.application.plan", isNew: true }],
      toolsListChanged: true,
    },
    fakeIntervals: true,
  });

  assert.match(view.root.innerHTML, /연결 준비 완료/);
  assert.match(view.root.innerHTML, /실제 업무 설계는 이어지는 카드에서 확인/);
  assert.doesNotMatch(view.root.innerHTML, /불러오기 실패/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "discovery_ready");
});

test("알 수 없는 호스트 결과는 설계 성공이나 형식 오류로 오인하지 않고 안내한다", () => {
  const view = harness({ output: { unexpected: true }, fakeIntervals: true });

  assert.match(view.root.innerHTML, /업무 설계를 불러오고 있습니다/);
  for (let attempt = 0; attempt < 40; attempt += 1) view.timers[0].fn();

  assert.match(view.root.innerHTML, /이 카드에는 업무 설계가 없습니다/);
  assert.match(view.root.innerHTML, /아직 업무 설계 결과로 확인되지 않았습니다/);
  assert.doesNotMatch(view.root.innerHTML, /불러오기 실패|테스트 앱을 만들 준비/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "unavailable");
});

test("호스트가 결과를 끝내 전달하지 않으면 입력만 있는 봉투를 형식 오류로 오인하지 않는다", () => {
  const view = harness({ includeToolOutput: false, fakeIntervals: true });

  for (let attempt = 0; attempt < 40; attempt += 1) view.timers[0].fn();

  assert.match(view.root.innerHTML, /이 카드에는 업무 설계가 없습니다/);
  assert.match(view.root.innerHTML, /설계 결과를 전달받지 못했습니다/);
  assert.match(view.root.innerHTML, /다시 확인/);
  assert.match(view.root.innerHTML, /ChatGPT에서 설계 다시 열기/);
  assert.doesNotMatch(view.root.innerHTML, /업무 설계를 불러오고 있습니다|결과의 형식|불러오기 실패/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().hasUnrecognizedResult, false);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "unavailable");
});

test("실패 화면의 다시 확인은 뒤늦게 복원된 결과를 읽어 화면을 되살린다", async () => {
  const view = harness({ includeToolOutput: false, fakeIntervals: true });
  view.context.__foundryDomainOsWidgetTest.markLoadFailure("결과가 아직 없습니다.");
  view.context.openai.toolOutput = { structuredContent: plan() };

  await view.elements.get("retry").listeners.click();

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "ready");
});

test("앱 생성 승인 결과는 입력에 담긴 설계를 복원하고 승인 대기 상태를 설명한다", () => {
  const generationPlan = plan();
  const view = harness({
    output: { status: "approval_required", challengeId: "challenge-1", toolId: "pilot.application.generate" },
    toolInput: {
      mode: "osdk_react",
      workspaceRef: "osdk-app:builder-app",
      arguments: { plan: generationPlan, idempotencyKey: "stable-generation" },
    },
  });

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.match(view.root.innerHTML, /테스트 앱 생성은 승인 대기 중입니다/);
  assert.doesNotMatch(view.root.innerHTML, /업무 설계를 불러오고 있습니다/);
});

test("openai set_globals로 늦게 온 도구 결과도 업무 설계로 반영한다", () => {
  const view = harness({ includeToolOutput: false, fakeIntervals: true });

  dispatch(view.context, view.listeners, "openai:set_globals", {
    detail: { globals: { toolOutput: { structuredContent: plan() } } },
  });

  assert.match(view.root.innerHTML, /Property Care Desk/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "ready");
});

test("프로젝트 없는 설계는 바로 생성을 시도하지 않고 대화에서 사람 승인으로 이어간다", async () => {
  let prompt = "";
  const view = harness({
    output: { ...plan(), mcpExecution: { mode: "osdk_react", workspaceRef: "tenant:self" } },
    sendFollowUpMessage: async (value) => { prompt = value.prompt; },
  });

  await view.context.__foundryDomainOsWidgetTest.generate();

  assert.equal(view.calls.length, 0);
  assert.match(prompt, /프로젝트/);
  assert.match(prompt, /사람 승인/);
});

test("호스트의 작업공간 오류를 형식 오류가 아닌 쉬운 복구 안내로 보여준다", () => {
  const view = harness({ output: { error: { message: "AI FDE workspaceRef is not valid" } } });

  assert.match(view.root.innerHTML, /업무 설계 공간을 찾지 못했습니다/);
  assert.match(view.root.innerHTML, /불러오기 실패/);
  assert.doesNotMatch(view.root.innerHTML, /결과의 형식/);
  assert.equal(view.context.__foundryDomainOsWidgetTest.getState().loadStatus, "failed");
});
