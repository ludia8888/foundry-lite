import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const script = html.match(/<script>([\s\S]*?)<\/script>/)?.[1];
let harnessSequence = 0;

function ontologyView(stage = "awaiting_review", nextActions = ["submit_release_decision"]) {
  return {
    release: {
      releaseKind: "ontology",
      proposalId: "ont-proposal-7",
      stage,
      candidate: {
        id: "ont-candidate-7",
        status: stage === "awaiting_review" ? "submitted" : stage === "active" ? "applied" : stage,
        title: "주문 Ontology 7",
        description: "주문 객체 계약을 내부 브랜치에 반영합니다.",
        fingerprint: "sha256:candidate-seven",
        assigneeUserId: "reviewer-42",
        validation: [{ label: "호환성 검증", status: "passed" }],
        appliedOntologyVersion: stage === "active" ? { versionNumber: 7 } : null,
      },
      releaseEvidence: {},
      nextActions,
    },
  };
}

function pipelineView(stage = "merged", nextActions = ["deploy_release"], isRollbackReady = false) {
  const rollbackTarget = isRollbackReady
    ? { pipelineId: "orders-pipeline", targetVersionId: "pipeline-v17", rolledBackFromId: "deployment-18" }
    : undefined;
  const applicationRollbackTarget = isRollbackReady
    ? {
        targetDeployId: "dep-render-17",
        targetCommitId: "7".repeat(40),
        rolledBackFromDeployId: "dep-render-18",
      }
    : undefined;
  const promotedDeployment = stage === "deployed"
    ? { id: "deployment-18", versionId: "pipeline-v18", status: "PROMOTED", environment: "production" }
    : null;
  return {
    release: {
      releaseKind: "pipeline",
      proposalId: "pipe-proposal-18",
      stage,
      candidate: {
        id: "pipe-candidate-18",
        pipelineId: "orders-pipeline",
        status: stage === "merged" ? "executed" : stage,
        title: "주문 Pipeline 18",
        description: "승인된 그래프를 내부 브랜치에 병합하고 PROMOTED로 승격합니다.",
        graphFingerprint: "sha256:pipeline-eighteen",
        assignedTo: "release-operator-9",
      },
      releaseEvidence: {
        pipelineId: "orders-pipeline",
        mergedVersion: { id: "pipeline-v18", proposalId: "pipe-proposal-18" },
        candidateDeployment: promotedDeployment,
        currentDeployment: promotedDeployment,
        ...(rollbackTarget ? { rollbackTarget } : {}),
        ...(applicationRollbackTarget
          ? { externalDelivery: { applicationRollbackTarget } }
          : {}),
      },
      nextActions,
      ...(rollbackTarget ? { rollbackTarget } : {}),
    },
  };
}

function workspaceView() {
  return {
    release: {
      releaseKind: "pipeline",
      proposalId: "pending-branch",
      stage: "workspace_ready",
      candidate: {
        id: "pending-branch",
        title: "orders-candidate branch",
        branchName: "orders-candidate",
        pipelineId: "orders-pipeline",
      },
      releaseEvidence: {
        branchPlan: {
          releaseKind: "pipeline",
          branchName: "orders-candidate",
          pipelineId: "orders-pipeline",
        },
      },
      nextActions: ["create_release_branch"],
    },
  };
}

function claimableView() {
  const view = ontologyView("awaiting_assignment", ["assign_release_reviewer"]);
  view.release.candidate.assigneeUserId = null;
  view.release.candidate.canCurrentUserClaim = true;
  view.release.candidate.canCurrentUserReview = false;
  return view;
}

function emptyInboxView() {
  const view = ontologyView("empty_inbox", []);
  view.release.proposalId = "empty-inbox";
  view.release.candidate = {
    id: "empty-inbox",
    title: "검토할 제안이 없습니다",
    description: "미배정 또는 나에게 배정된 검토 가능 제안만 표시됩니다.",
    reviewPolicy: { requiresAssignment: true, requiresSeparateReviewer: false },
  };
  view.release.releaseEvidence = { reviewInbox: { count: 0, items: [] } };
  return view;
}

function evidenceRichView() {
  const view = pipelineView("failed", ["get_release_status"]);
  Object.assign(view.release.releaseEvidence, {
    changeDiff: {
      changed: true,
      baseFingerprint: "sha256:base-seventeen",
      graphFingerprint: "sha256:pipeline-eighteen",
      summary: { totalChangeCount: 2, addedCount: 1, modifiedCount: 1, removedCount: 0 },
      items: [
        {
          changeType: "added",
          resourceType: "pipeline_node",
          resourceId: "normalize-orders",
          summary: "주문 정규화 노드 추가",
        },
        {
          changeType: "modified",
          resourceType: "pipeline_output_contract",
          resourceId: "outputContract",
          summary: "출력 계약 변경",
        },
      ],
    },
    validationEvidence: [
      {
        id: "pipeline-durable-tests",
        label: "현재 그래프 검증과 선언 테스트",
        status: "passed",
        proofKind: "static_graph_output_contract",
        details: {
          testCount: 3,
          declaredTestCount: 2,
          failureCount: 0,
          isCurrentGraph: true,
          isDataExecution: false,
          proofVersion: "pipeline-static-review-v1",
          evaluatedChecks: ["graph_validation", "output_dataset_and_contract"],
        },
      },
      {
        id: "external-ci-receipt",
        label: "후보 범위 외부 CI 영수증",
        status: "not_available",
        proofKind: "external_ci",
        details: { reason: "후보 범위 외부 CI 영수증이 저장되지 않음" },
      },
    ],
    impactScope: {
      summary: "운영 리소스 2개가 영향 범위에 포함됨",
      resources: [
        {
          type: "pipeline_node",
          id: "normalize-orders",
          label: "주문 정규화",
          impact: "added",
        },
        {
          type: "output_dataset",
          id: "orders.curated",
          label: "orders.curated",
          impact: "serving_output",
        },
      ],
    },
    riskClassification: {
      level: "high",
      reasons: ["출력 계약이 변경됩니다.", "외부 CI 영수증이 없습니다."],
      policyVersion: "foundry-lite-release-risk-v1",
      isComplete: false,
      missingEvidence: ["external_ci"],
    },
    executionTimeline: [
      {
        event: "pipeline.proposal.submitted",
        label: "Pipeline 제안 제출",
        status: "running",
        at: "2026-08-09T04:00:00Z",
        actorDisplayName: "builder-7",
        requestId: "req-submit-7",
      },
      {
        event: "governed_release.action.failed",
        label: "release deploy 실패",
        status: "failed",
        at: "2026-08-09T04:03:00Z",
        actorDisplayName: "release-operator-9",
        requestId: "req-deploy-7",
      },
    ],
    failureDetails: {
      code: "deployment_precondition_failed",
      message: "운영 배포 전제조건을 확인하지 못했습니다.",
      stage: "governed_release.action.failed",
      at: "2026-08-09T04:03:00Z",
      requestId: "req-deploy-7",
      isRetryable: false,
      knownNotCommitted: true,
    },
    auditEvidence: [
      {
        auditEventId: "audit-7",
        eventType: "governed_release.action.failed",
        label: "release deploy 실패",
        status: "failed",
        resourceType: "pipeline_proposal",
        resourceId: "pipe-proposal-18",
        actorUserId: "release-operator-9",
        requestId: "req-deploy-7",
        correlationId: "release-run-7",
        at: "2026-08-09T04:03:00Z",
      },
    ],
  });
  return view;
}

