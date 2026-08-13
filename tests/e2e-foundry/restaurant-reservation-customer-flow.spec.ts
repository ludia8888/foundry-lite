import { expect, type Page, test } from "@playwright/test";

const RESTAURANT = {
  objectType: "Restaurant",
  objectId: "seed-restaurant-1",
  objectVersion: 3,
  properties: {
    id: "seed-restaurant-1",
    name: "MCP Bistro Seoul",
    location: "서울 강남구",
    restaurantId: "REST-MCP-SEOUL-01",
    status: "OPEN",
    serviceStartLocal: "17:30",
    lastSeatingLocal: "21:30",
    slotIntervalMinutes: 30,
    largePartyThreshold: 4,
    depositPerPersonKrw: 20_000,
    lateArrivalGraceMinutes: 15,
    policyVersion: "2026.08.2",
  },
};

const TABLE = {
  objectType: "DiningTable",
  objectId: "seed-restaurant-1",
  objectVersion: 2,
  properties: {
    id: "seed-restaurant-1",
    tableId: "TABLE-WINDOW-04",
    label: "창가 4인석",
    capacity: 4,
    status: "AVAILABLE",
    area: "WINDOW",
    turnTimeMinutes: 120,
    isAccessible: true,
  },
};

function quote(isBookable: boolean, isPaymentRequired = false) {
  return {
    functionApiName: "QuoteAtomicReservationPolicy",
    logicRunId: "fnrun_customer_ui",
    aiRunId: null,
    status: "SUCCEEDED",
    output: {
      value: {
        decision: isBookable ? (isPaymentRequired ? "PAYMENT_REQUIRED" : "AUTO_CONFIRMED") : "UNAVAILABLE",
        isBookable,
        reasonCodes: isBookable ? [] : ["TURN_TIME_CONFLICT"],
        depositAmountKrw: isPaymentRequired ? 80_000 : 0,
        cancellationDeadlineAt: "2026-08-14T19:30:00+09:00",
        lateArrivalGraceMinutes: 15,
        quoteExpiresAt: "2026-08-12T17:25:00+09:00",
        inventoryHoldStatus: "AVAILABLE_NOT_HELD",
        holdDurationMinutes: 10,
        serviceEndsAt: "2026-08-15T21:30:00+09:00",
        turnTimeMinutes: 120,
        policyVersion: "2026.08.2",
        policyMessage: "24시간 전까지 무료 취소",
      },
    },
    resultHash: "sha256:quote-ui",
  };
}

async function mockReservationApi(page: Page) {
  const actionRequests: Array<{ body: Record<string, unknown>; idempotencyKey: string | undefined }> = [];
  let createdReservationId = "";
  let reservationProperties: Record<string, unknown> = {};
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path === "/api/objects/Restaurant/seed-restaurant-1") {
      await route.fulfill({ json: RESTAURANT });
      return;
    }
    if (path === "/api/objects/DiningTable/seed-restaurant-1") {
      await route.fulfill({ json: TABLE });
      return;
    }
    if (path === "/api/objects/Reservation/query") {
      await route.fulfill({ json: { items: [], nextCursor: null } });
      return;
    }
    if (path === "/api/functions/QuoteAtomicReservationPolicy/execute") {
      const body = request.postDataJSON() as { inputs: { desiredAt: string; partySize: number } };
      await route.fulfill({ json: quote(!body.inputs.desiredAt.includes("T19:00:"), body.inputs.partySize >= 4) });
      return;
    }
    if (/^\/api\/actions\/(ReserveTableWithAtomicInventory|PayReservationDeposit|CancelCustomerReservation)\/runs$/.test(path)) {
      const body = request.postDataJSON() as Record<string, unknown>;
      const params = body.params as Record<string, unknown>;
      const actionApiName = path.split("/")[3];
      if (actionApiName === "ReserveTableWithAtomicInventory") {
        createdReservationId = String(params.reservationId);
        const isPaymentRequired = Number(params.partySize) >= 4;
        reservationProperties = {
          reservationId: createdReservationId,
          guestName: "김민지",
          reservationAt: params.desiredAt,
          partySize: params.partySize,
          status: isPaymentRequired ? "PAYMENT_REQUIRED" : "AUTO_CONFIRMED",
          policyDecision: isPaymentRequired ? "PAYMENT_REQUIRED" : "AUTO_CONFIRMED",
          policyVersion: "2026.08.2",
          lateArrivalGraceMinutes: 15,
          cancellationDeadlineAt: "2026-08-14T19:30:00+09:00",
          inventoryHoldStatus: isPaymentRequired ? "HELD" : "BOOKED",
          holdExpiresAt: "2026-08-12T17:30:00+09:00",
          paymentStatus: isPaymentRequired ? "REQUIRED" : "NOT_REQUIRED",
          quotedDepositKrw: isPaymentRequired ? 80_000 : 0,
        };
      } else if (actionApiName === "PayReservationDeposit") {
        reservationProperties = {
          ...reservationProperties,
          status: "CONFIRMED",
          inventoryHoldStatus: "BOOKED",
          paymentStatus: "AUTHORIZED",
          paidDepositKrw: 80_000,
        };
      } else {
        reservationProperties = {
          ...reservationProperties,
          status: "CANCELLED",
          inventoryHoldStatus: "RELEASED",
          paymentStatus: "REFUNDED",
          refundStatus: "REFUNDED",
          refundedAmountKrw: 80_000,
        };
      }
      actionRequests.push({
        body,
        idempotencyKey: request.headers()["idempotency-key"],
      });
      await route.fulfill({
        json: {
          actionRunId: "action_run_customer_ui",
          actionApiName,
          status: "succeeded",
          target: body.target,
          expectedObjectVersion: body.expectedObjectVersion,
          parameters: params,
          planHash: "sha256:plan-ui",
          definitionVersion: "sha256:definition-ui",
          orchestration: {},
          steps: [],
          attempts: [],
          effects: [],
          cancel: { requestedAt: null, reason: null },
          result: { status: "succeeded" },
          error: null,
          eventSequence: 1,
          createdAt: "2026-08-12T17:20:00+09:00",
          startedAt: "2026-08-12T17:20:00+09:00",
          completedAt: "2026-08-12T17:20:01+09:00",
        },
      });
      return;
    }
    if (path.startsWith("/api/objects/Reservation/") && createdReservationId) {
      await route.fulfill({
        json: {
          objectType: "Reservation",
          objectId: createdReservationId,
          objectVersion: 1,
          properties: reservationProperties,
        },
      });
      return;
    }
    await route.fallback();
  });
  return actionRequests;
}

