import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];

function nodeStub() {
  return {
    dataset: {},
    textContent: "",
    disabled: false,
    addEventListener() {},
  };
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function createHarness(
  callTool,
  {
    token = "widget-secret",
    recoveryReceipt = "",
    structuredToken,
    toolId = "ontology.branch.apply_patch",
    argumentOverrides = {},
  } = {},
) {
  const nodes = new Map([
    "tool", "mode", "workspace", "category", "impact", "change-summary", "request-hash",
    "preview", "expiry", "approve", "message",
  ].map((id) => [id, nodeStub()]));
  const listeners = new Map();
  const toolInput = {
    mode: "ontology_editing",
    workspaceRef: "ontology-branch:branch-7",
    arguments: {
      upsertResources: [{ kind: "objectType", apiName: "Order" }],
      changeSummary: "Add Order",
      ...argumentOverrides,
    },
  };
  const toolOutput = {
    status: "approval_required",
    challengeId: "challenge-7",
    toolId,
    mode: "ontology_editing",
    workspaceRef: "ontology-branch:branch-7",
    requestBindingHash: "sha256:binding-7",
    expiresAt: "2030-01-01T00:00:00+00:00",
    ...(structuredToken ? { widgetApprovalToken: structuredToken } : {}),
  };
  const context = {
    Date,
    Error,
    JSON,
    Object,
    Promise,
    String,
    document: { getElementById: (id) => nodes.get(id) },
    openai: {
      toolInput,
      toolOutput,
      toolResponseMetadata: {
        ...(token ? { widgetApprovalToken: token } : {}),
        ...(recoveryReceipt ? { confirmationReceipt: recoveryReceipt } : {}),
      },
      callTool,
    },
    addEventListener(name, handler) {
      listeners.set(name, handler);
    },
  };
  context.globalThis = context;
  vm.runInNewContext(script, context, { filename: "chatgpt-builder-widget/index.html" });
  return { api: context.__foundryBuilderWidgetTest, context, listeners, nodes, toolInput, toolOutput };
}

test("app-only 승인 뒤 처음 Builder 입력을 confirmationReceipt만 더해 정확히 재호출한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "approve_builder_mutation") {
      return {
        _meta: { confirmationReceipt: "receipt-secret" },
        structuredContent: { confirmationReceipt: "must-not-use" },
      };
    }
    return { structuredContent: { changeSummary: "Add Order" }, isError: false };
  });

  await harness.api.approveAndRetry();

  assert.deepEqual(calls.map((call) => call.name), [
    "approve_builder_mutation",
    "ontology.branch.apply_patch",
  ]);
  assert.deepEqual(calls[0].args, {
    challengeId: "challenge-7",
    widgetApprovalToken: "widget-secret",
  });
  assert.deepEqual(
    Object.fromEntries(Object.entries(calls[1].args).filter(([key]) => key !== "confirmationReceipt")),
    harness.toolInput,
  );
  assert.equal(calls[1].args.confirmationReceipt, "receipt-secret");
  assert.deepEqual(clone(harness.api.getState()), {
    busy: false,
    completed: true,
    message: "처음 표시된 Builder 변경을 완료했습니다.",
    tone: "success",
    isReady: false,
  });
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /widget-secret|receipt-secret/);
});

test("structuredContent에 섞인 토큰은 사용하지 않고 _meta가 없으면 승인 호출을 막는다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args });
    return {};
  }, { token: "", structuredToken: "untrusted-structured-token" });

  await harness.api.approveAndRetry();

  assert.deepEqual(calls, []);
  assert.equal(harness.api.getState().isReady, false);
});

test("승인 후 재호출이 실패해도 로컬 secret 상태를 지운다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "approve_builder_mutation") return { _meta: { confirmationReceipt: "receipt-failure" } };
    return {
      isError: true,
      structuredContent: { error: { message: "branch head changed" } },
    };
  });

  await harness.api.approveAndRetry();

  assert.equal(calls.length, 2);
  assert.equal(harness.api.getState().completed, false);
  assert.equal(harness.api.getState().isReady, false);
  assert.match(harness.api.getState().message, /branch head changed/);
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /widget-secret|receipt-failure/);
});

test("승인 응답을 잃어도 _meta 영수증으로 private 승인 재호출 없이 정확한 작업을 복구한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    return { structuredContent: { status: "completed" }, isError: false };
  }, { token: "", recoveryReceipt: "recovered-receipt" });

  await harness.api.approveAndRetry();

  assert.deepEqual(calls.map((call) => call.name), ["ontology.branch.apply_patch"]);
  assert.equal(calls[0].args.confirmationReceipt, "recovered-receipt");
  assert.deepEqual(
    Object.fromEntries(Object.entries(calls[0].args).filter(([key]) => key !== "confirmationReceipt")),
    harness.toolInput,
  );
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /recovered-receipt/);
});

test("서버 승인 직후 bridge 응답이 유실되면 같은 token으로 한 번 재호출해 영수증을 복구한다", async () => {
  const calls = [];
  let approvalAttempts = 0;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "approve_builder_mutation") {
      approvalAttempts += 1;
      if (approvalAttempts === 1) throw new Error("bridge response lost after commit");
      return { _meta: { confirmationReceipt: "receipt-after-lost-response" } };
    }
    return { structuredContent: { status: "completed" }, isError: false };
  });

  await harness.api.approveAndRetry();

  assert.deepEqual(calls.map((call) => call.name), [
    "approve_builder_mutation",
    "approve_builder_mutation",
    "ontology.branch.apply_patch",
  ]);
  assert.deepEqual(calls[0].args, calls[1].args);
  assert.equal(calls[2].args.confirmationReceipt, "receipt-after-lost-response");
  assert.equal(harness.api.getState().completed, true);
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /widget-secret|receipt-after-lost-response/);
});

