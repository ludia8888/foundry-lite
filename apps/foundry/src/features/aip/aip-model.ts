import type {
  AipAgentRunRequest,
  AipBuilderRunRequest,
  AipBuilderValidateRequest,
  AipEvalRunRequest,
} from "@foundry-lite/sdk";

/** 백엔드 visual_builder.py의 canonical 값 (source-of-truth 실측). */
export const RELEASE_CHANNELS = [
  "draft",
  "dev",
  "canary",
  "stable",
  "sunset",
] as const;

/** validationStatus / runStatus를 StatusPill intent로 매핑. */
export type StatusTone = "success" | "warning" | "danger" | "info" | "neutral";

export function phaseTone(phase: string | null | undefined): StatusTone {
  switch (String(phase ?? "").toLowerCase()) {
    case "succeeded":
    case "success":
    case "passed":
    case "ready":
    case "active":
      return "success";
    case "running":
    case "queued":
    case "requested":
    case "started":
      return "info";
    case "blocked":
    case "warning":
      return "warning";
    case "failed":
    case "error":
    case "rejected":
      return "danger";
    default:
      return "neutral";
  }
}

/** sha256:... 해시를 앞 16 / 뒤 8만 노출. */
export function shortHash(value: unknown): string {
  if (typeof value !== "string" || value.length <= 24) {
    return typeof value === "string" && value.length > 0 ? value : "-";
  }
  return `${value.slice(0, 16)}…${value.slice(-8)}`;
}

export function asText(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value.length > 0 ? value : "-";
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return "-";
}

/** citations는 Array<Record<string, unknown>> — 안전 접근용 뷰. */
export type AgentCitationView = {
  order: number;
  contextId: string | null;
  sourceResourceId: string | null;
  displayLabel: string | null;
  contentHash: string | null;
  renderedRef: string | null;
  claimSpan: { start: number; end: number } | null;
  source: {
    kind: string | null;
    retrievalMethod: string | null;
    sourceVersion: string | null;
    tokenEstimate: unknown;
    securityPartition: string | null;
    selected: boolean;
    contextItemId: string | null;
    contentHash: string | null;
  } | null;
};

function readString(
  record: Record<string, unknown>,
  key: string,
): string | null {
  const value = record[key];
  return typeof value === "string" && value.length > 0 ? value : null;
}

function readClaimSpan(
  record: Record<string, unknown>,
): { start: number; end: number } | null {
  const span = record.claimSpan;
  if (span === null || typeof span !== "object" || Array.isArray(span)) {
    return null;
  }
  const start = (span as Record<string, unknown>).start;
  const end = (span as Record<string, unknown>).end;
  if (
    typeof start === "number" &&
    typeof end === "number" &&
    Number.isInteger(start) &&
    Number.isInteger(end) &&
    start >= 0 &&
    end > start
  ) {
    return { start, end };
  }
  return null;
}

function readSourcePreview(
  record: Record<string, unknown>,
): AgentCitationView["source"] {
  const preview = record.sourcePreview;
  if (
    preview === null ||
    typeof preview !== "object" ||
    Array.isArray(preview)
  ) {
    return null;
  }
  const source = preview as Record<string, unknown>;
  return {
    kind: readString(source, "kind"),
    retrievalMethod: readString(source, "retrievalMethod"),
    sourceVersion: readString(source, "sourceVersion"),
    tokenEstimate: source.tokenEstimate ?? null,
    securityPartition: readString(source, "securityPartition"),
    selected: source.selected === true,
    contextItemId: readString(source, "contextItemId"),
    contentHash: readString(source, "contentHash"),
  };
}

export function toCitationViews(
  citations: ReadonlyArray<Record<string, unknown>>,
): AgentCitationView[] {
  return citations.map((citation, index) => {
    const rawOrder = citation.citationOrder;
    const order =
      typeof rawOrder === "number" && Number.isInteger(rawOrder) && rawOrder > 0
        ? rawOrder
        : index + 1;
    return {
      order,
      contextId: readString(citation, "contextId"),
      sourceResourceId: readString(citation, "sourceResourceId"),
      displayLabel: readString(citation, "displayLabel"),
      contentHash: readString(citation, "contentHash"),
      renderedRef: readString(citation, "renderedRef"),
      claimSpan: readClaimSpan(citation),
      source: readSourcePreview(citation),
    };
  });
}

