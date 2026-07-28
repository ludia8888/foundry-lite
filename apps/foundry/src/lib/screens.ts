import type { LucideIcon } from "lucide-react";
import {
  Boxes,
  Cable,
  CircleCheckBig,
  Database,
  FolderTree,
  GitBranch,
  LayoutGrid,
  Network,
  Play,
  ScanText,
  ServerCog,
  Shapes,
  ShieldCheck,
  Sparkles,
  SquareTerminal,
  Store,
  TrendingUp,
  Workflow,
} from "lucide-react";

export type ScreenStatus = "current" | "partial" | "reference_only";
export type ScreenGroup = "data" | "ontology" | "apps" | "operations";

export interface ScreenDef {
  id: string;
  title: string;
  route: string;
  icon: LucideIcon;
  group: ScreenGroup;
  description: string;
  status: ScreenStatus;
  /** false면 placeholder 화면 — 셸 전역에 '예정' 배지로 표기한다. */
  isImplemented: boolean;
  isPinnedToRail: boolean;
}

export const SCREEN_GROUP_LABELS: Record<ScreenGroup, string> = {
  data: "Applications for Data Ops",
  ontology: "Applications for Ontology",
  apps: "Applications for Operations",
  operations: "Platform Administration",
};

/** Palantir 화면 분류 기준의 전체 화면 레지스트리 (스펙 v2 developer_handoff 기준). */
export const SCREENS: readonly ScreenDef[] = [
  {
    id: "projects",
    title: "Files",
    route: "/projects",
    icon: FolderTree,
    group: "data",
    description: "프로젝트/폴더/리소스 탐색과 생성 (Compass)",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "data-connection",
    title: "Data Connection",
    route: "/data/connections",
    icon: Cable,
    group: "data",
    description: "외부 데이터 source 생성과 sync 실행",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "datasets",
    title: "Dataset Preview",
    route: "/datasets",
    icon: Database,
    group: "data",
    description: "스키마, 프리뷰, 버전, 품질 evidence 확인",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "pipelines",
    title: "Pipeline Builder",
    route: "/pipelines",
    icon: Workflow,
    group: "data",
    description: "그래프 기반 ETL 브랜치/검증/배포",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "document-intelligence",
    title: "Document Intelligence",
    route: "/document-intelligence",
    icon: ScanText,
    group: "data",
    description: "PDF/OCR/layout/VLM 전략 실험과 bbox 근거 검증",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "lineage",
    title: "Data Lineage",
    route: "/lineage",
    icon: Network,
    group: "data",
    description: "리니지 그래프와 run/품질 evidence",
    status: "current",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "code",
    title: "Code Repositories",
    route: "/code",
    icon: SquareTerminal,
    group: "data",
    description: "SQL transform 등록/실행/재시도",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "ontology",
    title: "Ontology Manager",
    route: "/ontology",
    icon: Shapes,
    group: "ontology",
    description: "객체/링크/액션 타입과 branch/proposal",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "objects",
    title: "Object Explorer",
    route: "/objects",
    icon: Boxes,
    group: "ontology",
    description: "객체 탐색, 필터/집계, ObjectSet",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "actions",
    title: "Action Types / Functions",
    route: "/actions",
    icon: Play,
    group: "ontology",
    description: "액션 폼 실행과 함수 호출",
    status: "current",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "workshop",
    title: "Workshop",
    route: "/workshop",
    icon: LayoutGrid,
    group: "apps",
    description: "객체 뷰 조합으로 최소 업무 앱 구성",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "aip",
    title: "AIP",
    route: "/aip",
    icon: Sparkles,
    group: "apps",
    description: "AI 빌더/에이전트 실행과 근거 artifact",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "approvals",
    title: "Approvals",
    route: "/approvals",
    icon: CircleCheckBig,
    group: "apps",
    description: "리뷰 큐 assign/decide/execute",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "developer",
    title: "Developer Console",
    route: "/developer",
    icon: GitBranch,
    group: "apps",
    description: "OSDK 앱/클라이언트/SDK 버전 관리",
    status: "current",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "marketplace",
    title: "Marketplace",
    route: "/marketplace",
    icon: Store,
    group: "apps",
    description: "템플릿 참고 화면 (reference)",
    status: "reference_only",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "analytics",
    title: "Analytics",
    route: "/analytics",
    icon: TrendingUp,
    group: "apps",
    description: "Quiver/Contour 패턴 참고 화면 (reference)",
    status: "reference_only",
    isImplemented: true,
    isPinnedToRail: false,
  },
  {
    id: "operations",
    title: "Platform Operations",
    route: "/operations",
    icon: ServerCog,
    group: "operations",
    description: "run 조사, DLQ, 유지보수, 복구",
    status: "current",
    isImplemented: true,
    isPinnedToRail: true,
  },
  {
    id: "security",
    title: "Security & Governance",
    route: "/security",
    icon: ShieldCheck,
    group: "operations",
    description: "project grant와 권한 상태",
    status: "partial",
    isImplemented: true,
    isPinnedToRail: false,
  },
];

export function getScreensByGroup(group: ScreenGroup): ScreenDef[] {
  return SCREENS.filter((screen) => screen.group === group);
}

export function getScreenByRoute(pathname: string): ScreenDef | undefined {
  return SCREENS.find(
    (screen) =>
      pathname === screen.route || pathname.startsWith(`${screen.route}/`),
  );
}
