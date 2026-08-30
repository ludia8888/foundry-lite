import type {
  FoundryLiteRequestContext,
  FoundryLiteSession,
} from "@foundry-lite/sdk";

/**
 * 로컬 개발에서는 uvicorn(127.0.0.1:8000)을 직접 사용한다.
 * 배포 빌드는 사용자가 연 현재 origin의 `/api` reverse proxy를 사용하므로,
 * Mac mini·고객 도메인·HTTPS 환경 어디에서도 빌드 시점의 loopback 주소를
 * 브라우저에 새기지 않는다. 병렬 검증 환경은
 * `VITE_FOUNDRY_LITE_API_URL`로 별도 runtime을 명시할 수 있다.
 */
const configuredApiBaseUrl = import.meta.env.VITE_FOUNDRY_LITE_API_URL?.trim();
const defaultApiBaseUrl = import.meta.env.DEV
  ? "http://127.0.0.1:8000"
  : globalThis.location.origin;

export const API_BASE_URL = configuredApiBaseUrl
  ? configuredApiBaseUrl.replace(/\/+$/, "")
  : defaultApiBaseUrl;

/**
 * 기본 auth profile(header-trust)에서 신뢰되는 데모 컨텍스트.
 * AIP evidence까지 확인하는 로컬 operator workflow이므로 prompt artifact reader도 포함한다.
 */
export const DEMO_CONTEXT: FoundryLiteRequestContext = {
  tenantId: "tenant-demo",
  userId: "web-demo-operator",
  roles: ["ops_manager", "data_engineer", "finance", "aip_prompt_artifact_reader"],
};

export const DEMO_SESSION: FoundryLiteSession = {
  context: DEMO_CONTEXT,
};
