import { FoundryLiteProvider } from "@foundry-lite/sdk/react";
import { LockKeyhole, LogOut, ShieldCheck } from "lucide-react";
import { Suspense, useEffect, useState, useSyncExternalStore, type ReactNode } from "react";

import { API_BASE_URL } from "@/lib/api";
import { browserSession } from "./browser-session";

export function useBrowserSession() {
  return useSyncExternalStore(browserSession.subscribe, browserSession.getSnapshot);
}

export function BrowserAuthGate({ children }: { children: ReactNode }) {
  const session = useBrowserSession();
  const [isSigningIn, setIsSigningIn] = useState(false);
  useEffect(() => { void browserSession.initialize(); }, []);

  if (session.status === "ready") {
    return <FoundryLiteProvider baseUrl={API_BASE_URL} sessionProvider={browserSession.sessionProvider} context={session.context}>
      {!session.isLocal ? <div className="flex min-h-11 flex-wrap items-center justify-end gap-x-4 gap-y-1 bg-[#17324d] px-4 py-2 text-sm text-white" aria-label="내 로그인 정보">
        <span className="min-w-0 break-all">{session.displayName}</span>
        <button type="button" onClick={() => { void browserSession.signOut(); }} className="inline-flex min-h-8 items-center gap-2 rounded px-2 hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">
          <LogOut size={15} aria-hidden="true" />로그아웃
        </button>
      </div> : null}
      <Suspense fallback={<p role="status" className="p-8">업무 화면을 준비하고 있어요…</p>}>{children}</Suspense>
    </FoundryLiteProvider>;
  }

  const isChecking = session.status === "checking";
  const signIn = async () => {
    setIsSigningIn(true);
    try { await browserSession.signIn(); } finally { setIsSigningIn(false); }
  };
  return <main className="flex min-h-dvh flex-col bg-[#f3f7fa] text-[#263647]" style={{ fontFamily: '"Apple SD Gothic Neo", "Noto Sans KR", sans-serif' }}>
    <header className="flex items-center gap-3 px-6 py-6 sm:px-10">
      <ShieldCheck className="text-[#0e766e]" size={28} aria-hidden="true" />
      <span className="text-base font-semibold">비공개 업무 공간</span>
    </header>
    <section className="mx-auto flex w-full max-w-lg flex-1 flex-col justify-center px-6 pb-20 sm:px-10" aria-labelledby="login-title" aria-busy={isChecking || isSigningIn}>
      <LockKeyhole className="mb-6 text-[#0e766e]" size={34} strokeWidth={1.5} aria-hidden="true" />
      <h1 id="login-title" className="text-3xl leading-tight font-semibold tracking-tight text-[#17324d] [word-break:keep-all]">
        {isChecking ? "안전한 연결을 확인하고 있어요" : "로그인하고 업무를 이어가세요"}
      </h1>
      <p className="mt-5 text-base leading-7 text-[#607387]">초대받은 계정으로만 이용할 수 있습니다. 로그인 후에는 보려던 업무 화면으로 돌아옵니다.</p>
      {session.message ? <p role="alert" className="mt-6 border-l-3 border-[#0e766e] pl-4 text-sm leading-6">{session.message}</p> : null}
      {isChecking ? <p role="status" className="mt-8 text-sm text-[#607387]">로그인 상태 확인 중…</p> : session.canSignIn ?
        <button type="button" disabled={isSigningIn} onClick={() => { void signIn(); }} className="mt-8 min-h-12 w-full rounded-md bg-[#0e766e] px-5 py-3 text-base font-semibold text-white hover:bg-[#095c56] focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-[#0e766e] disabled:opacity-60">
          {isSigningIn ? "로그인 페이지 여는 중…" : session.status === "error" ? "초대받은 계정으로 로그인" : "로그인"}
        </button> : <button type="button" onClick={() => window.location.reload()} className="mt-8 min-h-12 rounded-md border border-[#607387] px-5 py-3 font-semibold focus-visible:outline-2 focus-visible:outline-offset-4">연결 다시 확인</button>}
      <p className="mt-6 text-sm leading-6 text-[#607387]">비밀번호는 안전한 로그인 페이지에서만 입력하세요. 대화창에 보내지 않아도 됩니다.</p>
      {session.status === "error" && session.canSignIn ? <button type="button" onClick={() => window.location.reload()} className="mt-4 min-h-11 self-start rounded text-sm underline underline-offset-4 focus-visible:outline-2 focus-visible:outline-offset-2">연결 다시 확인</button> : null}
    </section>
  </main>;
}
