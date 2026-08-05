import {
  normalizeFoundryLiteError,
  type ActionNotificationPolicy,
  type FoundryLiteApiError,
} from "@foundry-lite/sdk";
import { useFoundryLiteClient } from "@foundry-lite/sdk/react";
import { useCallback, useEffect, useState } from "react";

export function useActionNotificationPolicies() {
  const client = useFoundryLiteClient();
  const [policies, setPolicies] = useState<ActionNotificationPolicy[]>([]);
  const [error, setError] = useState<FoundryLiteApiError | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      const result = await client.actions.notificationPolicies.list({ limit: 100 });
      setPolicies(result.items);
      setError(null);
    } catch (cause) {
      setError(normalizeFoundryLiteError(cause));
    } finally {
      setIsLoading(false);
    }
  }, [client]);
  useEffect(() => { void reload(); }, [reload]);
  return { policies, error, isLoading, reload };
}
