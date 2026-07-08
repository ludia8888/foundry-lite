import { useCallback, useReducer } from "react";

import type { AppDefinition } from "../lib/app-model";

/**
 * 앱 정의 편집 히스토리(undo/redo).
 * 모든 편집이 불변 AppDefinition을 만드므로, 이전 스냅샷 스택만 유지하면 된다.
 * 단일 리듀서로 present/past/future를 조율해 여러 setter 간 경쟁을 피한다.
 */
type HistoryState = {
  present: AppDefinition;
  past: AppDefinition[];
  future: AppDefinition[];
};

type HistoryAction =
  | { type: "edit"; next: AppDefinition } // 사용자 편집: present를 past로 밀고 future 비움
  | { type: "set"; next: AppDefinition } // 외부 반영(저장 버전 부여): present만 교체, 히스토리 유지
  | { type: "reset"; next: AppDefinition } // 새 기준(서버 로드): 히스토리 초기화
  | { type: "undo" }
  | { type: "redo" };

const HISTORY_LIMIT = 50;

function historyReducer(
  state: HistoryState,
  action: HistoryAction,
): HistoryState {
  switch (action.type) {
    case "edit":
      return {
        present: action.next,
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        future: [],
      };
    case "set":
      return { ...state, present: action.next };
    case "reset":
      return { present: action.next, past: [], future: [] };
    case "undo": {
      if (state.past.length === 0) return state;
      const previous = state.past[state.past.length - 1];
      return {
        present: previous,
        past: state.past.slice(0, -1),
        future: [state.present, ...state.future].slice(0, HISTORY_LIMIT),
      };
    }
    case "redo": {
      if (state.future.length === 0) return state;
      const [next, ...rest] = state.future;
      return {
        present: next,
        past: [...state.past, state.present].slice(-HISTORY_LIMIT),
        future: rest,
      };
    }
    default:
      return state;
  }
}

export type DefinitionHistory = {
  definition: AppDefinition;
  canUndo: boolean;
  canRedo: boolean;
  /** 사용자 편집: 히스토리에 기록. */
  edit: (next: AppDefinition) => void;
  /** 외부 반영(저장): present만 교체하고 히스토리 유지. */
  set: (next: AppDefinition) => void;
  /** 새 기준(서버 로드): 히스토리 초기화. */
  reset: (next: AppDefinition) => void;
  undo: () => void;
  redo: () => void;
};

export function useDefinitionHistory(
  init: () => AppDefinition,
): DefinitionHistory {
  const [state, dispatch] = useReducer(
    historyReducer,
    undefined,
    (): HistoryState => ({ present: init(), past: [], future: [] }),
  );

  return {
    definition: state.present,
    canUndo: state.past.length > 0,
    canRedo: state.future.length > 0,
    edit: useCallback(
      (next: AppDefinition) => dispatch({ type: "edit", next }),
      [],
    ),
    set: useCallback(
      (next: AppDefinition) => dispatch({ type: "set", next }),
      [],
    ),
    reset: useCallback(
      (next: AppDefinition) => dispatch({ type: "reset", next }),
      [],
    ),
    undo: useCallback(() => dispatch({ type: "undo" }), []),
    redo: useCallback(() => dispatch({ type: "redo" }), []),
  };
}