function nodeStub() {
  let markup = "";
  const node = { dataset: {}, buttons: [], addEventListener() {} };
  Object.defineProperty(node, "innerHTML", {
    get: () => markup,
    set(value) {
      markup = String(value);
      node.buttons = [];
      const buttonPattern = /<button\b([\s\S]*?)>([\s\S]*?)<\/button>/g;
      for (const match of markup.matchAll(buttonPattern)) {
        const attributes = match[1];
        const action = attributes.match(/data-action="([^"]+)"/)?.[1];
        if (!action) continue;
        const handlers = new Map();
        const button = {
          dataset: { action },
          disabled: /(?:^|\s)disabled(?:\s|$)/.test(attributes),
          addEventListener(name, handler) {
            handlers.set(name, handler);
          },
          click() {
            if (button.disabled) return undefined;
            return handlers.get("click")?.({ currentTarget: button, target: button });
          },
        };
        node.buttons.push(button);
      }
    },
  });
  return node;
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function createHarness(callTool, structuredContent = ontologyView(), sendFollowUpMessage, options = {}) {
  const app = nodeStub();
  const listeners = new Map();
  const standardMessages = [];
  const persistedWidgetStates = [];
  const pendingTimers = new Map();
  const clearedTimers = [];
  let timerSequence = 0;
  const harnessId = ++harnessSequence;
  const aliasToolOutput = Object.prototype.hasOwnProperty.call(options, "aliasToolOutput")
    ? options.aliasToolOutput
    : { structuredContent };
  let context;
  let selfSource;
  function handleStandardPostMessage(message, source, echoToWidget) {
    standardMessages.push(clone(message));
    if (echoToWidget) listeners.get("message")?.({ source, data: clone(message) });
    if (message.method === "ui/initialize" && message.id !== undefined) {
      listeners.get("message")?.({
        source,
        data: {
          jsonrpc: "2.0",
          id: message.id,
          result: { protocolVersion: "2026-01-26", hostCapabilities: {} },
        },
      });
      return;
    }
    if (message.method === "ui/update-model-context" && message.id !== undefined) {
      persistedWidgetStates.push(clone(message.params));
      listeners.get("message")?.({
        source,
        data: { jsonrpc: "2.0", id: message.id, result: {} },
      });
      return;
    }
    if (message.method && message.id !== undefined) {
      const result = options.standardHostHandler?.(clone(message));
      if (result !== undefined) {
        listeners.get("message")?.({
          source,
          data: result?.$jsonRpcError
            ? { jsonrpc: "2.0", id: message.id, error: result.$jsonRpcError }
            : { jsonrpc: "2.0", id: message.id, result },
        });
      }
    }
  }
  const parent = options.standardHost
    ? {
        postMessage(message) {
          handleStandardPostMessage(message, parent, false);
        },
      }
    : undefined;
  context = {
    console,
    Date,
    Error,
    Math,
    Object,
    Promise,
    Set,
    String,
    setTimeout(callback) {
      if (options.manualTimers) {
        const timerId = ++timerSequence;
        pendingTimers.set(timerId, callback);
        return timerId;
      }
      callback();
      return 1;
    },
    clearTimeout(timerId) {
      clearedTimers.push(timerId);
      pendingTimers.delete(timerId);
    },
    crypto: {
      randomUUID: (() => {
        let counter = 0;
        return () => `00000000-0000-4000-8000-${String((harnessId * 100000) + (++counter)).padStart(12, "0")}`;
      })(),
    },
    document: {
      getElementById: () => app,
      querySelectorAll: (selector) => selector === "[data-action]" ? app.buttons : [],
    },
    ...(options.omitOpenAI
      ? {}
      : {
          openai: {
            ...(options.omitAliasToolOutput ? {} : { toolOutput: aliasToolOutput }),
            ...(Object.prototype.hasOwnProperty.call(options, "aliasToolInput")
              ? { toolInput: options.aliasToolInput }
              : {}),
            ...(Object.prototype.hasOwnProperty.call(options, "aliasWidgetState")
              ? { widgetState: options.aliasWidgetState }
              : {}),
            callTool,
            async setWidgetState(value) {
              persistedWidgetStates.push(clone(value));
            },
            ...(sendFollowUpMessage ? { sendFollowUpMessage } : {}),
          },
        }),
    ...(options.standardHostSelf
      ? {
          __standardSelfPostMessage(message, source) {
            selfSource = source;
            handleStandardPostMessage(message, source, true);
          },
        }
      : {}),
    ...(parent ? { parent } : {}),
    addEventListener(name, handler) {
      listeners.set(name, handler);
    },
  };
  context.globalThis = context;
  const executableScript = options.standardHostSelf
    ? `globalThis.postMessage = function postMessage(message) {
        globalThis.__standardSelfPostMessage(message, globalThis);
      };\n${script}`
    : script;
  vm.runInNewContext(executableScript, context, { filename: "chatgpt-release-widget/index.html" });
  return {
    app,
    context,
    listeners,
    standardMessages,
    persistedWidgetStates,
    pendingTimers,
    clearedTimers,
    api: context.__foundryReleaseWidgetTest,
    button: (action) => app.buttons.find((item) => item.dataset.action === action),
    sendStandardMessage(message) {
      listeners.get("message")?.({ source: parent || selfSource || context, data: message });
    },
    sendToolResult(result) {
      listeners.get("message")?.({
        source: parent || selfSource || context,
        data: { jsonrpc: "2.0", method: "ui/notifications/tool-result", params: result },
      });
    },
    runNextTimer() {
      const timerId = [...pendingTimers.keys()].sort((left, right) => left - right)[0];
      if (timerId === undefined) return false;
      const callback = pendingTimers.get(timerId);
      pendingTimers.delete(timerId);
      callback();
      return true;
    },
  };
}

async function capturedAction(action, structuredContent) {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: `token-${action}` } };
    }
    return { structuredContent };
  }, structuredContent);
  await harness.api.invokeAction(action);
  const prepared = calls.find((call) => call.name === "prepare_release_action");
  assert.ok(prepared, `${action} must prepare one governed release action`);
  return {
    arguments: prepared.args.arguments,
    requestId: harness.api.getState().lastRequestId,
  };
}

test("같은 immutable release target은 페이지를 다시 만들어도 같은 key를 쓰고 requestId만 바뀐다", async () => {
  const first = await capturedAction("approve", ontologyView());
  const recreated = await capturedAction("approve", ontologyView());

  assert.deepEqual(first.arguments, recreated.arguments);
  assert.equal(first.arguments.idempotencyKey, recreated.arguments.idempotencyKey);
  assert.notEqual(first.requestId, recreated.requestId);
  assert.match(first.arguments.idempotencyKey, /^release-widget-approve-[0-9a-f]{32}$/);
  assert.doesNotMatch(first.arguments.idempotencyKey, /ont-proposal|candidate-seven/);
});

test("target, decision, version, branch, rollback target가 달라지면 deterministic key도 달라진다", async () => {
  const base = await capturedAction("approve", ontologyView());
  const changedProposalView = ontologyView();
  changedProposalView.release.proposalId = "ont-proposal-8";
  const changedProposal = await capturedAction("approve", changedProposalView);
  const changedFingerprintView = ontologyView();
  changedFingerprintView.release.candidate.fingerprint = "sha256:candidate-eight";
  const changedFingerprint = await capturedAction("approve", changedFingerprintView);
  const changedDecision = await capturedAction("reject", ontologyView());

  const deployV18 = await capturedAction("deploy", pipelineView());
  const deployV19View = pipelineView();
  deployV19View.release.releaseEvidence.mergedVersion.id = "pipeline-v19";
  const deployV19 = await capturedAction("deploy", deployV19View);

  const branchA = await capturedAction("create", workspaceView());
  const branchBView = workspaceView();
  branchBView.release.candidate.branchName = "orders-candidate-v2";
  const branchB = await capturedAction("create", branchBView);

  const rollbackV17 = await capturedAction("rollback", pipelineView("deployed", ["rollback_release"], true));
  const rollbackV16View = pipelineView("deployed", ["rollback_release"], true);
  rollbackV16View.release.rollbackTarget.targetVersionId = "pipeline-v16";
  rollbackV16View.release.releaseEvidence.rollbackTarget.targetVersionId = "pipeline-v16";
  const rollbackV16 = await capturedAction("rollback", rollbackV16View);
  const rollbackExternalTargetView = pipelineView("deployed", ["rollback_release"], true);
  rollbackExternalTargetView.release.releaseEvidence.externalDelivery.applicationRollbackTarget.targetDeployId = "dep-render-16";
  const rollbackExternalTarget = await capturedAction("rollback", rollbackExternalTargetView);

  assert.notEqual(base.arguments.idempotencyKey, changedProposal.arguments.idempotencyKey);
  assert.notEqual(base.arguments.idempotencyKey, changedFingerprint.arguments.idempotencyKey);
  assert.notEqual(base.arguments.idempotencyKey, changedDecision.arguments.idempotencyKey);
  assert.notEqual(deployV18.arguments.idempotencyKey, deployV19.arguments.idempotencyKey);
  assert.notEqual(branchA.arguments.idempotencyKey, branchB.arguments.idempotencyKey);
  assert.notEqual(rollbackV17.arguments.idempotencyKey, rollbackV16.arguments.idempotencyKey);
  assert.notEqual(rollbackV17.arguments.idempotencyKey, rollbackExternalTarget.arguments.idempotencyKey);
});

