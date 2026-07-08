/**
 * 최근 방문 화면/리소스 기록 — 로컬 저장 (서버 persistence future).
 * 백엔드 recent API가 없어 localStorage에만 보관한다.
 */

const STORAGE_KEY = "foundry.recent-visits.v1";
const MAX_ENTRIES = 8;

export interface RecentVisit {
  screenId: string;
  title: string;
  route: string;
  visitedAt: string;
}

function isRecentVisit(value: unknown): value is RecentVisit {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.screenId === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.route === "string" &&
    typeof candidate.visitedAt === "string"
  );
}

export function getRecentVisits(): RecentVisit[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isRecentVisit).slice(0, MAX_ENTRIES);
  } catch {
    return [];
  }
}

export function createRecentVisit(visit: Omit<RecentVisit, "visitedAt">): void {
  try {
    const next = [
      { ...visit, visitedAt: new Date().toISOString() },
      ...getRecentVisits().filter((entry) => entry.route !== visit.route),
    ].slice(0, MAX_ENTRIES);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // localStorage 접근 불가(사생활 보호 모드 등)면 기록을 건너뛴다.
  }
}
