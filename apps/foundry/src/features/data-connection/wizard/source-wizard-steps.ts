import type { WizardStepMeta } from "./WizardStepLayout";

/** 업로드/CDC 계열의 공통 Source 설정 단계. */
export const NEW_SOURCE_STEPS = [
  { id: "type", title: "소스 유형 선택" },
  { id: "connection", title: "연결 방식" },
  { id: "project", title: "프로젝트에 저장" },
  { id: "configure", title: "구성 & 자격 증명" },
  { id: "done", title: "완료" },
] as const satisfies readonly WizardStepMeta[];

/** REST/JDBC 관리형 Source의 끊기지 않는 전체 설정 단계. */
export const MANAGED_SOURCE_STEPS = [
  { id: "type", title: "소스 유형 선택" },
  { id: "connection", title: "연결 방식" },
  { id: "project", title: "프로젝트에 저장" },
  { id: "credential", title: "자격 증명 & 네트워크" },
  { id: "sync", title: "동기화 설정" },
  { id: "run", title: "실행 & 증거" },
  { id: "done", title: "완료" },
] as const satisfies readonly WizardStepMeta[];