test("Ontology 결정은 canonical proposal schema와 _meta 일회용 토큰만 사용한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return {
        _meta: { widgetConfirmationToken: "one-time-secret" },
        structuredContent: { widgetConfirmationToken: "must-not-use-structured-content" },
        content: [{ type: "text", text: "must-not-use-content" }],
      };
    }
    return { structuredContent: ontologyView("approved", ["execute_approved_release"]) };
  });

  await harness.api.invokeAction("approve");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "submit_release_decision"]);
  const envelope = calls[0].args;
  const expectedArguments = envelope.arguments;
  assert.equal(envelope.targetTool, "submit_release_decision");
  assert.deepEqual(Object.keys(expectedArguments).sort(), [
    "decision",
    "expectedFingerprint",
    "idempotencyKey",
    "proposalId",
    "releaseKind",
  ]);
  assert.equal(expectedArguments.releaseKind, "ontology");
  assert.equal(expectedArguments.proposalId, "ont-proposal-7");
  assert.equal(expectedArguments.decision, "approve");
  assert.equal(expectedArguments.expectedFingerprint, "sha256:candidate-seven");
  assert.deepEqual(
    Object.fromEntries(Object.entries(calls[1].args).filter(([key]) => key !== "widgetConfirmationToken")),
    expectedArguments,
  );
  assert.equal(calls[1].args.widgetConfirmationToken, "one-time-secret");
  assert.equal(harness.api.getState().snapshot.status, "approved");
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /one-time-secret/);
  assert.doesNotMatch(harness.app.innerHTML, /one-time-secret/);
});

test("GPT 시작 카드에서 격리 Pipeline 브랜치를 exact plan으로 생성한다", async () => {
  const calls = [];
  const followUps = [];
  const created = workspaceView();
  created.release.stage = "branch_created";
  created.release.proposalId = "branch:pipeline-branch-7";
  created.release.releaseEvidence = {
    branchId: "pipeline-branch-7",
    builderWorkspaceRef: "pipeline-branch:pipeline-branch-7",
  };
  created.release.nextActions = [];
  const harness = createHarness(
    async (name, args) => {
      calls.push({ name, args: clone(args) });
      if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "branch-token" } };
      return { structuredContent: created };
    },
    workspaceView(),
    async (message) => followUps.push(clone(message)),
  );

  await harness.api.invokeAction("create");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "create_release_branch"]);
  assert.deepEqual(
    Object.keys(calls[0].args.arguments).sort(),
    ["branchName", "idempotencyKey", "pipelineId", "releaseKind"],
  );
  assert.equal(calls[0].args.arguments.branchName, "orders-candidate");
  assert.equal(calls[0].args.arguments.pipelineId, "orders-pipeline");
  assert.equal(calls[1].args.widgetConfirmationToken, "branch-token");
  assert.equal(harness.api.getState().snapshot.builderWorkspaceRef, "pipeline-branch:pipeline-branch-7");
  assert.equal(followUps.length, 1);
  assert.match(followUps[0].prompt, /mode: data_integration/);
  assert.match(followUps[0].prompt, /workspaceRef: pipeline-branch:pipeline-branch-7/);
  assert.match(followUps[0].prompt, /제가 직접 승인/);
  assert.equal(followUps[0].scrollToBottom, true);
  assert.match(harness.api.getState().message, /이 대화에 전달/);
});

test("서버가 허용한 후보만 prepare 확인 뒤 exact schema로 GitHub PR에 게시한다", async () => {
  const calls = [];
  const publishable = ontologyView("validated", ["publish_release_candidate"]);
  const published = ontologyView("awaiting_assignment", ["assign_release_reviewer"]);
  published.release.candidate.assigneeUserId = null;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: "publish-token" } };
    }
    return { structuredContent: published };
  }, publishable);

  assert.match(harness.app.innerHTML, /data-action="publish"/);
  assert.match(harness.app.innerHTML, /GitHub 후보 PR 게시/);
  assert.doesNotMatch(createHarness(async () => ({}), ontologyView()).app.innerHTML, /GitHub 후보 PR 게시/);

  await harness.api.invokeAction("publish");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "publish_release_candidate"]);
  const envelope = calls[0].args;
  assert.equal(envelope.targetTool, "publish_release_candidate");
  assert.deepEqual(Object.keys(envelope.arguments).sort(), ["idempotencyKey", "proposalId", "releaseKind"]);
  assert.equal(envelope.arguments.releaseKind, "ontology");
  assert.equal(envelope.arguments.proposalId, "ont-proposal-7");
  assert.match(envelope.arguments.idempotencyKey, /^release-widget-publish-[0-9a-f]{32}$/);
  assert.deepEqual(
    Object.fromEntries(Object.entries(calls[1].args).filter(([key]) => key !== "widgetConfirmationToken")),
    envelope.arguments,
  );
  assert.equal(calls[1].args.widgetConfirmationToken, "publish-token");
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /publish-token/);
  assert.doesNotMatch(harness.app.innerHTML, /publish-token/);
});

test("검토함 제안은 작성자 여부와 무관하게 현재 사람이 담당을 수락한 뒤 승인 가능하다", async () => {
  const calls = [];
  const assigned = ontologyView("awaiting_review", ["submit_release_decision"]);
  assigned.release.candidate.canCurrentUserReview = true;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "claim-token" } };
    return { structuredContent: assigned };
  }, claimableView());

  await harness.api.invokeAction("claim");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "assign_release_reviewer"]);
  assert.equal(calls[0].args.arguments.proposalId, "ont-proposal-7");
  assert.equal(calls[1].args.widgetConfirmationToken, "claim-token");
  assert.equal(harness.api.getState().snapshot.canCurrentUserReview, true);
});

test("실제 proposal이 없는 workspace·branch·빈 검토함에서는 상태 조회를 fail-closed한다", async () => {
  const branchCreated = workspaceView();
  branchCreated.release.stage = "branch_created";
  branchCreated.release.proposalId = "branch:pipeline-branch-7";
  branchCreated.release.candidate.id = "pipeline-branch-7";
  branchCreated.release.releaseEvidence = {
    branchId: "pipeline-branch-7",
    builderWorkspaceRef: "pipeline-branch:pipeline-branch-7",
  };
  branchCreated.release.nextActions = [];

  for (const view of [workspaceView(), branchCreated, emptyInboxView()]) {
    const calls = [];
    const harness = createHarness(async (name, args) => {
      calls.push({ name, args: clone(args) });
      return { structuredContent: view };
    }, view);

    assert.equal(harness.button("refresh").disabled, true);
    assert.match(harness.app.innerHTML, /서버 조회 가능한 릴리스 제안이 아직 없음/);
    await harness.api.invokeAction("refresh");
    assert.deepEqual(calls, []);
  }

  const proposal = ontologyView();
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    return { structuredContent: proposal };
  }, proposal);

  assert.equal(harness.button("refresh").disabled, false);
  await harness.api.invokeAction("refresh");
  assert.deepEqual(calls, [{
    name: "get_release_status",
    args: { releaseKind: "ontology", proposalId: "ont-proposal-7" },
  }]);
});

