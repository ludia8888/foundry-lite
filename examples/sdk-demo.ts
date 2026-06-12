import {
  createFoundryLiteClient,
  expectedObjectVersion,
  idempotencyKey,
  type Order,
} from "../packages/sdk-ts/src/client";

const client = createFoundryLiteClient({
  baseUrl: process.env.FOUNDRY_LITE_API_URL ?? "http://127.0.0.1:8000",
  headers: {
    "X-User-ID": "sdk-demo-operator",
    "X-Roles": "ops_manager,data_engineer,finance",
  },
});

const order: Order = await client.objects.Order.get("O-1001", { explain: true });
const pendingOrders = await client.objects.Order.query({
  filter: { property: "status", op: "eq", value: "PENDING" },
  limit: 5,
});

await client.actions.ApproveOrder.apply({
  objectId: order.objectId,
  expectedObjectVersion: expectedObjectVersion(order),
  params: { reason: "Inventory confirmed" },
  idempotencyKey: idempotencyKey("ApproveOrder", order.objectId),
});

console.log(
  JSON.stringify(
    {
      orderId: order.objectId,
      pendingCount: pendingOrders.items.length,
    },
    null,
    2,
  ),
);
