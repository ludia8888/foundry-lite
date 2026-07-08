import type {
  OntologyBranchDiffResource,
  OntologyProposalPayload,
} from "@foundry-lite/sdk";
import type { FoundryLiteOntologyCatalogState } from "@foundry-lite/sdk/react";

import type { StatusIntent } from "@/components/shared/StatusPill";

/** 좌측 네비 섹션 id. */
export type OntologySection =
  | "overview"
  | "objectTypes"
  | "linkTypes"
  | "actionTypes"
  | "interfaces"
  | "functionTypes";

/** branch diff의 resource kind와 동일한 표기를 쓴다. */
export type OntologyResourceKind =
  "objectType" | "linkType" | "actionType" | "interface" | "functionType";

export const RESOURCE_KIND_LABELS: Record<OntologyResourceKind, string> = {
  objectType: "객체 타입",
  linkType: "링크 타입",
  actionType: "액션 타입",
  interface: "인터페이스",
  functionType: "함수",
};

export const SECTION_TO_KIND: Record<
  Exclude<OntologySection, "overview">,
  OntologyResourceKind
> = {
  objectTypes: "objectType",
  linkTypes: "linkType",
  actionTypes: "actionType",
  interfaces: "interface",
  functionTypes: "functionType",
};

/** 메인 테이블 한 행. */
export type OntologyResourceRow = {
  kind: OntologyResourceKind;
  apiName: string;
  displayName: string;
  subtitle: string | null;
  /** 선택된 브랜치에서 변경이 있으면 experimental. */
  status: "active" | "experimental";
  /** 분류(classification)/역할 제한이 걸려 있으면 restricted. */
  isRestricted: boolean;
};

export function ontologyResourceRowKey(row: {
  kind: OntologyResourceKind;
  apiName: string;
}): string {
  return `${row.kind}:${row.apiName}`;
}

export function buildDiffKeySet(
  resources: readonly OntologyBranchDiffResource[],
): ReadonlySet<string> {
  const keys = new Set<string>();
  for (const resource of resources) {
    if (resource.branchChange !== "unchanged") {
      keys.add(`${resource.kind}:${resource.apiName}`);
    }
  }
  return keys;
}

function rowStatus(
  changedKeys: ReadonlySet<string>,
  kind: OntologyResourceKind,
  apiName: string,
): "active" | "experimental" {
  return changedKeys.has(`${kind}:${apiName}`) ? "experimental" : "active";
}

function matchesSearch(search: string, ...values: (string | null)[]): boolean {
  if (search.length === 0) return true;
  const lowered = search.toLowerCase();
  return values.some((value) => value?.toLowerCase().includes(lowered));
}

/** 검색어 기준 텍스트 분할 조각 — isMatch 조각은 <mark>로 렌더한다. */
export type SearchTextSegment = { text: string; isMatch: boolean };

/** 대소문자 무시 부분 일치로 텍스트를 하이라이트 조각으로 나눈다. */
export function splitBySearchMatch(
  text: string,
  search: string,
): SearchTextSegment[] {
  if (search.length === 0 || text.length === 0) {
    return [{ text, isMatch: false }];
  }
  const lowered = text.toLowerCase();
  const query = search.toLowerCase();
  const segments: SearchTextSegment[] = [];
  let cursor = 0;
  while (cursor < text.length) {
    const found = lowered.indexOf(query, cursor);
    if (found === -1) {
      segments.push({ text: text.slice(cursor), isMatch: false });
      break;
    }
    if (found > cursor) {
      segments.push({ text: text.slice(cursor, found), isMatch: false });
    }
    segments.push({
      text: text.slice(found, found + query.length),
      isMatch: true,
    });
    cursor = found + query.length;
  }
  return segments;
}

/** 검색 시 사이드바 배지에 쓰는 카테고리별 매치 카운트. */
export type OntologySearchMatchCounts = Record<
  Exclude<OntologySection, "overview">,
  number
>;

/** 검색어가 비어 있으면 null, 아니면 전 카테고리 매치 카운트를 계산한다. */
export function buildSearchMatchCounts(
  workspace: FoundryLiteOntologyCatalogState,
  changedKeys: ReadonlySet<string>,
  search: string,
): OntologySearchMatchCounts | null {
  if (search.length === 0) return null;
  const sections = Object.keys(SECTION_TO_KIND) as Exclude<
    OntologySection,
    "overview"
  >[];
  return Object.fromEntries(
    sections.map((section) => [
      section,
      buildResourceRows(section, workspace, changedKeys, search).length,
    ]),
  ) as OntologySearchMatchCounts;
}