test("승인 실행은 서버가 제시한 external source-control exact snapshot을 그대로 결합한다", async () => {
  const calls = [];
  const approved = pipelineView("approved", ["execute_approved_release"]);
  approved.release.releaseEvidence.externalSourceControl = {
    provider: "github",
    candidate: {
      baseSha: "base-sha-17",
      headSha: "head-sha-18",
      checksFingerprint: "sha256:checks-18",
      rulesFingerprint: "sha256:rules-main",
      providerRequestId: "must-not-forward",
    },
  };
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "execute-token" } };
    return { structuredContent: pipelineView("merged", ["deploy_release"]) };
  }, approved);

  assert.deepEqual(harness.api.getState().snapshot.externalSourceCandidate, {
    baseSha: "base-sha-17",
    headSha: "head-sha-18",
    checksFingerprint: "sha256:checks-18",
    rulesFingerprint: "sha256:rules-main",
  });

  await harness.api.invokeAction("execute");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "execute_approved_release"]);
  const expectedArguments = calls[0].args.arguments;
  assert.deepEqual(Object.keys(expectedArguments).sort(), [
    "expectedFingerprint",
    "expectedSourceBaseSha",
    "expectedSourceChecksFingerprint",
    "expectedSourceHeadSha",
    "expectedSourceRulesFingerprint",
    "idempotencyKey",
    "proposalId",
    "releaseKind",
  ]);
  assert.equal(expectedArguments.expectedSourceBaseSha, "base-sha-17");
  assert.equal(expectedArguments.expectedSourceHeadSha, "head-sha-18");
  assert.equal(expectedArguments.expectedSourceChecksFingerprint, "sha256:checks-18");
  assert.equal(expectedArguments.expectedSourceRulesFingerprint, "sha256:rules-main");
  assert.equal(Object.prototype.hasOwnProperty.call(expectedArguments, "providerRequestId"), false);
  assert.deepEqual(
    Object.fromEntries(Object.entries(calls[1].args).filter(([key]) => key !== "widgetConfirmationToken")),
    expectedArguments,
  );
  assert.equal(calls[1].args.widgetConfirmationToken, "execute-token");
});

test("Pipeline 배포는 mergedVersion을 versionId로 사용한 정확한 schema로 호출한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "deploy-token" } };
    return { structuredContent: pipelineView("deployed", ["rollback_release"], true) };
  }, pipelineView());

  await harness.api.invokeAction("deploy");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "deploy_release"]);
  const expectedArguments = calls[0].args.arguments;
  assert.deepEqual(Object.keys(expectedArguments).sort(), ["idempotencyKey", "pipelineId", "proposalId", "releaseKind", "versionId"]);
  assert.equal(expectedArguments.releaseKind, "pipeline");
  assert.equal(expectedArguments.proposalId, "pipe-proposal-18");
  assert.equal(expectedArguments.pipelineId, "orders-pipeline");
  assert.equal(expectedArguments.versionId, "pipeline-v18");
  assert.equal(calls[1].args.widgetConfirmationToken, "deploy-token");
  assert.equal(harness.api.getState().snapshot.status, "deployed");
});

test("Pipeline 롤백은 서버 제공 rollbackTarget 필드만 releaseKind와 멱등키에 결합한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "rollback-token" } };
    return { structuredContent: pipelineView("superseded", [], false) };
  }, pipelineView("deployed", ["rollback_release"], true));

  await harness.api.invokeAction("rollback");

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "rollback_release"]);
  const expectedArguments = calls[0].args.arguments;
  assert.deepEqual(Object.keys(expectedArguments).sort(), [
    "idempotencyKey",
    "pipelineId",
    "proposalId",
    "releaseKind",
    "rolledBackFromDeployId",
    "rolledBackFromId",
    "targetCommitId",
    "targetDeployId",
    "targetVersionId",
  ]);
  assert.equal(expectedArguments.pipelineId, "orders-pipeline");
  assert.equal(expectedArguments.targetVersionId, "pipeline-v17");
  assert.equal(expectedArguments.rolledBackFromId, "deployment-18");
  assert.equal(expectedArguments.targetDeployId, "dep-render-17");
  assert.equal(expectedArguments.targetCommitId, "7".repeat(40));
  assert.equal(expectedArguments.rolledBackFromDeployId, "dep-render-18");
  assert.equal(expectedArguments.releaseKind, "pipeline");
  assert.equal(expectedArguments.proposalId, "pipe-proposal-18");
  assert.equal(calls[1].args.widgetConfirmationToken, "rollback-token");
});

test("Pipeline 외부 배포 진행·미확인·실패·교체 상태를 마지막 진행 단계와 구체적 문구로 표시한다", () => {
  const expectedLabels = {
    deploying: "외부 운영 배포 진행 중",
    deployment_unverified: "외부 운영 배포 확인 필요",
    deployment_failed: "외부 운영 배포 실패",
    superseded: "현재 운영판에서 교체됨",
  };

  for (const [stage, expectedLabel] of Object.entries(expectedLabels)) {
    const harness = createHarness(async () => ({}), pipelineView(stage, ["get_release_status"]));
    const output = harness.app.innerHTML;

    assert.match(output, new RegExp(expectedLabel));
    const finalRail = stage === "superseded"
      ? "운영판 교체 완료 · 롤백/후속 배포"
      : "PROMOTED · 외부 배포";
    assert.match(output, new RegExp(`<li class="rail-step is-current" aria-current="step"><span>${finalRail}</span></li>`));
  }
});

test("정상 운영 완료와 호스티드 배포·복구 리허설은 별도 행과 별도 의미로 표시한다", () => {
  const labels = {
    blocked: "호스티드 배포·복구 리허설 증거 차단",
    ready_for_live_run: "호스티드 배포·복구 리허설 준비 · 아직 미검증",
    live_verified: "호스티드 배포·복구 리허설 검증됨",
  };
  for (const [status, label] of Object.entries(labels)) {
    const view = pipelineView("merged", []);
    view.release.operationalCompletion = {
      completionPurpose: "normal_operational_release",
      isComplete: false,
      stage: "merged",
      isRollbackRehearsal: false,
    };
    view.release.liveReadiness = {
      status,
      is_ready_for_live_run: status !== "blocked",
      is_live_verified: status === "live_verified",
      blockers: status === "live_verified" ? [] : ["authentic_live_collector_required"],
      attestation: status === "live_verified"
        ? {
            attestationPurpose: "rollback_rehearsal",
            attestationId: "attestation-rehearsal-7",
            collectorVersion: "collector-v2",
            collectedAt: "2026-08-09T05:00:00Z",
            validUntil: "2026-08-09T06:00:00Z",
          }
        : null,
    };
    const harness = createHarness(async () => ({}), view);
    assert.match(harness.app.innerHTML, new RegExp(label));
    assert.match(harness.app.innerHTML, /현재 릴리스 운영 미완료 · 내부 브랜치 병합 완료/);
    assert.doesNotMatch(harness.app.innerHTML, /현재 릴리스 정상 운영 완료/);
    if (status !== "live_verified") assert.match(harness.app.innerHTML, /authentic_live_collector_required/);
    if (status === "live_verified") {
      assert.match(harness.app.innerHTML, /영수증 attestation-rehearsal-7/);
      assert.match(harness.app.innerHTML, /Collector collector-v2/);
    }
    assert.match(harness.app.innerHTML, /data-action="deploy"[\s\S]*?disabled/);
  }

  const completed = pipelineView("deployed", []);
  completed.release.operationalCompletion = {
    completionPurpose: "normal_operational_release",
    isComplete: true,
    stage: "deployed",
    isRollbackRehearsal: false,
  };
  completed.release.liveReadiness = {
    status: "blocked",
    is_ready_for_live_run: false,
    is_live_verified: false,
    blockers: ["rollback_rehearsal_not_run"],
  };
  const completedHarness = createHarness(async () => ({}), completed);
  assert.match(completedHarness.app.innerHTML, /현재 릴리스 정상 운영 완료/);
  assert.match(completedHarness.app.innerHTML, /호스티드 배포·복구 리허설 증거 차단/);
});

