import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const template = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const runtime = readFileSync(new URL("./foundry-lite-mcp-osdk.js", import.meta.url), "utf8");

test("GPT 업무 화면은 Workshop 정의와 위젯만 렌더링한다", () => {
  assert.match(template, /GPT와 외부 앱이 같은 업무 정의를 사용합니다/);
  assert.match(template, /data-workshop-widget/);
  assert.match(template, /experience\?\.workshopApp/);
  assert.match(template, /사람 확인 대기함/);
  assert.match(template, /business\.executeAction/);
  assert.match(template, /kind === "statusTracker"/);
  assert.match(template, /kind === "kanban"/);
  assert.match(template, /kind === "calendar"/);
  assert.match(template, /kind === "pivotTable"/);
  assert.match(template, /section\.span/);
  assert.match(template, /theme\.preset/);
  assert.match(template, /오늘의 업무 흐름/);
  assert.match(template, /capabilityGroups/);
  assert.match(template, /trustCenter/);
  assert.match(template, /approvalStatement/);
  assert.match(template, /statusDisplay/);
  assert.match(template, /displayValue/);
  assert.doesNotMatch(template, /Operational workspace|Live work pulse/);
  assert.doesNotMatch(template, /LIVE WORK SYSTEM|work_queue|action_panel|ai_suggestion_panel/);
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
