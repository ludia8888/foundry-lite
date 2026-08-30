import type { GenericObject } from "@foundry-lite/sdk";
import type { FoundryLiteOntologyObjectView } from "@foundry-lite/sdk/react";
import type { FoundryLiteOntologyActionView } from "@foundry-lite/sdk/react";

import type { StatusIntent } from "@/components/shared/StatusPill";

import type { AppPresentation } from "./app-model";

const IDENTIFIER_WORDS: Record<string, string> = {
  ACTIVE: "진행 중",
  APPROVED: "승인 완료",
  BLOCKED: "진행 차단",
  CANCELLED: "취소됨",
  CLOSED: "종료",
  COLLECTING: "자료 수집 중",
  COMPLETED: "완료",
  CONFIRMED: "확정",
  CONSENT: "동의",
  CONTRACTED: "계약 완료",
  DRAFTED: "초안 완료",
  FILED: "제출 완료",
  HELD: "임시 확보",
  NEW: "신규",
  OFFERED: "제안 완료",
  PENDING: "확인 대기",
  QUALIFIED: "확인 완료",
  READY: "준비 완료",
  RECONCILING: "대사 중",
  REJECTED: "반려",
  REPORTED: "접수됨",
  REQUESTED: "요청됨",
  REVIEW: "검토 중",
  SCHEDULED: "일정 확정",
  SEATED: "이용 중",
  TRIAGED: "분류 완료",
  CARE: "진료",
  CONTRACT: "계약",
  CUSTOMER: "고객",
  INQUIRY: "문의",
  ORDER: "주문",
  PATIENT: "환자",
  REQUEST: "요청",
  RESERVATION: "예약",
  VISIT: "방문",
  WORKSHOP: "",
  AMOUNT: "금액",
  ASSIGNEE: "담당자",
  CUSTOMER_ID: "고객 번호",
  DUE_DATE: "기한",
  ID: "번호",
  MARGIN: "마진",
  NOTE: "메모",
  OPERATOR_NOTE: "담당자 메모",
  ORDER_ID: "주문 번호",
  PRIORITY: "우선순위",
  RISK_SCORE: "위험 점수",
  STATUS: "상태",
  CREATED: "등록",
  DATE: "날짜",
  DUE: "기한",
  EMAIL: "이메일",
  END: "종료",
  NAME: "이름",
  OPERATOR: "담당자",
  PHONE: "전화번호",
  RISK: "위험",
  SCORE: "점수",
  START: "시작",
  TITLE: "제목",
  UPDATED: "수정",
};

export type BusinessStatus = {
  label: string;
  intent: StatusIntent;
  description?: string;
};

export function businessStatus(
  value: unknown,
  presentation?: AppPresentation,
): BusinessStatus {
  const key = String(value ?? "");
  const configured = presentation?.statusLabels[key];
  if (configured) return configured;
  return { label: humanizeIdentifier(key) || "상태 미지정", intent: inferStatusIntent(key) };
}

export function businessObjectTypeName(
  apiName: string,
  objectView: FoundryLiteOntologyObjectView | null,
  presentation?: AppPresentation,
): string {
  return (
    presentation?.objectTypeNames[apiName] ??
    friendlyViewName(apiName, objectView?.displayName) ??
    humanizeIdentifier(apiName) ??
    "업무 기록"
  );
}

export function businessPropertyName(
  objectApiName: string,
  propertyApiName: string,
  objectView: FoundryLiteOntologyObjectView | null,
  presentation?: AppPresentation,
): string {
  return (
    presentation?.propertyNames[`${objectApiName}.${propertyApiName}`] ??
    presentation?.propertyNames[propertyApiName] ??
    friendlyViewName(
      propertyApiName,
      objectView?.properties.find((property) => property.apiName === propertyApiName)?.displayName,
    ) ??
    humanizeIdentifier(propertyApiName) ??
    "정보"
  );
}

export function businessActionName(
  apiName: string,
  actionView: FoundryLiteOntologyActionView | null,
  presentation?: AppPresentation,
): string {
  return (
    presentation?.actionNames[apiName] ??
    friendlyViewName(apiName, actionView?.displayName) ??
    humanizeIdentifier(apiName) ??
    "업무 실행"
  );
}