test("실제 등록된 버튼 클릭은 MCP Apps 표준 bridge만 사용하고 Builder 인계를 ui/message 배열로 보낸다", async () => {
  const aliasCalls = [];
  const aliasMessages = [];
  const created = workspaceView();
  created.release.stage = "branch_created";
  created.release.candidate.status = "branch_created";
  created.release.releaseEvidence.builderWorkspaceRef = "release-workspace:orders-candidate";
  created.release.nextActions = [];
  const harness = createHarness(
    async (name, args) => aliasCalls.push({ name, args }),
    workspaceView(),
    async (message) => aliasMessages.push(message),
    {
      standardHost: true,
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "prepare_release_action") {
          return { _meta: { widgetConfirmationToken: "standard-create-token" } };
        }
        if (message.method === "tools/call" && message.params.name === "create_release_branch") {
          return { structuredContent: created };
        }
        if (message.method === "ui/message") return {};
        return {};
      },
    },
  );

  assert.equal(harness.button("create").disabled, false);
  await harness.button("create").click();

  assert.equal(harness.api.getBridgeMode(), "standard");
  const initialize = harness.standardMessages.find((message) => message.method === "ui/initialize");
  assert.equal(initialize.params.protocolVersion, "2026-01-26");
  assert.deepEqual(initialize.params.appCapabilities.availableDisplayModes, ["inline"]);
  assert.match(
    script,
    /MCP_APPS_INITIALIZE_TIMEOUT_MS = 15000/,
    "host sandbox setup must have enough time to complete before compatibility fallback",
  );
  assert.ok(harness.standardMessages.some((message) => message.method === "ui/notifications/initialized" && message.id === undefined));
  const toolCalls = harness.standardMessages.filter((message) => message.method === "tools/call");
  assert.deepEqual(toolCalls.map((message) => message.params.name), [
    "prepare_release_action",
    "create_release_branch",
  ]);
  assert.equal(toolCalls[1].params.arguments.widgetConfirmationToken, "standard-create-token");
  const followUp = harness.standardMessages.find((message) => message.method === "ui/message");
  assert.equal(followUp.params.role, "user");
  assert.ok(Array.isArray(followUp.params.content));
  assert.equal(followUp.params.content[0].type, "text");
  assert.match(followUp.params.content[0].text, /release-workspace:orders-candidate/);
  assert.equal(aliasCalls.length, 0);
  assert.equal(aliasMessages.length, 0);
});

test("ChatGPT sandbox의 same-window MCP Apps adapter가 초기 결과와 버튼 호출을 전달한다", async () => {
  const created = workspaceView();
  created.release.stage = "branch_created";
  created.release.candidate.status = "branch_created";
  created.release.releaseEvidence.builderWorkspaceRef = "release-workspace:self-window-candidate";
  created.release.nextActions = [];
  const harness = createHarness(
    async () => {
      throw new Error("표준 self-window bridge가 alias transport로 내려가면 안 됩니다.");
    },
    workspaceView(),
    undefined,
    {
      standardHostSelf: true,
      omitAliasToolOutput: true,
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "prepare_release_action") {
          return { _meta: { widgetConfirmationToken: "self-window-create-token" } };
        }
        if (message.method === "tools/call" && message.params.name === "create_release_branch") {
          return { structuredContent: created };
        }
        if (message.method === "ui/message") return {};
        return {};
      },
    },
  );

  harness.sendToolResult({ structuredContent: workspaceView() });
  assert.equal(harness.button("create").disabled, false);
  await harness.button("create").click();

  assert.equal(harness.api.getBridgeMode(), "standard");
  assert.deepEqual(
    harness.standardMessages.filter((message) => message.method === "tools/call").map((message) => message.params.name),
    ["prepare_release_action", "create_release_branch"],
  );
  assert.ok(
    harness.standardMessages.some(
      (message) => message.method === "ui/notifications/initialized" && message.id === undefined,
    ),
  );
  assert.match(harness.app.innerHTML, /self-window-candidate/);
});

test("빈 초기 toolOutput은 unknown 릴리스로 렌더링하지 않고 늦게 채워진 서버 스냅샷을 bounded polling으로 복구한다", () => {
  const harness = createHarness(
    async () => ({}),
    workspaceView(),
    undefined,
    {
      manualTimers: true,
      aliasToolOutput: {},
    },
  );

  assert.equal(harness.api.normalizeSnapshot({}), null);
  assert.equal(harness.api.normalizeSnapshot({ structuredContent: {} }), null);
  assert.equal(harness.api.getState().snapshot, null);
  assert.match(harness.app.innerHTML, /운영 릴리스 정보를 기다리는 중입니다/);
  assert.equal(harness.pendingTimers.size, 1);

  harness.context.openai.toolOutput = { structuredContent: workspaceView() };
  assert.equal(harness.runNextTimer(), true);

  assert.equal(harness.api.getState().snapshot.proposalId, "pending-branch");
  assert.equal(harness.api.getState().snapshot.branchName, "orders-candidate");
  assert.equal(harness.button("create").disabled, false);
  assert.doesNotMatch(harness.app.innerHTML, /unknown-proposal/);
  assert.equal(harness.pendingTimers.size, 0);
});

test("표준 MCP Apps bridge는 비밀 없는 읽기 전용 복구 좌표만 widget state로 영구 저장한다", async () => {
  const view = ontologyView();
  view.release.releaseEvidence.widgetConfirmationToken = "must-never-persist";
  const harness = createHarness(
    async () => ({}),
    view,
    undefined,
    { standardHostSelf: true },
  );

  await new Promise((resolve) => setImmediate(resolve));

  assert.deepEqual(harness.persistedWidgetStates, [{
    structuredContent: {
      schemaVersion: "foundry-lite-governed-release-recovery/v1",
      view: "proposal",
      releaseKind: "ontology",
      proposalId: "ont-proposal-7",
    },
  }]);
  assert.doesNotMatch(JSON.stringify(harness.persistedWidgetStates), /must-never-persist|widgetConfirmationToken/);
  assert.equal(harness.api.getState().recoveryPersistenceStatus, "succeeded");
});

test("저장된 workspace 좌표는 mutation 없이 exact open_release_workspace 한 번으로만 복구한다", async () => {
  const view = workspaceView();
  const harness = createHarness(
    async () => ({}),
    view,
    undefined,
    {
      standardHostSelf: true,
      manualTimers: true,
      aliasToolOutput: {},
      aliasWidgetState: {
        structuredContent: {
          schemaVersion: "foundry-lite-governed-release-recovery/v1",
          view: "workspace",
          releaseKind: "pipeline",
          branchName: "orders-candidate",
          pipelineId: "orders-pipeline",
        },
      },
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "open_release_workspace") {
          return { structuredContent: view };
        }
        return {};
      },
    },
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    assert.equal(harness.runNextTimer(), true);
  }
  await new Promise((resolve) => setImmediate(resolve));

  const toolCalls = harness.standardMessages.filter((message) => message.method === "tools/call");
  assert.deepEqual(toolCalls.map((message) => message.params.name), ["open_release_workspace"]);
  assert.deepEqual(toolCalls[0].params.arguments, {
    releaseKind: "pipeline",
    branchName: "orders-candidate",
    pipelineId: "orders-pipeline",
  });
  assert.equal(harness.api.getState().snapshot.status, "workspace_ready");
});

