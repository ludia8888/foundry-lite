(() => {
  "use strict";

  const PROTOCOL_VERSION = "2026-01-26";
  const DomainOsStudio = Object.freeze({ kind: "mcpApplication", apiName: "DomainOsStudio" });
  const subscribers = new Set();
  const pending = new Map();
  let nextRequestId = 1;
  let standardTarget = null;
  let bridgeMode = "pending";
  let bridgeReady = null;
  let isTornDown = false;

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

  function targets() {
    const values = [];
    if (typeof globalThis.postMessage === "function") values.push(globalThis);
    if (globalThis.parent && globalThis.parent !== globalThis && typeof globalThis.parent.postMessage === "function") {
      values.push(globalThis.parent);
    }
    return values;
  }

  function responseError(message) {
    const error = new Error(String(message?.error?.message || "GPT 호스트 요청이 거부되었습니다."));
    error.code = message?.error?.code;
    error.data = isObject(message?.error?.data) ? message.error.data : {};
    return error;
  }

  function handleMessage(event) {
    if (!targets().includes(event.source) || !isObject(event.data) || event.data.jsonrpc !== "2.0") return;
    const message = event.data;
    if (message.method === "ui/resource-teardown" && message.id !== undefined) {
      tearDown();
      event.source.postMessage({ jsonrpc: "2.0", id: message.id, result: {} }, "*");
      return;
    }
    const isResponse = Object.prototype.hasOwnProperty.call(message, "result")
      || Object.prototype.hasOwnProperty.call(message, "error");
    if (message.id !== undefined && isResponse && pending.has(message.id)) {
      if (standardTarget && event.source !== standardTarget) return;
      standardTarget = event.source;
      const request = pending.get(message.id);
      pending.delete(message.id);
      if (request.timeoutId !== undefined) globalThis.clearTimeout(request.timeoutId);
      if (message.error) request.reject(responseError(message));
      else request.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-result") {
      if (standardTarget && event.source !== standardTarget) return;
      standardTarget = event.source;
      publish(isObject(message.params?.result) ? message.params.result : message.params);
    }
  }

  function request(method, params, timeoutMs = 15000) {
    if (isTornDown) return Promise.reject(new Error("GPT가 이 업무 화면을 종료했습니다."));
    const destinations = standardTarget ? [standardTarget] : targets();
    if (!destinations.length) return Promise.reject(new Error("MCP Apps 호스트를 찾지 못했습니다."));
    const id = `foundry-domain-os-${nextRequestId++}`;
    return new Promise((resolve, reject) => {
      const item = { resolve, reject, timeoutId: undefined };
      pending.set(id, item);
      for (const target of destinations) target.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
      if (!pending.has(id)) return;
      item.timeoutId = globalThis.setTimeout(() => {
        if (!pending.has(id)) return;
        pending.delete(id);
        reject(new Error(`${method} 응답 시간이 초과되었습니다.`));
      }, timeoutMs);
    });
  }

  function notify(method, params) {
    if (!standardTarget) throw new Error("MCP Apps 호스트를 찾지 못했습니다.");
    standardTarget.postMessage({ jsonrpc: "2.0", method, ...(params === undefined ? {} : { params }) }, "*");
  }

  function aliasAvailable() {
    return Boolean(globalThis.openai && typeof globalThis.openai.callTool === "function");
  }

  async function initialize() {
    if (isTornDown) return "unavailable";
    if (!targets().length) {
      bridgeMode = aliasAvailable() ? "alias" : "unavailable";
      return bridgeMode;
    }
    try {
      const initialized = await request("ui/initialize", {
        protocolVersion: PROTOCOL_VERSION,
        appInfo: { name: "foundry-lite-domain-os-studio", version: "1.0.0" },
        appCapabilities: { availableDisplayModes: ["inline"] },
      }, 2500);
      if (!isObject(initialized) || initialized.protocolVersion !== PROTOCOL_VERSION) {
        throw new Error("MCP Apps 프로토콜 버전을 확인하지 못했습니다.");
      }
      bridgeMode = "standard";
      notify("ui/notifications/initialized");
    } catch (_error) {
      bridgeMode = !isTornDown && aliasAvailable() ? "alias" : "unavailable";
    }
    return bridgeMode;
  }

  function ready() {
    if (!bridgeReady) bridgeReady = initialize();
    return bridgeReady;
  }

  function resultError(result) {
    if (!isObject(result) || result.isError !== true) return null;
    const structured = isObject(result.structuredContent) ? result.structuredContent : {};
    const nested = isObject(structured.error) ? structured.error : {};
    const error = new Error(String(nested.message || "업무 앱 요청이 거부되었습니다."));
    error.isToolResultError = true;
    error.code = nested.type;
    error.details = isObject(nested.details) ? nested.details : {};
    return error;
  }

  async function callTool(name, args) {
    const mode = await ready();
    let result;
    if (mode === "standard") result = await request("tools/call", { name, arguments: args });
    else if (mode === "alias") result = await globalThis.openai.callTool(name, args);
    else throw new Error("GPT 도구 연결을 사용할 수 없습니다. 이 화면을 ChatGPT 안에서 다시 열어주세요.");
    const error = resultError(result);
    if (error) throw error;
    return result;
  }

  function structured(result) {
    if (!isObject(result)) return null;
    if (isObject(result.structuredContent)) return result.structuredContent;
    if (isObject(result.toolOutput)) return structured(result.toolOutput) || result.toolOutput;
    return result;
  }

  function metadata(result) {
    if (!isObject(result)) return null;
    if (isObject(result.toolResponseMetadata)) return result.toolResponseMetadata;
    return isObject(result._meta) ? result._meta : null;
  }

  function publish(result) {
    for (const listener of subscribers) listener(clone(result));
  }

  function subscribe(listener) {
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  }

  function aliasSnapshot() {
    if (!isObject(globalThis.openai)) return null;
    return {
      toolInput: clone(globalThis.openai.toolInput),
      toolOutput: clone(globalThis.openai.toolOutput),
      toolResponseMetadata: clone(globalThis.openai.toolResponseMetadata),
    };
  }

  async function followUp(prompt) {
    const mode = await ready();
    if (mode === "standard") {
      await request("ui/message", { role: "user", content: [{ type: "text", text: prompt }] });
      return true;
    }
    if (mode === "alias" && typeof globalThis.openai?.sendFollowUpMessage === "function") {
      await globalThis.openai.sendFollowUpMessage({ prompt, scrollToBottom: true });
      return true;
    }
    return false;
  }

  async function withOneRecovery(operation, recoveryMessage) {
    try {
      return await operation();
    } catch (firstError) {
      if (firstError?.isToolResultError) throw firstError;
      publish({ localStatus: "recovering", message: recoveryMessage });
      return operation();
    }
  }

  function randomKey(slug) {
    const identity = globalThis.crypto?.randomUUID?.().replaceAll("-", "")
      || `${Date.now()}${Math.random().toString(16).slice(2)}`;
    return `domain-os-${slug || "app"}-${identity.slice(0, 32)}`;
  }

  async function generateTestApplication(input) {
    const outer = {
      mode: input.mode,
      workspaceRef: input.workspaceRef,
      arguments: {
        plan: clone(input.plan),
        idempotencyKey: input.idempotencyKey || randomKey(input.plan?.slug),
      },
    };
    let token = "";
    let receipt = "";
    let retry = null;
    try {
      const challenged = await callTool("pilot.application.generate", outer);
      const challenge = structured(challenged);
      if (!isObject(challenge) || challenge.status !== "approval_required") return challenged;
      token = String(metadata(challenged)?.widgetApprovalToken || "");
      if (!token || !challenge.challengeId) throw new Error("사용자 확인 토큰이 없어 앱을 만들지 않았습니다.");
      const approved = await withOneRecovery(
        () => callTool("approve_builder_mutation", {
          challengeId: challenge.challengeId,
          widgetApprovalToken: token,
        }),
        "확인 응답을 받지 못해 같은 앱 생성 요청을 안전하게 복구하고 있습니다.",
      );
      receipt = String(metadata(approved)?.confirmationReceipt || "");
      if (!receipt) throw new Error("서버 확인 영수증이 없어 앱을 만들지 않았습니다.");
      retry = clone(outer);
      retry.confirmationReceipt = receipt;
      return await withOneRecovery(
        () => callTool("pilot.application.generate", retry),
        "생성 결과를 받지 못해 중복 생성 없이 같은 요청을 확인하고 있습니다.",
      );
    } finally {
      token = "";
      receipt = "";
      if (retry) delete retry.confirmationReceipt;
      retry = null;
    }
  }

  function domainOsBinding() {
    return Object.freeze({
      generateTestApplication,
      askInConversation: followUp,
    });
  }

  function createFoundryLiteMcpAppsOsdk() {
    const osdk = (resource) => {
      if (resource === DomainOsStudio || resource?.apiName === DomainOsStudio.apiName) return domainOsBinding();
      throw new Error(`지원하지 않는 MCP App OSDK 리소스입니다: ${String(resource?.apiName || resource)}`);
    };
    osdk.initialize = ready;
    osdk.subscribe = subscribe;
    osdk.aliasSnapshot = aliasSnapshot;
    osdk.structured = structured;
    osdk.metadata = metadata;
    osdk.tearDown = tearDown;
    return Object.freeze(osdk);
  }

  function tearDown() {
    isTornDown = true;
    bridgeMode = "unavailable";
    for (const item of pending.values()) {
      if (item.timeoutId !== undefined) globalThis.clearTimeout(item.timeoutId);
      item.reject(new Error("GPT가 이 업무 화면을 종료했습니다."));
    }
    pending.clear();
    subscribers.clear();
  }

  globalThis.addEventListener("message", handleMessage);
  globalThis.addEventListener("openai:set_globals", (event) => {
    const detail = event && event.detail;
    publish(isObject(detail?.globals) ? detail.globals : detail);
  });
  globalThis.FoundryLiteMcpResources = Object.freeze({ DomainOsStudio });
  globalThis.createFoundryLiteMcpAppsOsdk = createFoundryLiteMcpAppsOsdk;
})();