/** 답변 텍스트를 claimSpan 기준으로 분할해 inline anchor를 삽입 (legacy 렌더 로직 이식). */
export type AnswerSegment =
  | { kind: "text"; text: string }
  | { kind: "anchor"; text: string; order: number };

export function toAnswerSegments(
  answer: string,
  citations: readonly AgentCitationView[],
): AnswerSegment[] {
  let cursor = 0;
  const anchors = citations
    .filter(
      (
        citation,
      ): citation is AgentCitationView & {
        claimSpan: { start: number; end: number };
      } => citation.claimSpan !== null,
    )
    .map((citation) => ({
      start: citation.claimSpan.start,
      end: citation.claimSpan.end,
      order: citation.order,
    }))
    .filter((anchor) => anchor.end <= answer.length)
    .sort(
      (left, right) =>
        left.start - right.start ||
        left.end - right.end ||
        left.order - right.order,
    )
    .filter((anchor) => {
      if (anchor.start < cursor) return false;
      cursor = anchor.end;
      return true;
    });

  if (anchors.length === 0) {
    return [{ kind: "text", text: answer }];
  }

  const segments: AnswerSegment[] = [];
  let position = 0;
  for (const anchor of anchors) {
    if (anchor.start > position) {
      segments.push({
        kind: "text",
        text: answer.slice(position, anchor.start),
      });
    }
    segments.push({
      kind: "anchor",
      text: answer.slice(anchor.start, anchor.end),
      order: anchor.order,
    });
    position = anchor.end;
  }
  if (position < answer.length) {
    segments.push({ kind: "text", text: answer.slice(position) });
  }
  return segments;
}

/** JSON 편집기 파싱 결과. */
export type JsonParseResult<T> =
  { ok: true; value: T } | { ok: false; error: string };

export function parseJsonObject<T = Record<string, unknown>>(
  text: string,
): JsonParseResult<T> {
  try {
    const value = JSON.parse(text) as unknown;
    if (value === null || typeof value !== "object" || Array.isArray(value)) {
      return { ok: false, error: "JSON 객체({ ... }) 형식이어야 합니다." };
    }
    return { ok: true, value: value as T };
  } catch (caught) {
    const message = caught instanceof Error ? caught.message : String(caught);
    return { ok: false, error: `JSON 파싱 실패: ${message}` };
  }
}

/**
 * 에이전트 실행 콘솔의 기본 페이로드 (supply-chain 시드 기준).
 * 실서버 모델 secret 미구성 시 실패 evidence 경로를 그대로 노출한다.
 */
export function defaultAgentPayload(agentRunId: string): AipAgentRunRequest {
  return {
    agentRunId,
    agentVersionId: "agent.order-ops.v1",
    modelAlias: "default-completion",
    promptVersionId: "prompt-order-copilot@v1",
    userMessage: "Explain Order O-1001 for the operator.",
    agentInstruction:
      "Answer as the Order Operations Copilot. Do not execute tools.",
    securityPartition: "tenant-demo:internal",
    allowedSecurityPartitions: ["tenant-demo:internal"],
    stateJson: { objectType: "Order", objectId: "O-1001" },
    outputSchema: { type: "object" },
    dataClassification: "internal",
    maxContextItems: 4,
    maxContextTokens: 1200,
    maxModelCalls: 1,
    maxLoopIterations: 1,
    maxOutputTokens: 512,
    policyVersion: "policy-v1",
  };
}