test("저장된 proposal 좌표는 mutation 없이 exact get_release_status 한 번으로만 복구한다", async () => {
  const deployed = pipelineView("deployed", [], true);
  const harness = createHarness(
    async () => ({}),
    deployed,
    undefined,
    {
      standardHostSelf: true,
      manualTimers: true,
      aliasToolOutput: {},
      aliasWidgetState: {
        structuredContent: {
          schemaVersion: "foundry-lite-governed-release-recovery/v1",
          view: "proposal",
          releaseKind: "pipeline",
          proposalId: "pipe-proposal-18",
        },
      },
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "get_release_status") {
          return { structuredContent: deployed };
        }
        return {};
      },
    },
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    assert.equal(harness.runNextTimer(), true);
  }
  await new Promise((resolve) => setImmediate(resolve));

  const toolCalls = harness.standardMessages.filter((message) => message.method === "tools/call");
  assert.deepEqual(toolCalls.map((message) => message.params.name), ["get_release_status"]);
  assert.deepEqual(toolCalls[0].params.arguments, {
    releaseKind: "pipeline",
    proposalId: "pipe-proposal-18",
  });
  assert.equal(harness.api.getState().snapshot.status, "deployed");
});

test("host globals와 복구 좌표가 모두 없어도 bounded wait 뒤 무한 skeleton 대신 명시적 안내를 표시한다", async () => {
  const harness = createHarness(
    async () => ({}),
    emptyInboxView(),
    undefined,
    { omitOpenAI: true, manualTimers: true },
  );

  assert.equal(harness.pendingTimers.size, 1);
  for (let attempt = 0; attempt < 60; attempt += 1) {
    assert.equal(harness.runNextTimer(), true);
  }
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(harness.api.getState().snapshot, null);
  assert.equal(harness.pendingTimers.size, 0);
  assert.match(harness.app.innerHTML, /이전 결과를 복구할 읽기 전용 좌표가 없습니다/);
  assert.doesNotMatch(harness.app.innerHTML, /aria-busy="true"/);
});

test("hosted 대화 재로드에서 과거 tool result가 없으면 동일 kind의 inbox만 읽어 empty snapshot을 복구한다", async () => {
  const harness = createHarness(
    async () => ({}),
    emptyInboxView(),
    undefined,
    {
      standardHostSelf: true,
      manualTimers: true,
      aliasToolOutput: {},
      aliasToolInput: { releaseKind: "ontology", limit: 10 },
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "list_release_inbox") {
          return { structuredContent: emptyInboxView() };
        }
        return {};
      },
    },
  );

  for (let attempt = 0; attempt < 60; attempt += 1) {
    assert.equal(harness.runNextTimer(), true);
  }
  await new Promise((resolve) => setImmediate(resolve));

  const toolCalls = harness.standardMessages.filter((message) => message.method === "tools/call");
  assert.deepEqual(toolCalls.map((message) => message.params.name), ["list_release_inbox"]);
  assert.deepEqual(toolCalls[0].params.arguments, { releaseKind: "ontology", limit: 10 });
  assert.equal(harness.api.getState().snapshot.status, "empty_inbox");
  assert.match(harness.app.innerHTML, /검토할 제안이 없습니다/);
  assert.ok(toolCalls.every((message) => !["prepare_release_action", "submit_release_decision"].includes(message.params.name)));
});

test("proposal 좌표가 있는 재로드 복구는 mutation 대신 exact get_release_status 한 번만 호출한다", async () => {
  const deployed = pipelineView("deployed", [], true);
  const harness = createHarness(
    async () => ({}),
    deployed,
    undefined,
    {
      standardHostSelf: true,
      manualTimers: true,
      aliasToolOutput: {},
      aliasToolInput: { releaseKind: "pipeline", proposalId: "pipe-proposal-18" },
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "get_release_status") {
          return { structuredContent: deployed };
        }
        return {};
      },
    },
  );

  await new Promise((resolve) => setImmediate(resolve));
  const recovered = await harness.api.recoverInitialSnapshotFromReadTool();
  assert.equal(recovered, true);

  const toolCalls = harness.standardMessages.filter((message) => message.method === "tools/call");
  assert.deepEqual(toolCalls.map((message) => message.params.name), ["get_release_status"]);
  assert.deepEqual(toolCalls[0].params.arguments, {
    releaseKind: "pipeline",
    proposalId: "pipe-proposal-18",
  });
  assert.equal(harness.api.getState().snapshot.status, "deployed");
});

test("초기 스냅샷 polling은 호스트 teardown에서 즉시 취소된다", () => {
  const harness = createHarness(
    async () => ({}),
    workspaceView(),
    undefined,
    {
      standardHost: true,
      manualTimers: true,
      aliasToolOutput: {},
    },
  );

  assert.equal(harness.pendingTimers.size, 1);
  harness.sendStandardMessage({ jsonrpc: "2.0", id: "host-initial-teardown", method: "ui/resource-teardown", params: {} });

  assert.equal(harness.api.getBridgeMode(), "unavailable");
  assert.equal(harness.pendingTimers.size, 0);
  assert.equal(harness.clearedTimers.length, 1);
  assert.ok(harness.standardMessages.some(
    (message) => message.id === "host-initial-teardown" && message.result && Object.keys(message.result).length === 0,
  ));
});

test("표준 bridge에서 mutation 응답 오류가 나도 같은 작업을 alias transport로 재전송하지 않는다", async () => {
  const aliasCalls = [];
  const harness = createHarness(
    async (name, args) => aliasCalls.push({ name, args }),
    workspaceView(),
    undefined,
    {
      standardHost: true,
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "prepare_release_action") {
          return { _meta: { widgetConfirmationToken: "standard-failure-token" } };
        }
        if (message.method === "tools/call" && message.params.name === "create_release_branch") {
          return { $jsonRpcError: { code: -32000, message: "standard host mutation response failed" } };
        }
        return {};
      },
    },
  );

  await harness.button("create").click();

  const mutationCalls = harness.standardMessages.filter(
    (message) => message.method === "tools/call" && message.params.name === "create_release_branch",
  );
  assert.equal(mutationCalls.length, 1, "an explicit standard-host error must not replay the mutation");
  assert.equal(aliasCalls.length, 0, "a standard mutation must never cross over to the alias transport");
  assert.match(harness.api.getState().message, /standard host mutation response failed/);
});

test("completionCoordinates는 서버의 exact 리허설 권한 4조건을 만족할 때만 버튼과 인자로 사용한다", async () => {
  const valid = pipelineView("deployed", []);
  valid.release.completionCoordinates = {
    attestationPurpose: "rollback_rehearsal",
    ontologyWorkflowRunId: "ontology-workflow-root-7",
    pipelineWorkflowRunId: "pipeline-workflow-root-9",
    isEligible: true,
    nextAction: "verify_release_completion",
    verificationMode: "<script>untrusted-mode</script>",
    kind: "untrusted-kind",
  };
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: "completion-token" } };
    }
    return { structuredContent: valid };
  }, valid);

  assert.equal(harness.button("verify").disabled, false);
  assert.match(harness.app.innerHTML, /서버가 제공한 두 워크플로 좌표만 사용 · 롤백 리허설/);
  assert.doesNotMatch(harness.app.innerHTML, /untrusted-mode|untrusted-kind/);
  await harness.button("verify").click();

  const prepared = calls.find((call) => call.name === "prepare_release_action");
  assert.deepEqual(Object.keys(prepared.args.arguments).sort(), [
    "idempotencyKey",
    "ontologyWorkflowRunId",
    "pipelineWorkflowRunId",
  ]);
  assert.equal(prepared.args.arguments.ontologyWorkflowRunId, "ontology-workflow-root-7");
  assert.equal(prepared.args.arguments.pipelineWorkflowRunId, "pipeline-workflow-root-9");
  const mutation = calls.find((call) => call.name === "verify_release_completion");
  assert.equal(mutation.args.widgetConfirmationToken, "completion-token");

  for (const override of [
    { isEligible: false },
    { nextAction: "rollback_release" },
    { attestationPurpose: "normal_operational_release" },
    { ontologyWorkflowRunId: "" },
  ]) {
    const invalid = pipelineView("deployed", []);
    invalid.release.completionCoordinates = { ...valid.release.completionCoordinates, ...override };
    const invalidHarness = createHarness(async () => ({}), invalid);
    assert.equal(invalidHarness.api.getState().snapshot.completionCoordinates, null);
    assert.equal(invalidHarness.button("verify"), undefined);
  }
});

