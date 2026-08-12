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
    generatedFiles: { count: 2, names: ["src/App.tsx", "packages/application-osdk/src/generated.ts"] },
  };
}

function harness({ callTool, standard = false, output = plan(), sendFollowUpMessage } = {}) {
  const listeners = new Map();
  const elements = new Map();
  const calls = [];
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
      toolInput: { mode: "osdk_react", workspaceRef: "osdk-app:builder-app", arguments: {} },
      toolOutput: { structuredContent: output },
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
    clearInterval,
    clearTimeout,
    setInterval,
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
  return { calls, context, elements, root };
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
