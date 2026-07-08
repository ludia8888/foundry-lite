import type { SourceTemplate } from "@foundry-lite/sdk";
import type { LucideIcon } from "lucide-react";
import {
  Cable,
  Database,
  FileUp,
  Image,
  ServerCog,
  Share2,
  Waypoints,
  Webhook,
} from "lucide-react";

import type { ScreenDef } from "@/lib/screens";

/**
 * Marketplace 카탈로그 모델.
 *
 * Foundry-lite marketplace는 reference_only다 (template registry / solution
 * package install / billing은 future). 그래서 카드는 fabricate하지 않고 실존
 * 리소스로만 채운다:
 *  - 데이터 연결 제품: sources.templates.list() (실 SDK 호출)
 *  - 플랫폼 앱: 화면 레지스트리의 구현된 앱들
 */

export type MarketplaceCategory = "all" | "data-connection" | "platform-app";

export type MarketplaceProductKind = "data-connection" | "platform-app";

export interface MarketplaceProduct {
  /** 카드 라우팅용 안정 id. */
  id: string;
  kind: MarketplaceProductKind;
  name: string;
  /** 카드 부제 (예: sourceType api name, 앱 route). */
  subtitle: string;
  description: string;
  icon: LucideIcon;
  /** capability 칩 라벨 (한국어). */
  capabilities: string[];
  /**
   * 실링크 (있으면 실제로 동작하는 딥링크).
   * 데이터 연결 제품 → /data/connections?newSourceType=... (새 소스에서 사용)
   * 플랫폼 앱 → 앱 route (앱 열기)
   */
  primaryHref: string;
  primaryLabel: string;
  /** future 배지로만 노출할 설치 액션 여부. install 백엔드 없음. */
  hasFutureInstall: boolean;
  /** 상세 스크린샷 자리에 그릴 다이어그램 노드 라벨. */
  diagramNodes: readonly string[];
}

const SOURCE_ICON_BY_TYPE: Record<string, LucideIcon> = {
  csv_upload: FileUp,
  rest_api: Cable,
  postgres_jdbc: Database,
  sap_odata: ServerCog,
  sharepoint_graph: Share2,
  webhook_listener: Webhook,
  debezium_cdc: Waypoints,
  media_upload: Image,
  batch_file: FileUp,
};

/** capability 코드 → 한국어 칩 라벨. */
const CAPABILITY_LABELS: Record<string, string> = {
  batch: "배치",
  batch_file: "배치 파일",
  exploration: "탐색",
  code_use: "코드 사용",
  cdc: "CDC",
  streaming: "스트리밍",
  media: "미디어",
};

export function capabilityLabel(code: string): string {
  return CAPABILITY_LABELS[code] ?? code;
}

/** networkMode / credentialMode / managedRunMode 코드 → 라벨(+future 여부). */
export function modeLabel(code: string): { label: string; isFuture: boolean } {
  const isFuture = code.endsWith("_future");
  const base = isFuture ? code.slice(0, -"_future".length) : code;
  const labels: Record<string, string> = {
    direct: "직접 연결",
    agent_proxy: "에이전트 프록시",
    credential_ref: "자격 증명 참조",
    oauth: "OAuth",
    manual: "수동 실행",
    scheduled: "스케줄 실행",
  };
  return { label: labels[base] ?? base, isFuture };
}

function templateDescription(template: SourceTemplate): string {
  const caps = template.capabilities.map(capabilityLabel).join(" · ");
  const network = template.networkModes.some((mode) => mode === "agent_proxy")
    ? "직접/에이전트 프록시"
    : "직접 연결";
  return `${caps} 지원. ${network} 방식으로 연결하고 자격 증명 참조로 sync를 실행합니다.`;
}

const BUILT_IN_CSV_PRODUCT: MarketplaceProduct = {
  id: "source:csv_upload",
  kind: "data-connection",
  name: "CSV 업로드",
  subtitle: "csv_upload",
  description:
    "브라우저에서 CSV 파일을 업로드하고 Foundry-lite dataset transaction으로 즉시 커밋합니다.",
  icon: FileUp,
  capabilities: ["배치"],
  primaryHref: "/data/connections?newSourceType=csv_upload",
  primaryLabel: "새 소스에서 사용",
  hasFutureInstall: false,
  diagramNodes: ["CSV 파일", "CSV 업로드", "Dataset transaction", "데이터셋"],
};

/** SourceTemplate[] → 데이터 연결 제품 카드. */
export function toDataConnectionProducts(
  templates: readonly SourceTemplate[],
): MarketplaceProduct[] {
  const templateProducts = templates.map((template) => ({
    id: `source:${template.sourceType}`,
    kind: "data-connection" as const,
    name: template.displayName,
    subtitle: template.sourceType,
    description: templateDescription(template),
    icon: SOURCE_ICON_BY_TYPE[template.sourceType] ?? Cable,
    capabilities: template.capabilities.map(capabilityLabel),
    primaryHref: `/data/connections?newSourceType=${encodeURIComponent(
      template.sourceType,
    )}`,
    primaryLabel: "새 소스에서 사용",
    hasFutureInstall: false,
    diagramNodes: [
      "외부 시스템",
      template.displayName,
      "자격 증명 · sync",
      "데이터셋",
    ],
  }));
  if (templateProducts.some((product) => product.id === BUILT_IN_CSV_PRODUCT.id)) {
    return templateProducts;
  }
  return [BUILT_IN_CSV_PRODUCT, ...templateProducts];
}

/** 구현된 화면 레지스트리 앱 → 플랫폼 앱 카드. */
export function toPlatformAppProducts(
  screens: readonly ScreenDef[],
): MarketplaceProduct[] {
  return screens
    .filter((screen) => screen.isImplemented && screen.id !== "marketplace")
    .map((screen) => ({
      id: `app:${screen.id}`,
      kind: "platform-app" as const,
      name: screen.title,
      subtitle: screen.route,
      description: screen.description,
      icon: screen.icon,
      capabilities: [
        SCREEN_GROUP_CHIP[screen.group],
        statusChip(screen.status),
      ],
      primaryHref: screen.route,
      primaryLabel: "앱 열기",
      hasFutureInstall: true,
      diagramNodes: ["온톨로지 · 데이터", screen.title, "실행 · 액션", "증거"],
    }));
}

const SCREEN_GROUP_CHIP: Record<ScreenDef["group"], string> = {
  data: "데이터 운영",
  ontology: "온톨로지",
  apps: "운영 앱",
  operations: "플랫폼 관리",
};

function statusChip(status: ScreenDef["status"]): string {
  if (status === "current") return "정식";
  if (status === "partial") return "부분 구현";
  return "참고 전용";
}

export interface MarketplaceCatalog {
  dataConnection: MarketplaceProduct[];
  platformApp: MarketplaceProduct[];
  all: MarketplaceProduct[];
}

export function buildCatalog(
  templates: readonly SourceTemplate[],
  screens: readonly ScreenDef[],
): MarketplaceCatalog {
  const dataConnection = toDataConnectionProducts(templates);
  const platformApp = toPlatformAppProducts(screens);
  return {
    dataConnection,
    platformApp,
    all: [...dataConnection, ...platformApp],
  };
}

export function productsForCategory(
  catalog: MarketplaceCatalog,
  category: MarketplaceCategory,
): MarketplaceProduct[] {
  if (category === "data-connection") return catalog.dataConnection;
  if (category === "platform-app") return catalog.platformApp;
  return catalog.all;
}
