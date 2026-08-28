(() => {
  "use strict";

  const PROTOCOL_VERSION = "2026-01-26";
  const BusinessSystem = Object.freeze({ kind: "mcpApplication", apiName: "BusinessSystem" });
  const pending = new Map();
  const subscribers = new Set();
  let nextId = 1;
  let standardTarget = null;
  let bridgeReady = null;

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const clone = (value) => value === undefined ? undefined : JSON.parse(JSON.stringify(value));
  const targets = () => [globalThis, globalThis.parent]
    .filter((value, index, values) => value && typeof value.postMessage === "function" && values.indexOf(value) === index);

  function receive(event) {
    if (!targets().includes(event.source) || !isObject(event.data) || event.data.jsonrpc !== "2.0") return;
    const message = event.data;
    if (message.id !== undefined && pending.has(message.id)) {
      if (standardTarget && event.source !== standardTarget) return;
      standardTarget = event.source;
      const request = pending.get(message.id);
      pending.delete(message.id);
      globalThis.clearTimeout(request.timeout);
      if (message.error) request.reject(new Error(String(message.error.message || "업무 요청이 거부되었습니다.")));
      else request.resolve(message.result);
      return;
    }
    if (message.method === "ui/notifications/tool-result") publish(message.params?.result || message.params);
  }

  function request(method, params, timeoutMs = 15000) {
    const destinations = standardTarget ? [standardTarget] : targets();
    if (!destinations.length) return Promise.reject(new Error("GPT 업무 화면 연결을 찾지 못했습니다."));
    const id = `foundry-business-system-${nextId++}`;
    return new Promise((resolve, reject) => {
      const timeout = globalThis.setTimeout(() => {
        pending.delete(id);
        reject(new Error(`${method} 응답 시간이 초과되었습니다.`));
      }, timeoutMs);
      pending.set(id, { resolve, reject, timeout });
      destinations.forEach((target) => target.postMessage({ jsonrpc: "2.0", id, method, params }, "*"));
    });
  }

  async function initialize() {
    if (!targets().length && globalThis.openai?.callTool) return "alias";
    try {
      const result = await request("ui/initialize", {
        protocolVersion: PROTOCOL_VERSION,
        appInfo: { name: "foundry-lite-business-system", version: "1.0.0" },
        appCapabilities: { availableDisplayModes: ["inline", "fullscreen"] },
      }, 2500);
      if (!isObject(result) || result.protocolVersion !== PROTOCOL_VERSION) throw new Error("protocol mismatch");
      standardTarget?.postMessage({ jsonrpc: "2.0", method: "ui/notifications/initialized" }, "*");
      return "standard";
    } catch (_error) {
      return globalThis.openai?.callTool ? "alias" : "unavailable";
    }
  }

  function ready() {
    if (!bridgeReady) bridgeReady = initialize();
    return bridgeReady;
  }

  async function callTool(name, args = {}) {
    const mode = await ready();
    const result = mode === "standard"
      ? await request("tools/call", { name, arguments: args })
      : mode === "alias"
        ? await globalThis.openai.callTool(name, args)
        : null;
    if (!result) throw new Error("GPT 업무 도구를 사용할 수 없습니다.");
    if (result.isError === true) {
      const nested = result.structuredContent?.error || {};
      throw new Error(String(nested.message || "업무 요청을 완료하지 못했습니다."));
    }
    return result.structuredContent || result.toolOutput?.structuredContent || result.toolOutput || result;
  }

  function publish(value) {
    subscribers.forEach((listener) => listener(clone(value)));
  }

  function subscribe(listener) {
    subscribers.add(listener);
    return () => subscribers.delete(listener);
  }

  function binding() {
    return Object.freeze({
      loadDefinition: () => callTool("business_system.get"),
      searchWork: (objectType, options = {}) => callTool(`object.${objectType}.search`, { limit: 50, ...options }),
      executeAction: (actionType, payload) => callTool(`action.${actionType}.apply`, payload),
      approvalStatus: (reviewId) => callTool("action_approval.get", { reviewId }),
    });
  }

  function createFoundryLiteBusinessSystemOsdk() {
    const osdk = (resource) => {
      if (resource === BusinessSystem || resource?.apiName === BusinessSystem.apiName) return binding();
      throw new Error("지원하지 않는 업무 화면입니다.");
    };
    osdk.initialize = ready;
    osdk.subscribe = subscribe;
    osdk.aliasSnapshot = () => isObject(globalThis.openai) ? clone({
      toolInput: globalThis.openai.toolInput,
      toolOutput: globalThis.openai.toolOutput,
    }) : null;
    return Object.freeze(osdk);
  }

  globalThis.addEventListener("message", receive);
  globalThis.addEventListener("openai:set_globals", (event) => publish(event?.detail?.globals || event?.detail));
  globalThis.FoundryLiteBusinessSystemResources = Object.freeze({ BusinessSystem });
  globalThis.createFoundryLiteBusinessSystemOsdk = createFoundryLiteBusinessSystemOsdk;
})();