/** 빌더 그래프 기본 정의 — 실측한 canonical block/tool/axis로 validate=ready 되는 세트. */
export const DEFAULT_BUILDER_GRAPH_JSON = `{
  "agentVersionId": "agent.order-ops.v1",
  "releaseChannel": "dev",
  "modelAliasVersion": "default-completion@v1",
  "promptVersionId": "prompt-order-copilot@v1",
  "contextSources": [
    {
      "sourceId": "order-index",
      "kind": "ontology",
      "securityPartition": "tenant-demo:internal",
      "selectedProperties": ["orderId", "status"],
      "tokenBudget": 800
    }
  ],
  "toolManifest": [
    {
      "toolId": "ontology.get_object",
      "version": "2026-06-25",
      "effect": "READ",
      "requiredPermission": "object:read",
      "confirmationPolicy": "NONE",
      "objectTypeAllowlist": ["Order"],
      "status": "published"
    }
  ],
  "logicBlocks": [
    { "blockId": "b1", "kind": "Input" },
    {
      "blockId": "b2",
      "kind": "CallFunction",
      "inputs": {
        "toolId": "ontology.get_object",
        "version": "2026-06-25",
        "toolCallId": "builder-order-read",
        "arguments": {
          "object_type": "Order",
          "object_id": "O-1001",
          "property_names": ["orderId", "status"]
        },
        "occurredAt": "2026-06-25T00:00:00Z"
      },
      "dependsOn": ["b1"]
    },
    {
      "blockId": "b3",
      "kind": "Output",
      "inputs": { "fromBlock": "b2" },
      "dependsOn": ["b2"]
    }
  ],
  "evalAxes": ["retrieval", "answer"],
  "agentAllowedActions": ["ApproveOrder"],
  "maxLogicBlocks": 8
}`;

/** 검증 실패 케이스를 즉시 재현할 수 있는 초안 (비-canonical 값). */
export const INVALID_BUILDER_GRAPH_JSON = `{
  "agentVersionId": "agent.order-ops.v1",
  "releaseChannel": "candidate",
  "modelAliasVersion": "default-completion@v1",
  "promptVersionId": "prompt-order-copilot@v1",
  "contextSources": [],
  "toolManifest": [],
  "logicBlocks": [
    { "blockId": "b1", "kind": "retrieve" },
    { "blockId": "b2", "kind": "answer", "dependsOn": ["b1"] }
  ],
  "evalAxes": ["faithfulness", "safety"],
  "agentAllowedActions": [],
  "maxLogicBlocks": 8
}`;

export function buildBuilderRunRequest(
  validatePayload: AipBuilderValidateRequest,
  logicRunId: string,
): AipBuilderRunRequest {
  return {
    ...validatePayload,
    logicRunId,
    inputJson: { objectType: "Order", objectId: "O-1001" },
    agentAllowedTools: ["ontology.get_object"],
  };
}

/**
 * 평가 스위트 기본 페이로드 (실측: sample_index는 1-base 양수 필수).
 * candidateReleaseChannel은 릴리스 대상 채널과 반드시 일치해야 승격이 가능하다.
 * agentVersionId는 (agent_version, channel) 단위로 릴리스가 고정되므로,
 * 평가 대상 후보 버전을 인자로 받아 승격 충돌을 candidate 단위로 격리한다.
 */
export function defaultEvalRequest(
  evalRunId: string,
  candidateReleaseChannel: string,
  agentVersionId: string,
): AipEvalRunRequest {
  return {
    evalRunId,
    suiteApiName: "order-copilot-suite",
    suiteVersion: "v1",
    suiteDescription: "Order copilot regression",
    agentVersionId,
    candidateReleaseChannel,
    cases: [
      {
        caseApiName: "case-answer-1",
        axis: "answer",
        inputJson: { q: "status?" },
        expectedJson: { a: "delayed" },
        actualJson: { a: "delayed" },
        rubricJson: { match: "exact" },
        sampleIndex: 1,
        weight: 1,
      },
      {
        caseApiName: "case-retrieval-1",
        axis: "retrieval",
        inputJson: { q: "context" },
        expectedJson: { n: 1 },
        actualJson: { n: 1 },
        rubricJson: { match: "exact" },
        sampleIndex: 1,
        weight: 1,
      },
    ],
    minScore: 0.7,
    requiredAxes: ["answer", "retrieval"],
  };
}
