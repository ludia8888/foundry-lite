import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";

const APP_PATH = "/apps/private-hospital";
const IDENTITY = "https://login.example.test/realms/private";

async function mockLogin(page: Page, baseURL: string, isOwner = true) {
  let nonce = "";
  let challenge = "";
  const token = (claims: object) => [
    Buffer.from(JSON.stringify({ alg: "none" })).toString("base64url"),
    Buffer.from(JSON.stringify(claims)).toString("base64url"), "fixture-signature",
  ].join(".");
  await page.route("**/api/auth/browser/config", (route) => route.fulfill({ json: {
    mode: "keycloak", issuer: IDENTITY, clientId: "private-browser",
    redirectUri: `${baseURL}/auth/callback`, applicationId: "private-hospital",
  } }));
  await page.route(`${IDENTITY}/protocol/openid-connect/auth?**`, async (route) => {
    const params = new URL(route.request().url()).searchParams;
    expect(params.get("code_challenge_method")).toBe("S256");
    expect(params.get("response_type")).toBe("code");
    expect(params.get("redirect_uri")).toBe(`${baseURL}/auth/callback`);
    nonce = params.get("nonce") ?? "";
    challenge = params.get("code_challenge") ?? "";
    expect(nonce.length).toBeGreaterThan(16);
    const callback = new URL(`${baseURL}/auth/callback`);
    callback.searchParams.set("code", "one-use-fixture-code");
    callback.searchParams.set("state", params.get("state") ?? "");
    callback.searchParams.set("session_state", "fixture-session");
    await route.fulfill({ contentType: "text/html; charset=utf-8", body:
      `<a href="${callback.href.replaceAll("&", "&amp;")}">시험 로그인 완료</a>` });
  });
  await page.route(`${IDENTITY}/protocol/openid-connect/token`, async (route) => {
    const params = new URLSearchParams(route.request().postData() ?? "");
    if (params.get("grant_type") === "refresh_token") {
      await route.fulfill({ status: 400, json: { error: "invalid_grant" } });
      return;
    }
    expect(params.get("grant_type")).toBe("authorization_code");
    expect(createHash("sha256").update(params.get("code_verifier") ?? "").digest("base64url")).toBe(challenge);
    const now = Math.floor(Date.now() / 1000);
    await route.fulfill({ json: {
      access_token: token({ sub: "owner", exp: now + 300, iat: now, session_state: "fixture-session" }),
      id_token: token({ sub: "owner", nonce, email: "owner@example.test", exp: now + 300, iat: now }),
      refresh_token: token({ exp: now + 1800 }), expires_in: 300, token_type: "Bearer",
    } });
  });
  await page.route("**/api/auth/browser/session", (route) => {
    expect(route.request().headers().authorization).toMatch(/^Bearer /);
    return route.fulfill(isOwner ? { json: {
      userId: "owner", tenantId: "private-tenant", roles: ["viewer"],
      applicationId: "private-hospital", clientId: "private-browser",
    } } : { status: 403, json: { detail: { code: "PERMISSION_DENIED", message: "not invited" } } });
  });
  await page.route("**/api/ontology/catalog", (route) => route.fulfill({ json: {
    ontologyVersionId: "test", versionNumber: 1, objectTypes: [], interfaceTypes: [], functionTypes: [], linkTypes: [],
  } }));
  await page.route("**/api/aip/pilot/operating-applications/private-hospital", (route) => {
    expect(route.request().headers().authorization).toMatch(/^Bearer /);
    expect(route.request().headers()["x-user-id"]).toBe("owner");
    expect(route.request().headers()["x-roles"]).toBe("viewer");
    return route.fulfill({ json: {
      applicationName: "병원 운영", businessSystemDefinition: { experience: {} },
      operatingApplication: { status: "awaiting_release", blockers: [{ code: "test", message: "시험용 승인 안내" }] },
    } });
  });
}

test("strict server without browser configuration shows recovery instead of issuing demo requests", async ({ page }) => {
  await page.route("**/api/auth/browser/config", (route) => route.fulfill({ json: { mode: "unavailable" } }));
  const protectedRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/api/aip/") || request.url().includes("/api/ontology/")) protectedRequests.push(request.url());
  });
  await page.goto(APP_PATH);
  await expect(page.getByRole("alert")).toContainText("로그인 준비가 끝나지 않았습니다");
  expect(protectedRequests).toEqual([]);
  await expect(page.getByRole("button", { name: "연결 다시 확인" })).toBeVisible();
});

test("PKCE login contract returns to original app with verified user and no persistent tokens", async ({ page, baseURL }) => {
  await mockLogin(page, baseURL!);
  await page.goto(APP_PATH);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("link", { name: "시험 로그인 완료" }).click();
  await expect(page).toHaveURL(`${baseURL}${APP_PATH}`);
  await expect(page.getByRole("heading", { name: "병원 운영" })).toBeVisible();
  await expect(page.getByText("owner@example.test")).toBeVisible();
  const persisted = await page.evaluate(() => JSON.stringify({ ...localStorage, ...sessionStorage }));
  expect(persisted).not.toContain("fixture-signature");
  expect(page.url()).not.toContain("code=");
});

test("another logged-in identity never mounts the protected app", async ({ page, baseURL }) => {
  await mockLogin(page, baseURL!, false);
  let appRequests = 0;
  page.on("request", (request) => { if (request.url().includes("/api/aip/")) appRequests += 1; });
  await page.goto(APP_PATH);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("link", { name: "시험 로그인 완료" }).click();
  await expect(page.getByRole("alert")).toContainText("접근 권한을 확인하지 못했습니다");
  await expect(page.getByRole("button", { name: "초대받은 계정으로 로그인" })).toBeVisible();
  expect(appRequests).toBe(0);
});

test("login and recovery remain readable on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 360, height: 740 });
  await page.route("**/api/auth/browser/config", (route) => route.fulfill({ json: { mode: "unavailable" } }));
  await page.goto(APP_PATH);
  await expect(page.getByRole("heading")).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: "artifacts/operations/private-browser-login-mobile.png", fullPage: true });
});

test("failed session renewal removes the app instead of falling back to demo", async ({ page, baseURL }) => {
  await page.clock.install();
  await mockLogin(page, baseURL!);
  await page.goto(APP_PATH);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("link", { name: "시험 로그인 완료" }).click();
  await expect(page.getByRole("heading", { name: "병원 운영" })).toBeVisible();
  await page.clock.fastForward(301_000);
  await expect(page.getByRole("button", { name: "로그인", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "병원 운영" })).toHaveCount(0);
});

test("logout leaves the protected app and a reload has no stored login", async ({ page, baseURL }) => {
  await mockLogin(page, baseURL!);
  await page.route(`${IDENTITY}/protocol/openid-connect/logout?**`, (route) => route.fulfill({
    contentType: "text/html; charset=utf-8", body: "<h1>로그아웃 완료</h1>",
  }));
  await page.goto(APP_PATH);
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await page.getByRole("link", { name: "시험 로그인 완료" }).click();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByRole("heading", { name: "로그아웃 완료" })).toBeVisible();
  await page.goto(APP_PATH);
  await expect(page.getByRole("button", { name: "로그인", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "병원 운영" })).toHaveCount(0);
});
