export type FoundryLiteClientOptions = {
  baseUrl: string;
  token?: string;
};

export function createFoundryLiteClient(options: FoundryLiteClientOptions) {
  async function request(path: string, init?: RequestInit) {
    const response = await fetch(`${options.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(options.token ? { Authorization: `Bearer ${options.token}` } : {}),
        ...(init?.headers ?? {}),
      },
    });
    if (!response.ok) {
      throw new Error(await response.text());
    }
    return response.json();
  }

  return {
    objects: {
      Order: {
        get: (id: string) => request(`/api/objects/Order/${id}`),
      },
    },
    actions: {
      ApproveOrder: {
        apply: (payload: {
          objectId: string;
          expectedObjectVersion: number;
          params: { reason: string };
          idempotencyKey: string;
        }) =>
          request("/api/actions/ApproveOrder/apply", {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              "Idempotency-Key": payload.idempotencyKey,
            },
            body: JSON.stringify({
              target: { objectType: "Order", objectId: payload.objectId },
              expectedObjectVersion: payload.expectedObjectVersion,
              params: payload.params,
            }),
          }),
      },
    },
  };
}

