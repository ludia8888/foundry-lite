import type {
  OntologyProposalPayload,
  PipelineProposal,
} from "@foundry-lite/sdk";

export type DiffChangeKind = "added" | "modified" | "removed" | "unchanged";

export type ResourceChange = {
  apiName: string;
  kind: string;
  change: DiffChangeKind;
};

export type OntologyDiffModel = {
  changes: ResourceChange[];
  branchId: string | null;
  branchName: string | null;
  migrationStatus: string | null;
  consumerCompatibility: string | null;
  sdkCompatibility: string | null;
  objectTypeCount: number | null;
  linkTypeCount: number | null;
  actionTypeCount: number | null;
  yamlText: string | null;
  hasBlockedChanges: boolean;
};

/**
 * 제안 description에 `[ontology-branch-diff] {json}` 형태로 임베드된 브랜치 diff.
 * 백엔드 시드는 migration plan의 changes를 비우고 이 마커에 리소스 변경을 담는다.
 */
const BRANCH_DIFF_MARKER = "[ontology-branch-diff]";

export type OntologyBranchDiff = {
  branchId: string | null;
  branchName: string | null;
  resources: ResourceChange[];
};

/** description에서 브랜치 diff 마커를 파싱한다. 없으면 null. */
export function parseBranchDiff(
  description: string | null,
): OntologyBranchDiff | null {
  if (!description) return null;
  const markerIndex = description.indexOf(BRANCH_DIFF_MARKER);
  if (markerIndex < 0) return null;
  const jsonStart = description.indexOf("{", markerIndex);
  if (jsonStart < 0) return null;
  try {
    const parsed = JSON.parse(description.slice(jsonStart));
    const record = asRecord(parsed);
    if (!record) return null;
    return {
      branchId: asString(record.branchId),
      branchName: asString(record.branchName),
      resources: parseChanges(record.resources),
    };
  } catch {
    return null;
  }
}

/** description에서 브랜치 diff 마커 이하를 제거한 사람이 읽을 본문만 남긴다. */
export function stripBranchDiffMarker(description: string | null): string {
  if (!description) return "";
  const markerIndex = description.indexOf(BRANCH_DIFF_MARKER);
  const body =
    markerIndex < 0 ? description : description.slice(0, markerIndex);
  return body.trim();
}

function asString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" ? value : null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function normalizeChangeKind(raw: unknown): DiffChangeKind {
  const value = String(raw ?? "").toLowerCase();
  if (value === "added" || value === "create" || value === "new")
    return "added";
  if (value === "removed" || value === "delete" || value === "deleted") {
    return "removed";
  }
  if (value === "modified" || value === "update" || value === "updated") {
    return "modified";
  }
  return "unchanged";
}

/** migrationPlan.changes 배열을 정규화한다. 형태가 다양하므로 방어적으로 파싱. */
function parseChanges(raw: unknown): ResourceChange[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((entry) => {
      const record = asRecord(entry);
      if (!record) return null;
      const apiName =
        asString(record.apiName) ??
        asString(record.name) ??
        asString(record.id);
      if (!apiName) return null;
      return {
        apiName,
        kind:
          asString(record.kind) ??
          asString(record.resourceKind) ??
          asString(record.resourceType) ??
          "resource",
        change: normalizeChangeKind(record.change ?? record.changeType),
      };
    })
    .filter((entry): entry is ResourceChange => entry !== null);
}

export function ontologyDiffModel(
  proposal: OntologyProposalPayload,
): OntologyDiffModel {
  const plan = asRecord(proposal.submittedMigrationPlan);
  const validation = asRecord(proposal.validation);
  // migration plan의 changes가 비어 있으면 description의 브랜치 diff 마커에서 보충한다.
  const planChanges = parseChanges(plan?.changes);
  const branchDiff = parseBranchDiff(proposal.description);
  const changes =
    planChanges.length > 0 ? planChanges : (branchDiff?.resources ?? []);
  return {
    changes,
    branchId: branchDiff?.branchId ?? null,
    branchName: branchDiff?.branchName ?? null,
    migrationStatus: asString(plan?.status),
    consumerCompatibility: asString(plan?.consumerCompatibility),
    sdkCompatibility: asString(plan?.sdkCompatibility),
    objectTypeCount: asNumber(validation?.objectTypeCount),
    linkTypeCount: asNumber(validation?.linkTypeCount),
    actionTypeCount: asNumber(validation?.actionTypeCount),
    yamlText: asString(proposal.yamlText),
    hasBlockedChanges: proposal.hasBlockedChangesAtSubmit,
  };
}

export type PipelineNodeSummary = {
  id: string;
  type: string;
  label: string;
  outputRef: string | null;
};

export type PipelineDiffModel = {
  nodes: PipelineNodeSummary[];
  edgeCount: number;
  graphFingerprint: string | null;
  decision: string | null;
  decisionComment: string | null;
  decidedAt: string | null;
};

export function pipelineDiffModel(
  proposal: PipelineProposal,
): PipelineDiffModel {
  const record = proposal as Record<string, unknown>;
  const graph = asRecord(record.graph);
  const rawNodes = Array.isArray(graph?.nodes) ? graph?.nodes : [];
  const rawEdges = Array.isArray(graph?.edges) ? graph?.edges : [];
  const nodes: PipelineNodeSummary[] = rawNodes.map((raw) => {
    const node = asRecord(raw) ?? {};
    const data = asRecord(node.data) ?? {};
    return {
      id: asString(node.id) ?? "-",
      type: asString(node.type) ?? "node",
      label:
        asString(data.label) ??
        asString(data.datasetRef) ??
        asString(node.id) ??
        "-",
      outputRef: asString(data.outputDatasetRef) ?? asString(data.datasetRef),
    };
  });
  return {
    nodes,
    edgeCount: rawEdges.length,
    graphFingerprint: asString(record.graphFingerprint),
    decision: asString(record.decision),
    decisionComment: asString(record.decisionComment),
    decidedAt: asString(record.decidedAt),
  };
}
