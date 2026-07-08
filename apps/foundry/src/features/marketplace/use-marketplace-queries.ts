import {
  useFoundryLiteClient,
  useFoundryLiteQuery,
} from "@foundry-lite/sdk/react";
import { useCallback } from "react";

/**
 * sources.templates.list — 마켓플레이스의 '데이터 연결 제품' 카탈로그.
 * Data Connection 위저드의 소스 템플릿 picker와 동일한 실 SDK 호출을 재사용한다.
 */
export function useSourceTemplateCatalog() {
  const client = useFoundryLiteClient();
  const load = useCallback(() => client.sources.templates.list(), [client]);
  return useFoundryLiteQuery(["marketplace", "source-templates"], load);
}