test("원본 mutation commit 뒤 bridge 응답이 유실되면 새 승인 없이 exact input으로 한 번 재호출한다", async () => {
  const calls = [];
  let mutationAttempts = 0;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "approve_builder_mutation") {
      return { _meta: { confirmationReceipt: "receipt-for-mutation-replay" } };
    }
    mutationAttempts += 1;
    if (mutationAttempts === 1) throw new Error("bridge lost committed mutation response");
    return { structuredContent: { status: "completed" }, isError: false, isReplayed: true };
  });

  await harness.api.approveAndRetry();

  assert.deepEqual(calls.map((call) => call.name), [
    "approve_builder_mutation",
    "ontology.branch.apply_patch",
    "ontology.branch.apply_patch",
  ]);
  assert.deepEqual(calls[1].args, calls[2].args);
  assert.equal(calls[1].args.confirmationReceipt, "receipt-for-mutation-replay");
  assert.equal(harness.api.getState().completed, true);
});

test("즉시 저장 작업의 영향과 요청 해시를 표시하고 secret-like 입력은 가린다", () => {
  const harness = createHarness(async () => ({}), {
    toolId: "create_foundry_project",
    argumentOverrides: {
      displayName: "GPT Complete",
      apiKey: "never-render-this-key",
      private_key: "never-render-this-private-key",
      databaseUrl: "postgresql://user:password@db.example.test/app",
      cookie: "never-render-this-cookie",
      headers: { "X-Custom": "never-render-this-header" },
      endpoint: "https://api.example.test/orders?access_token=never-render-this-url-token",
      nested: { password: "never-render-this-password", harmless: "visible-value" },
    },
  });

  assert.equal(harness.nodes.get("category").textContent, "프로젝트 생성");
  assert.match(harness.nodes.get("impact").textContent, /즉시 저장/);
  assert.equal(harness.nodes.get("request-hash").textContent, "sha256:binding-7");
  assert.match(harness.nodes.get("preview").textContent, /GPT Complete|visible-value/);
  assert.doesNotMatch(harness.nodes.get("preview").textContent, /never-render-this/);
  assert.match(harness.nodes.get("preview").textContent, /\[redacted\]/);
});

test("큰 변경 미리보기는 21번째 배열 항목과 51번째 객체 키도 숨기지 않는다", () => {
  const manyItems = Array.from({ length: 21 }, (_, index) => ({ apiName: `Object${index + 1}` }));
  const manyKeys = Object.fromEntries(
    Array.from({ length: 51 }, (_, index) => [`property${index + 1}`, `value${index + 1}`]),
  );
  const harness = createHarness(async () => ({}), {
    argumentOverrides: { manyItems, manyKeys },
  });

  assert.match(harness.nodes.get("preview").textContent, /Object21/);
  assert.match(harness.nodes.get("preview").textContent, /property51/);
  assert.match(harness.nodes.get("preview").textContent, /value51/);
  assert.doesNotMatch(harness.nodes.get("preview").textContent, /more items hidden|more keys hidden/);
});

test("위젯은 외부 네트워크나 브라우저 저장소 없이 단일 HTML로 동작한다", () => {
  assert.ok(script);
  assert.doesNotMatch(html, /https?:\/\//i);
  assert.doesNotMatch(html, /localStorage|sessionStorage/);
  assert.match(html, /EXACT CHANGE REVIEW/);
  assert.match(html, /prefers-reduced-motion/);
  assert.match(html, /focus-visible/);
});

test("호스트 전역이 늦게 준비돼도 승인 카드가 challenge를 놓치지 않는다", () => {
  // Regression: the host publishes the tool call on `globalThis.openai`, but its later
  // `openai:set_globals` events carry only UI state (displayMode, view). Reading the snapshot
  // exactly once left the card stuck on "미제공" whenever the widget rendered first.
  const nodes = new Map(
    ["tool", "mode", "workspace", "category", "impact", "change-summary", "request-hash", "preview", "expiry", "approve", "message"].map(
      (id) => [id, nodeStub()],
    ),
  );
  const timers = [];
  const context = {
    Date,
    Error,
    JSON,
    Object,
    Promise,
    String,
    Boolean,
    document: { getElementById: (id) => nodes.get(id) },
    addEventListener() {},
    setInterval: (fn) => {
      timers.push(fn);
      return timers.length;
    },
    clearInterval: () => {},
  };
  context.globalThis = context;
  vm.runInNewContext(script, context, { filename: "chatgpt-builder-widget/index.html" });

  assert.equal(nodes.get("tool").textContent, "미제공", "nothing to show before the host publishes");
  assert.ok(timers.length > 0, "host globals were absent, so the widget must keep polling");

  context.openai = {
    toolInput: { mode: "ontology_editing", workspaceRef: "ontology-branch:branch-9", arguments: {} },
    toolOutput: { status: "approval_required", challengeId: "challenge-9", toolId: "ontology.branch.rebase" },
    toolResponseMetadata: { widgetApprovalToken: "late-secret" },
    callTool: async () => ({}),
  };
  timers[0]();

  assert.equal(nodes.get("tool").textContent, "ontology.branch.rebase");
  assert.equal(nodes.get("workspace").textContent, "ontology-branch:branch-9");
});