export function businessValue(
  value: unknown,
  propertyApiName?: string,
  dataType?: string,
  presentation?: AppPresentation,
): string {
  if (value === null || value === undefined || value === "") {
    return presentation?.booleanLabels.emptyLabel ?? "정보 없음";
  }
  if (typeof value === "boolean") {
    return value
      ? presentation?.booleanLabels.trueLabel ?? "예"
      : presentation?.booleanLabels.falseLabel ?? "아니요";
  }
  if (typeof value === "number") return value.toLocaleString(presentation?.locale ?? "ko-KR");
  if (Array.isArray(value)) return `${value.length.toLocaleString("ko-KR")}개 항목`;
  if (typeof value === "object") return "상세 정보 있음";
  if (isDateValue(value, propertyApiName, dataType)) {
    const parsed = new Date(String(value));
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleString(presentation?.locale ?? "ko-KR", {
        year: "numeric",
        month: "short",
        day: "numeric",
        ...(hasTime(String(value)) ? { hour: "2-digit", minute: "2-digit" } : {}),
      });
    }
  }
  return String(value);
}

export function businessObjectTitle(
  object: GenericObject,
  objectView: FoundryLiteOntologyObjectView | null,
  presentation?: AppPresentation,
): string {
  const candidates = ["title", "name", "displayName", "subject", "customerName", "patientName"];
  for (const key of candidates) {
    const value = object.properties[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  const primaryKey = objectView?.primaryKeyProperty;
  const primaryValue = primaryKey ? object.properties[primaryKey] : null;
  const typeName = businessObjectTypeName(object.objectType, objectView, presentation);
  if (typeof primaryValue === "string" || typeof primaryValue === "number") {
    return `${typeName} ${maskIdentifier(String(primaryValue))}`;
  }
  return typeName;
}

export function humanizeIdentifier(value: string): string {
  const normalized = value
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[\s-]+/g, "_")
    .replace(/^_+|_+$/g, "");
  if (!normalized) return "";
  const tokens = normalized.split("_");
  const translated = tokens
    .map((token) => IDENTIFIER_WORDS[token.toUpperCase()] ?? token.toLowerCase())
    .filter(Boolean);
  if (translated.every((token) => /^[a-z0-9]+$/.test(token))) {
    return translated.join(" ").replace(/^./, (character) => character.toUpperCase());
  }
  return translated.join(" ");
}

export function isTechnicalIdentifierProperty(
  apiName: string,
  isPrimaryKey = false,
): boolean {
  return isPrimaryKey || /(^id$|_id$|Id$)/.test(apiName);
}

function friendlyViewName(apiName: string, displayName?: string): string | null {
  if (!displayName) return null;
  if (displayName === apiName || /^[A-Za-z][A-Za-z0-9 _-]*$/.test(displayName)) {
    return humanizeIdentifier(displayName);
  }
  return displayName;
}

function inferStatusIntent(value: string): StatusIntent {
  const normalized = value.toUpperCase();
  if (/APPROVED|ACTIVE|CONFIRMED|COMPLETED|CLOSED|FILED|SUCCESS/.test(normalized)) return "success";
  if (/PENDING|REVIEW|WAIT|READY|SCHEDULED|DRAFT/.test(normalized)) return "warning";
  if (/REJECT|BLOCK|ERROR|FAIL|CANCEL|DENIED/.test(normalized)) return "danger";
  if (/NEW|REPORTED|REQUESTED|OPEN|COLLECTING/.test(normalized)) return "info";
  return "neutral";
}

function maskIdentifier(value: string): string {
  if (value.length <= 4) return value;
  return `#${value.slice(-4)}`;
}

function isDateValue(value: unknown, propertyApiName?: string, dataType?: string): boolean {
  if (typeof value !== "string") return false;
  return /date|time|timestamp/i.test(dataType ?? "") || /date|time|at$|due|scheduled/i.test(propertyApiName ?? "");
}

function hasTime(value: string): boolean {
  return /T\d{2}:\d{2}|\d{2}:\d{2}/.test(value);
}