test("초기 tool-result가 deploying이면 bounded status poll로 terminal 상태까지 자동 추적한다", async () => {
  const aliasCalls = [];
  const terminal = pipelineView("deployed", []);
  terminal.release.operationalCompletion = {
    completionPurpose: "normal_operational_release",
    isComplete: true,
    stage: "deployed",
    isRollbackRehearsal: false,
  };
  const harness = createHarness(
    async (name, args) => aliasCalls.push({ name, args }),
    pipelineView("merged"),
    undefined,
    {
      standardHost: true,
      omitAliasToolOutput: true,
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "get_release_status") {
          return { structuredContent: terminal };
        }
        return {};
      },
    },
  );

  harness.sendToolResult({ structuredContent: pipelineView("deploying", ["get_release_status"]) });
  await harness.api.waitForPolling();

  const polls = harness.standardMessages.filter(
    (message) => message.method === "tools/call" && message.params.name === "get_release_status",
  );
  assert.equal(polls.length, 1);
  assert.deepEqual(polls[0].params.arguments, { releaseKind: "pipeline", proposalId: "pipe-proposal-18" });
  assert.equal(harness.api.getState().snapshot.status, "deployed");
  assert.match(harness.app.innerHTML, /현재 릴리스 정상 운영 완료/);
  assert.match(harness.api.getState().message, /자동 확인했습니다/);
  assert.equal(aliasCalls.length, 0);
});

test("deploying 자동 확인은 terminal이 아니어도 정확히 3회에서 멈춘다", async () => {
  const harness = createHarness(
    async () => ({}),
    pipelineView("merged"),
    undefined,
    {
      standardHost: true,
      omitAliasToolOutput: true,
      standardHostHandler(message) {
        if (message.method === "tools/call" && message.params.name === "get_release_status") {
          return { structuredContent: pipelineView("deploying", ["get_release_status"]) };
        }
        return {};
      },
    },
  );

  harness.sendToolResult({ structuredContent: pipelineView("deploying", ["get_release_status"]) });
  await harness.api.waitForPolling();

  const polls = harness.standardMessages.filter(
    (message) => message.method === "tools/call" && message.params.name === "get_release_status",
  );
  assert.equal(polls.length, 3);
  assert.match(harness.api.getState().message, /3회 자동 확인했지만 아직 완료되지 않았습니다/);
});

test("ui/resource-teardown은 polling을 취소하고 timers를 정리한 뒤 빈 성공 응답을 보낸다", async () => {
  const harness = createHarness(
    async () => ({}),
    pipelineView("merged"),
    undefined,
    {
      standardHost: true,
      omitAliasToolOutput: true,
      manualTimers: true,
      standardHostHandler: () => ({}),
    },
  );

  harness.sendToolResult({ structuredContent: pipelineView("deploying", ["get_release_status"]) });
  harness.sendStandardMessage({ jsonrpc: "2.0", id: "host-teardown-1", method: "ui/resource-teardown", params: {} });
  harness.sendToolResult({ structuredContent: pipelineView("deploying", ["get_release_status"]) });
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(harness.api.getBridgeMode(), "unavailable");
  assert.equal(harness.pendingTimers.size, 0);
  assert.equal(harness.clearedTimers.length, 2);
  assert.ok(harness.standardMessages.some(
    (message) => message.id === "host-teardown-1" && message.result && Object.keys(message.result).length === 0,
  ));
  assert.equal(harness.standardMessages.filter(
    (message) => message.method === "tools/call" && message.params.name === "get_release_status",
  ).length, 0);
});

test("mutation이 전체 view를 반환하지 않으면 exact status schema로 자동 갱신한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") return { _meta: { widgetConfirmationToken: "execute-token" } };
    if (name === "execute_approved_release") return { structuredContent: { operationId: "op-7" } };
    return { structuredContent: ontologyView("active", ["rollback_release"]) };
  }, ontologyView("approved", ["execute_approved_release"]));

  await harness.api.invokeAction("execute");

  assert.deepEqual(calls.map((call) => call.name), [
    "prepare_release_action",
    "execute_approved_release",
    "get_release_status",
  ]);
  assert.deepEqual(Object.keys(calls[0].args.arguments).sort(), [
    "expectedFingerprint",
    "idempotencyKey",
    "proposalId",
    "releaseKind",
  ]);
  assert.deepEqual(calls[2].args, { releaseKind: "ontology", proposalId: "ont-proposal-7" });
  assert.equal(harness.api.getState().snapshot.status, "active");
});

test("동시에 두 번 누르면 한 번의 보호 절차만 진행한다", async () => {
  let releasePrepare;
  const waitForPrepare = new Promise((resolve) => {
    releasePrepare = resolve;
  });
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      await waitForPrepare;
      return { _meta: { widgetConfirmationToken: "single-flight-token" } };
    }
    return { structuredContent: ontologyView("approved", ["execute_approved_release"]) };
  });

  const first = harness.api.invokeAction("approve");
  const second = harness.api.invokeAction("approve");
  releasePrepare();
  await Promise.all([first, second]);

  assert.deepEqual(calls.map((call) => call.name), ["prepare_release_action", "submit_release_decision"]);
});

test("응답 유실 뒤에는 새 승인 없이 같은 one-time 요청을 exact replay로 복구한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: "response-loss-token" } };
    }
    if (calls.filter((call) => call.name === "submit_release_decision").length === 1) {
      throw new Error("bridge response lost");
    }
    return { structuredContent: ontologyView("approved", ["execute_approved_release"]) };
  });

  await harness.api.invokeAction("approve");

  assert.deepEqual(calls.map((call) => call.name), [
    "prepare_release_action",
    "submit_release_decision",
    "submit_release_decision",
  ]);
  const confirmed = calls[1].args;
  const recovered = calls[2].args;
  assert.equal(confirmed.widgetConfirmationToken, "response-loss-token");
  assert.equal(recovered.widgetConfirmationToken, "response-loss-token");
  assert.deepEqual(
    Object.fromEntries(Object.entries(confirmed).filter(([key]) => key !== "widgetConfirmationToken")),
    Object.fromEntries(Object.entries(recovered).filter(([key]) => key !== "widgetConfirmationToken")),
  );
  assert.equal(harness.api.getState().snapshot.status, "approved");
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /response-loss-token/);
});

test("준비 응답 유실 뒤에는 같은 exact prepare를 한 번 재호출해 새 token으로 계속한다", async () => {
  const calls = [];
  let prepareAttempts = 0;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      prepareAttempts += 1;
      if (prepareAttempts === 1) throw new Error("prepare response lost after commit");
      return { _meta: { widgetConfirmationToken: "rotated-prepare-token" } };
    }
    return { structuredContent: ontologyView("approved", ["execute_approved_release"]) };
  });

  await harness.api.invokeAction("approve");

  assert.deepEqual(calls.map((call) => call.name), [
    "prepare_release_action",
    "prepare_release_action",
    "submit_release_decision",
  ]);
  assert.deepEqual(calls[0].args, calls[1].args);
  assert.equal(calls[2].args.widgetConfirmationToken, "rotated-prepare-token");
  assert.equal(harness.api.getState().snapshot.status, "approved");
  assert.doesNotMatch(JSON.stringify(harness.api.getState()), /rotated-prepare-token/);
});

