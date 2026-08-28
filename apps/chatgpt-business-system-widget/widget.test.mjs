import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const template = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const runtime = readFileSync(new URL("./foundry-lite-mcp-osdk.js", import.meta.url), "utf8");

test("GPT 업무 화면은 외부 앱과 같은 정의 지문과 업무 컴포넌트를 표시한다", () => {
  assert.match(template, /외부 앱과 같은 화면 정의/);
  assert.match(template, /AI 업무 제안/);
  assert.match(template, /사람 확인 대기함/);
  assert.match(template, /business\.executeAction/);
  assert.doesNotMatch(template, /tools\/call|action\.[A-Za-z].*\.apply|object\.[A-Za-z].*\.search/);
  assert.match(runtime, /createFoundryLiteBusinessSystemOsdk|BusinessSystem/);
});

test("고수준 업무 OSDK만 정의 조회·검색·Action·승인 상태 도구 이름을 조립한다", async () => {
  const calls = [];
  const context = {
    console,
    openai: {
      async callTool(name, args) {
        calls.push({ name, args: structuredClone(args) });
        return { structuredContent: { status: "ok" } };
      },
    },
    addEventListener() {},
    clearTimeout,
    setTimeout,
  };
  context.globalThis = context;
  context.parent = context;
  vm.runInNewContext(runtime, context, { filename: "foundry-lite-mcp-osdk.js" });
  const osdk = context.createFoundryLiteBusinessSystemOsdk();
  const business = osdk(context.FoundryLiteBusinessSystemResources.BusinessSystem);

  await business.loadDefinition();
  await business.searchWork("WorkItem", { search: "긴급" });
  await business.executeAction("CompleteWorkItem", { objectId: "work-1" });
  await business.approvalStatus("review-1");

  assert.deepEqual(calls, [
    { name: "business_system.get", args: {} },
    { name: "object.WorkItem.search", args: { limit: 50, search: "긴급" } },
    { name: "action.CompleteWorkItem.apply", args: { objectId: "work-1" } },
    { name: "action_approval.get", args: { reviewId: "review-1" } },
  ]);
});