/** 섹션별로 catalog 워크스페이스를 테이블 행으로 변환한다. */
export function buildResourceRows(
  section: Exclude<OntologySection, "overview">,
  workspace: FoundryLiteOntologyCatalogState,
  changedKeys: ReadonlySet<string>,
  search: string,
): OntologyResourceRow[] {
  const catalog = workspace.catalog;
  if (!catalog) return [];
  if (section === "objectTypes") {
    return workspace.objectViews
      .filter((view) => matchesSearch(search, view.apiName, view.displayName))
      .map((view) => ({
        kind: "objectType" as const,
        apiName: view.apiName,
        displayName: view.displayName,
        subtitle: `속성 ${view.propertyCount} · 링크 ${view.linkCount} · 액션 ${view.actionCount}`,
        status: rowStatus(changedKeys, "objectType", view.apiName),
        isRestricted: view.classifiedProperties.length > 0,
      }));
  }
  if (section === "linkTypes") {
    return catalog.linkTypes
      .filter((link) => matchesSearch(search, link.apiName, link.displayName))
      .map((link) => ({
        kind: "linkType" as const,
        apiName: link.apiName,
        displayName: link.displayName,
        subtitle: `${link.fromObjectType} → ${link.toObjectType} (${link.cardinality})`,
        status: rowStatus(changedKeys, "linkType", link.apiName),
        isRestricted: false,
      }));
  }
  if (section === "actionTypes") {
    return workspace.actionViews
      .filter((view) => matchesSearch(search, view.apiName, view.displayName))
      .map((view) => ({
        kind: "actionType" as const,
        apiName: view.apiName,
        displayName: view.displayName,
        subtitle: `대상 ${view.targetObjectApiName} · 파라미터 ${view.parameterCount}`,
        status: rowStatus(changedKeys, "actionType", view.apiName),
        isRestricted: !view.isEnabled,
      }));
  }
  if (section === "interfaces") {
    return (catalog.interfaces ?? [])
      .filter((item) => matchesSearch(search, item.apiName, item.displayName))
      .map((item) => ({
        kind: "interface" as const,
        apiName: item.apiName,
        displayName: item.displayName,
        subtitle: `구현 객체 ${item.implementedBy.length}개`,
        status: rowStatus(changedKeys, "interface", item.apiName),
        isRestricted: false,
      }));
  }
  return (catalog.functionTypes ?? [])
    .filter((item) => matchesSearch(search, item.apiName, item.displayName))
    .map((item) => ({
      kind: "functionType" as const,
      apiName: item.apiName,
      displayName: item.displayName,
      subtitle: `런타임 ${item.runtime}`,
      status: rowStatus(changedKeys, "functionType", item.apiName),
      isRestricted: item.allowedRoles !== null,
    }));
}

/** 제안 상태 라벨 — backend PROPOSAL_STATUSES 기준. */
export const PROPOSAL_STATUS_LABELS: Record<string, string> = {
  submitted: "제출됨",
  in_review: "리뷰 중",
  approved: "승인됨",
  applied: "적용됨",
  rejected: "반려됨",
  withdrawn: "철회됨",
};

export const PROPOSAL_STATUS_INTENTS: Record<string, StatusIntent> = {
  submitted: "info",
  in_review: "warning",
  approved: "success",
  applied: "success",
  rejected: "danger",
  withdrawn: "neutral",
};

export function proposalStatusLabel(status: string): string {
  return PROPOSAL_STATUS_LABELS[status] ?? status;
}

export function proposalStatusIntent(status: string): StatusIntent {
  return PROPOSAL_STATUS_INTENTS[status] ?? "neutral";
}

export const BRANCH_STATUS_LABELS: Record<string, string> = {
  open: "작업 중",
  merged: "머지됨",
  abandoned: "폐기됨",
};

export const DIFF_CHANGE_LABELS: Record<string, string> = {
  added: "추가",
  modified: "수정",
  removed: "삭제",
  unchanged: "없음",
};

export const DIFF_CHANGE_INTENTS: Record<string, StatusIntent> = {
  added: "success",
  modified: "warning",
  removed: "danger",
  unchanged: "neutral",
};

export type ProposalStepState = "done" | "active" | "pending";

export type ProposalStepView = {
  id: "preparing" | "in_review" | "merge";
  label: string;
  description: string | null;
  state: ProposalStepState;
};

/**
 * 제안 상태를 번호 스테퍼(준비 → 리뷰 중 → 머지) 뷰로 변환한다.
 * rejected/withdrawn은 스테퍼 밖 배지로 따로 표시한다.
 */
export function buildProposalSteps(
  proposal: OntologyProposalPayload,
): ProposalStepView[] {
  const isApplied = proposal.status === "applied";
  const isApproved = proposal.status === "approved";
  const isInReview = proposal.status === "in_review";
  const isSubmitted = proposal.status === "submitted";
  const approvedCount = isApproved || isApplied ? 1 : 0;
  return [
    {
      id: "preparing",
      label: "준비",
      description: isSubmitted ? "리뷰어 지정 대기" : null,
      state: isSubmitted ? "active" : "done",
    },
    {
      id: "in_review",
      label: "리뷰 중",
      description: `${approvedCount} / 1 작업 승인됨`,
      state: isInReview ? "active" : isSubmitted ? "pending" : "done",
    },
    {
      id: "merge",
      label: "제안 머지",
      description: isApplied
        ? "새 온톨로지 버전으로 적용됨"
        : isApproved
          ? "실행 대기"
          : null,
      state: isApplied ? "done" : isApproved ? "active" : "pending",
    },
  ];
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function truncateMiddle(value: string, max = 24): string {
  if (value.length <= max) return value;
  const half = Math.floor((max - 1) / 2);
  return `${value.slice(0, half)}…${value.slice(-half)}`;
}
