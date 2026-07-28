type IdempotencyKeyFactory = (scope: string, intent: string) => string;

export type PipelineIdempotencyRegistry = {
  acquire(scope: string, payload: unknown): string;
  complete(scope: string, payload: unknown, key: string): void;
};

/**
 * Retain a mutation key until the server confirms success.
 *
 * A retry with the same semantic payload reuses its key, while a successful
 * operation releases the intent so a later deliberate action receives a new key.
 */
export function createPipelineIdempotencyRegistry(
  createKey: IdempotencyKeyFactory,
): PipelineIdempotencyRegistry {
  const pendingKeys = new Map<string, string>();
  let keySequence = 0;

  return {
    acquire(scope, payload) {
      const intent = mutationIntent(scope, payload);
      const retained = pendingKeys.get(intent);
      if (retained) return retained;
      keySequence += 1;
      const key = createKey(scope, `intent-${keySequence}`);
      pendingKeys.set(intent, key);
      return key;
    },
    complete(scope, payload, key) {
      const intent = mutationIntent(scope, payload);
      if (pendingKeys.get(intent) === key) pendingKeys.delete(intent);
    },
  };
}

export async function runRetainedPipelineMutation<TResult>(
  registry: PipelineIdempotencyRegistry,
  scope: string,
  payload: unknown,
  onKey: (key: string) => void,
  mutate: (key: string) => Promise<TResult>,
): Promise<TResult> {
  const key = registry.acquire(scope, payload);
  onKey(key);
  const result = await mutate(key);
  registry.complete(scope, payload, key);
  return result;
}

function mutationIntent(scope: string, payload: unknown): string {
  return `${scope}:${canonicalJson(payload)}`;
}

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map(
        (key) =>
          `${JSON.stringify(key)}:${canonicalJson(record[key])}`,
      )
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}
