import type {
  FoundryLiteRequestContext,
  FoundryLiteSession,
} from "@foundry-lite/sdk";

/**
 * 백엔드는 `pnpm dev`(uvicorn 127.0.0.1:8000)로 기동한다.
 * CORS 허용 origin이 4173으로 하드코딩되어 있어 dev 서버는 반드시 4173 포트를 쓴다.
 */
export const API_BASE_URL = "http://127.0.0.1:8000";

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