test("customer can quote, accept policy, and create a reservation", async ({ page }) => {
  const browserErrors: string[] = [];
  page.on("console", (message) => {
    if (message.type() === "error") browserErrors.push(message.text());
  });
  page.on("pageerror", (error) => browserErrors.push(error.message));
  const actionRequests = await mockReservationApi(page);

  await page.goto("/book/mcp-bistro-seoul");
  await expect(page.getByTestId("customer-reservation-page")).toBeVisible();
  await page.getByTestId("booking-date").fill("2026-08-15");
  await page.getByTestId("guest-phone-schedule").fill("010-1234-5678");
  await page.getByTestId("party-size-2").click();
  await page.getByTestId("seat-window").click();
  await page.getByTestId("slot-19-30").click();

  await expect(page.getByTestId("policy-quote")).toContainText("바로 확정 가능한 자리입니다");
  await expect(page.getByTestId("policy-quote")).toContainText("좌석을 선점하지 않습니다");
  await page.getByTestId("continue-to-details").click();
  await page.getByTestId("guest-name").fill("김민지");
  await page.getByTestId("guest-email").fill("minji@example.com");
  await page.getByTestId("special-request").fill("기념일 디저트 문의");
  await page.getByTestId("continue-to-policy").click();
  await expect(page.getByTestId("policy-step")).toContainText("정책 2026.08.2");
  await page.getByTestId("accept-policy").check();
  await page.getByTestId("submit-booking").click();

  await expect(page.getByTestId("booking-success")).toContainText("RESERVATION CONFIRMED");
  await expect(page.getByTestId("reservation-id")).toContainText("RSV-WEB-20260815-1930-");
  expect(actionRequests).toHaveLength(1);
  expect(actionRequests[0].idempotencyKey).toMatch(/^customer-book:RSV-WEB-/);
  expect(actionRequests[0].body).toMatchObject({
    target: { objectType: "Restaurant", objectId: "seed-restaurant-1" },
    expectedObjectVersion: 3,
    params: {
      guestName: "김민지",
      guestPhone: "010-1234-5678",
      guestEmail: "minji@example.com",
      customerAcceptedPolicy: true,
      desiredAt: "2026-08-15T19:30:00+09:00",
    },
  });
  expect(browserErrors, "browser console/page errors").toEqual([]);
});

test("large party hold can be paid and cancelled with a sandbox refund", async ({ page }) => {
  const actionRequests = await mockReservationApi(page);
  await page.goto("/book/mcp-bistro-seoul");
  await page.getByTestId("booking-date").fill("2026-08-15");
  await page.getByTestId("guest-phone-schedule").fill("010-1234-5678");
  await page.getByTestId("party-size-4").click();
  await page.getByTestId("slot-19-30").click();
  await expect(page.getByTestId("policy-quote")).toContainText("보증금 결제 후 예약 가능");
  await page.getByTestId("continue-to-details").click();
  await page.getByTestId("guest-name").fill("김민지");
  await page.getByTestId("guest-email").fill("minji@example.com");
  await page.getByTestId("continue-to-policy").click();
  await page.getByTestId("accept-policy").check();
  await page.getByTestId("submit-booking").click();

  await expect(page.getByTestId("payment-panel")).toContainText("80,000원");
  await page.getByTestId("pay-deposit").click();
  await expect(page.getByTestId("payment-authorized")).toContainText("승인 완료");
  await page.getByTestId("cancel-reservation").click();
  await expect(page.getByTestId("refund-result")).toContainText("REFUNDED");
  await expect(page.getByTestId("refund-result")).toContainText("80,000원");

  expect(actionRequests).toHaveLength(3);
  expect(actionRequests.map((item) => (item.body as { params: Record<string, unknown> }).params)).toEqual([
    expect.objectContaining({ reservationId: expect.stringMatching(/^RSV-WEB-/), partySize: 4 }),
    expect.objectContaining({ paymentMethodRef: "pm_sandbox_visa_4242" }),
    expect.objectContaining({ cancellationReason: "CUSTOMER_REQUEST" }),
  ]);
});

test("turn-time conflict blocks progression and performs no write", async ({ page }) => {
  const actionRequests = await mockReservationApi(page);
  await page.goto("/book/mcp-bistro-seoul");
  await page.getByTestId("booking-date").fill("2026-08-15");
  await page.getByTestId("guest-phone-schedule").fill("010-1234-5678");
  await page.getByTestId("slot-19-00").click();

  await expect(page.getByTestId("policy-quote")).toContainText("앞선 예약의 이용시간과 겹칩니다");
  await expect(page.getByTestId("continue-to-details")).toBeDisabled();
  expect(actionRequests).toHaveLength(0);
});
