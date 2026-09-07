import {
  createFoundryLiteClient,
  type BrowserAuthConfiguration,
  type FoundryLiteRequestContext,
  type FoundryLiteSessionProvider,
} from "@foundry-lite/sdk";
import type Keycloak from "keycloak-js";

import { API_BASE_URL, DEMO_CONTEXT, DEMO_SESSION } from "@/lib/api";

type LoginConfiguration = Extract<BrowserAuthConfiguration, { mode: "keycloak" }>;
export type BrowserSessionState = {
  status: "checking" | "signed_out" | "ready" | "error";
  context: FoundryLiteRequestContext;
  isLocal: boolean;
  displayName?: string;
  message?: string;
  canSignIn?: boolean;
};

const RETURN_PATH_KEY = "foundry-lite.browser-return-path.v1";
const bootstrapClient = createFoundryLiteClient({ baseUrl: API_BASE_URL });

export function privateAppReturnPath(path: string | null, applicationId: string): string {
  const appPath = `/apps/${encodeURIComponent(applicationId)}`;
  // This pilot identity is an app consumer, not a Workshop builder. Editor
  // links return to the same app runtime, never to privileged configuration.
  // No URLs, query strings, fragments, or other applications are accepted.
  return path === appPath ? path : appPath;
}

class BrowserSessionController {
  private state: BrowserSessionState = { status: "checking", context: {}, isLocal: false };
  private listeners = new Set<() => void>();
  private initialization?: Promise<void>;
  private keycloak?: Keycloak;
  private configuration?: LoginConfiguration;

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => { this.listeners.delete(listener); };
  };

  initialize = () => {
    this.initialization ??= this.bootstrap().catch(() => {
      this.clearCallbackUrl();
      this.setState({ status: "error", context: {}, isLocal: false,
        canSignIn: Boolean(this.keycloak),
        message: "로그인 연결을 확인하지 못했습니다. 다시 시도해 주세요. 계속되면 앱 관리자에게 알려주세요." });
    });
    return this.initialization;
  };

  private async bootstrap() {
    const configuration = await bootstrapClient.auth.browser.configuration();
    if (configuration.mode === "local") {
      this.setState({ status: "ready", context: DEMO_CONTEXT, isLocal: true, displayName: DEMO_CONTEXT.userId });
      return;
    }
    if (configuration.mode !== "keycloak") {
      this.setState({ status: "error", context: {}, isLocal: false,
        message: "아직 이 앱의 로그인 준비가 끝나지 않았습니다. 앱 관리자에게 로그인 연결을 요청해 주세요." });
      return;
    }
    await this.initializeKeycloak(configuration);
  }

  private async initializeKeycloak(configuration: LoginConfiguration) {
    const callback = new URL(configuration.redirectUri);
    if (callback.origin !== window.location.origin || callback.pathname !== "/auth/callback") {
      throw new Error("로그인 주소와 앱 주소가 일치하지 않습니다.");
    }
    const issuer = new URL(configuration.issuer);
    const marker = issuer.pathname.lastIndexOf("/realms/");
    if (marker < 0 || issuer.protocol !== "https:") throw new Error("로그인 제공자 설정을 확인해 주세요.");
    this.configuration = configuration;
    if (window.location.pathname !== "/auth/callback") {
      sessionStorage.setItem(RETURN_PATH_KEY, privateAppReturnPath(window.location.pathname, configuration.applicationId));
    }
    const { default: KeycloakAdapter } = await import("keycloak-js");
    const keycloak = new KeycloakAdapter({
      url: `${issuer.origin}${issuer.pathname.slice(0, marker)}`,
      realm: decodeURIComponent(issuer.pathname.slice(marker + 8)), clientId: configuration.clientId,
    });
    this.keycloak = keycloak;
    keycloak.onAuthLogout = this.expire;
    keycloak.onAuthRefreshError = this.expire;
    keycloak.onTokenExpired = () => { void keycloak.updateToken(30).catch(this.expire); };
    const isAuthenticated = await keycloak.init({
      checkLoginIframe: false, pkceMethod: "S256", flow: "standard", responseMode: "query",
      redirectUri: configuration.redirectUri,
    });
    if (!isAuthenticated) {
      this.clearCallbackUrl();
      this.setState({ status: "signed_out", context: {}, isLocal: false, canSignIn: true });
      return;
    }
    await this.verifySession();
  }

  private async verifySession() {
    const client = createFoundryLiteClient({ baseUrl: API_BASE_URL, tokenProvider: async () => this.keycloak?.token });
    try {
      const context = await client.auth.browser.session();
      this.clearCallbackUrl();
      const email: unknown = this.keycloak?.idTokenParsed?.email;
      this.setState({ status: "ready", context, isLocal: false,
        displayName: typeof email === "string" ? email : "내 계정" });
    } catch {
      this.keycloak?.clearToken();
      this.clearCallbackUrl();
      this.setState({ status: "error", context: {}, isLocal: false, canSignIn: true,
        message: "이 앱의 접근 권한을 확인하지 못했습니다. 초대받은 계정으로 다시 로그인해 주세요." });
    }
  }

  sessionProvider: FoundryLiteSessionProvider = async () => {
    if (this.state.isLocal) return DEMO_SESSION;
    if (this.state.status !== "ready" || !this.keycloak) throw new Error("로그인이 필요합니다.");
    try {
      await this.keycloak.updateToken(30);
      if (!this.keycloak.token || !this.keycloak.authenticated) throw new Error("로그인이 만료됐습니다.");
      return { accessToken: this.keycloak.token, context: this.state.context };
    } catch {
      this.expire();
      throw new Error("로그인이 만료됐습니다. 다시 로그인해 주세요.");
    }
  };

  signIn = async () => {
    if (!this.keycloak || !this.configuration) return;
    try {
      sessionStorage.setItem(RETURN_PATH_KEY, privateAppReturnPath(window.location.pathname, this.configuration.applicationId));
      await this.keycloak.login({ redirectUri: this.configuration.redirectUri, prompt: "login" });
    } catch {
      this.setState({ status: "error", context: {}, isLocal: false, canSignIn: true,
        message: "로그인 페이지를 열지 못했습니다. 연결을 확인하고 다시 시도해 주세요." });
    }
  };

  signOut = async () => {
    if (!this.keycloak || !this.configuration) return;
    // Capture the provider logout URL before clearing in-memory credentials.
    const url = this.keycloak.createLogoutUrl({ redirectUri: `${window.location.origin}/apps/${encodeURIComponent(this.configuration.applicationId)}` });
    this.keycloak.clearToken();
    window.location.assign(url);
  };

  private expire = () => {
    this.keycloak?.clearToken();
    this.setState({ status: "signed_out", context: {}, isLocal: false, canSignIn: true,
      message: "안전한 이용을 위해 로그인이 종료됐습니다. 다시 로그인하면 업무를 이어갈 수 있습니다." });
  };

  private clearCallbackUrl() {
    if (!this.configuration) return;
    const path = privateAppReturnPath(sessionStorage.getItem(RETURN_PATH_KEY), this.configuration.applicationId);
    if (window.location.pathname === "/auth/callback") window.history.replaceState(null, "", path);
    sessionStorage.removeItem(RETURN_PATH_KEY);
  }

  private setState(state: BrowserSessionState) {
    this.state = state;
    this.listeners.forEach((listener) => listener());
  }
}

// A page owns exactly one adapter, including under React StrictMode.
export const browserSession = new BrowserSessionController();