test("실행 중 복구 응답은 retryAfter 뒤 같은 tokenless 호출로 자동 재개한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: "crash-recovery-token" } };
    }
    const actionCalls = calls.filter((call) => call.name === "submit_release_decision");
    if (actionCalls.length === 1) throw new Error("server disconnected after mutation");
    if (actionCalls.length === 2) {
      return {
        isError: true,
        structuredContent: {
          error: {
            type: "CONFLICT",
            message: "release_run_in_progress",
            details: { reason: "release_run_in_progress", retryAfterSeconds: 1 },
          },
        },
        content: [{ type: "text", text: "release_run_in_progress" }],
      };
    }
    return { structuredContent: ontologyView("approved", ["execute_approved_release"]) };
  });

  await harness.api.invokeAction("approve");

  const actionCalls = calls.filter((call) => call.name === "submit_release_decision");
  assert.equal(actionCalls.length, 3);
  assert.equal(actionCalls[1].args.widgetConfirmationToken, "crash-recovery-token");
  assert.equal(Object.prototype.hasOwnProperty.call(actionCalls[2].args, "widgetConfirmationToken"), false);
  assert.deepEqual(
    Object.fromEntries(Object.entries(actionCalls[1].args).filter(([key]) => key !== "widgetConfirmationToken")),
    actionCalls[2].args,
  );
  assert.equal(harness.api.getState().snapshot.status, "approved");
});

test("검토자 미배정 후보는 위젯에서도 승인 호출을 시작하지 않는다", async () => {
  const calls = [];
  const unassigned = ontologyView();
  unassigned.release.candidate.assigneeUserId = null;
  unassigned.release.candidate.reviewPolicy = {
    requiresAssignment: true,
    requiresSeparateReviewer: false,
  };
  unassigned.release.candidate.canCurrentUserReview = false;
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    return {};
  }, unassigned);

  await harness.api.invokeAction("approve");

  assert.deepEqual(calls, []);
  assert.equal(harness.api.getState().snapshot.separateReviewerRequired, false);
  assert.equal(harness.api.getState().snapshot.reviewerAssigned, false);
});

test("서버의 중첩 CONFLICT 오류는 stale 차단 상태로 전환한다", async () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args: clone(args) });
    if (name === "prepare_release_action") {
      return { _meta: { widgetConfirmationToken: "conflict-token" } };
    }
    return {
      isError: true,
      structuredContent: {
        error: {
          type: "CONFLICT",
          message: "release head changed",
          details: { reason: "expected_head_mismatch" },
        },
      },
      content: [{ type: "text", text: "release head changed" }],
    };
  });

  await harness.api.invokeAction("approve");

  assert.equal(harness.api.getState().staleBlocked, true);
  assert.match(harness.api.getState().message, /새로고침/);
  assert.deepEqual(calls.map((call) => call.name), [
    "prepare_release_action",
    "submit_release_decision",
  ]);
});

test("후보 카드가 Diff·검증·영향·위험 분류를 증거 원장으로 렌더링한다", () => {
  const calls = [];
  const harness = createHarness(async (name, args) => {
    calls.push({ name, args });
    return {};
  }, evidenceRichView());
  const snapshot = harness.api.getState().snapshot;

  assert.equal(snapshot.changeDiff.counts.total, 2);
  assert.equal(snapshot.changeDiff.items[0].resourceId, "normalize-orders");
  assert.equal(snapshot.evidence[0].proofKind, "static_graph_output_contract");
  assert.equal(snapshot.impactScope.resources[1].resourceId, "orders.curated");
  assert.equal(snapshot.riskLevel, "high");
  assert.equal(snapshot.riskClassification.policyVersion, "foundry-lite-release-risk-v1");
  assert.match(harness.app.innerHTML, /<h2 class="section-label">변경 Diff<\/h2>/);
  assert.match(harness.app.innerHTML, /aria-label="변경 Diff 항목"/);
  assert.match(harness.app.innerHTML, /검증 및 테스트 증거/);
  assert.match(harness.app.innerHTML, /후보 범위 외부 CI 영수증/);
  assert.match(harness.app.innerHTML, /영향받는 운영 리소스/);
  assert.match(harness.app.innerHTML, /foundry-lite-release-risk-v1/);
  assert.match(harness.app.innerHTML, /미확보 증거: external_ci/);
  assert.deepEqual(calls, [], "증거 렌더링은 도구를 자동 호출하지 않아야 한다");
});

test("상태 카드가 실행 타임라인·실패 상세·감사 증거를 접근 가능한 구조로 렌더링한다", () => {
  const harness = createHarness(async () => ({}), evidenceRichView());
  const output = harness.app.innerHTML;

  assert.match(output, /<ol class="timeline-list" aria-label="릴리스 실행 타임라인">/);
  assert.match(output, /<time datetime="2026-08-09T04:03:00Z">/);
  assert.match(output, /role="alert" aria-label="릴리스 실패 상세"/);
  assert.match(output, /deployment_precondition_failed/);
  assert.match(output, /미커밋 확인됨/);
  assert.match(output, /<details class="audit-details">/);
  assert.match(output, /<summary>감사 증거 1건 보기<\/summary>/);
  assert.match(output, /aria-label="릴리스 감사 증거"/);
  assert.match(output, /req-deploy-7/);
});

test("증거 텍스트는 HTML을 escape하고 비밀·토큰 필드를 렌더링하지 않는다", () => {
  const view = evidenceRichView();
  const evidence = view.release.releaseEvidence;
  evidence.changeDiff.items[0].summary = '<img src=x onerror="diff-secret()">';
  evidence.changeDiff.widgetConfirmationToken = "diff-token-must-not-render";
  evidence.validationEvidence[0].details.widgetConfirmationToken = "validation-token-must-not-render";
  evidence.impactScope.resources[0].label = "<b>impact-secret</b>";
  evidence.riskClassification.reasons = ["<script>risk-secret()</script>"];
  evidence.executionTimeline[0].label = "<svg>timeline-secret</svg>";
  evidence.failureDetails.message = "<img src=x onerror=failure-secret()>";
  evidence.failureDetails.retryEvidence = { token: "failure-token-must-not-render" };
  evidence.auditEvidence[0].label = "<iframe>audit-secret</iframe>";
  evidence.auditEvidence[0].arguments = { confirmationToken: "audit-token-must-not-render" };
  const harness = createHarness(async () => ({}), view);
  const output = harness.app.innerHTML;

  assert.match(output, /&lt;img src=x onerror=&quot;diff-secret\(\)&quot;&gt;/);
  assert.match(output, /&lt;b&gt;impact-secret&lt;\/b&gt;/);
  assert.match(output, /&lt;script&gt;risk-secret\(\)&lt;\/script&gt;/);
  assert.match(output, /&lt;svg&gt;timeline-secret&lt;\/svg&gt;/);
  assert.match(output, /&lt;iframe&gt;audit-secret&lt;\/iframe&gt;/);
  assert.doesNotMatch(output, /<(?:img|script|svg|iframe|b)[\s>]/i);
  assert.doesNotMatch(
    output,
    /(?:diff|validation|failure|audit)-token-must-not-render/,
    "허용 목록 밖의 토큰·인자 필드는 화면에 나타나면 안 된다",
  );
});

test("산출물은 외부 네트워크와 브라우저 저장소 없이 접근 가능한 단일 HTML로 동작한다", () => {
  assert.ok(script, "inline script must exist");
  assert.doesNotMatch(html, /https?:\/\//i);
  assert.doesNotMatch(html, /localStorage|sessionStorage/);
  assert.doesNotMatch(script, /console\./);
  assert.match(html, /내부 브랜치/);
  assert.match(html, /Pipeline PROMOTED/);
  assert.match(html, /prefers-reduced-motion/);
  assert.match(html, /focus-visible/);
});
