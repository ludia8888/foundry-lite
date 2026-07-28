import type { FoundryLiteGeneratedClient } from "@foundry-lite/sdk";
import { useEffect, useState } from "react";

interface AuthenticatedMediaSource {
  contentType: string;
  url: string;
  versionId: string;
}

export function useAuthenticatedMediaSource(
  client: FoundryLiteGeneratedClient,
  versionId: string | null,
): AuthenticatedMediaSource | null {
  const [source, setSource] = useState<AuthenticatedMediaSource | null>(null);

  useEffect(() => {
    if (!versionId) return;
    const controller = new AbortController();
    let objectUrl: string | null = null;
    void client.media.versions
      .readContent(versionId, { signal: controller.signal })
      .then((blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setSource({
          contentType: blob.type,
          url: objectUrl,
          versionId,
        });
      })
      .catch(() => {
        if (!controller.signal.aborted) setSource(null);
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [client, versionId]);

  return source?.versionId === versionId ? source : null;
}
