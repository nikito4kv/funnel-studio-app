// Режим интерфейса «Постановщик / Разработчик» и первое знакомство (тур).
// Хранится в localStorage этого браузера; если хранилище недоступно (приватное окно,
// запрет сайта) – просто работаем с умолчаниями.
import { create } from "zustand";

export type UiMode = "simple" | "dev";

const MODE_KEY = "studio.mode";
const TOUR_KEY = "studio.tour.hidden";
const CARDS_KEY = "studio.cards.simplifyFar";
const MINIMAP_KEY = "studio.minimap";

function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* хранилище недоступно – настройка живёт до перезагрузки */
  }
}

export function initialMode(stored: string | null): UiMode {
  return stored === "dev" ? "dev" : "simple";
}

/** Автоматизация (Playwright и т.п.) тура не видит – он перекрыл бы клики. */
function automated(): boolean {
  try {
    return !!navigator.webdriver || new URLSearchParams(window.location.search).has("e2e");
  } catch {
    return false;
  }
}

interface UiState {
  mode: UiMode;
  tour: boolean;
  searchOpen: boolean;
  /** Мини-карта холста (по умолчанию спрятана: закрывала карточки и точки выхода). */
  minimap: boolean;
  setMinimap: (on: boolean) => void;
  /** Шпаргалка горячих клавиш («?»). */
  shortcuts: boolean;
  setShortcuts: (open: boolean) => void;
  /** Упрощать карточки при отдалении (значок + название + время). По умолчанию выключено:
   *  карточка на любом масштабе целиком, как на доске Miro. */
  simplifyFar: boolean;
  setSimplifyFar: (on: boolean) => void;
  setMode: (m: UiMode) => void;
  closeTour: (forever: boolean) => void;
  openTour: () => void;
  setSearch: (open: boolean) => void;
}

export const useUi = create<UiState>((set) => ({
  mode: typeof window === "undefined" ? "simple" : initialMode(read(MODE_KEY)),
  tour: typeof window === "undefined" ? false : !automated() && read(TOUR_KEY) !== "1",
  searchOpen: false,
  shortcuts: false,
  minimap: typeof window === "undefined" ? false : read(MINIMAP_KEY) === "1",
  setMinimap: (on) => {
    write(MINIMAP_KEY, on ? "1" : "0");
    set({ minimap: on });
  },
  setShortcuts: (shortcuts) => set({ shortcuts }),
  simplifyFar: typeof window === "undefined" ? false : read(CARDS_KEY) === "1",
  setSimplifyFar: (on) => {
    write(CARDS_KEY, on ? "1" : "0");
    set({ simplifyFar: on });
  },
  setMode: (mode) => {
    write(MODE_KEY, mode);
    set({ mode });
  },
  closeTour: (forever) => {
    if (forever) write(TOUR_KEY, "1");
    set({ tour: false });
  },
  openTour: () => set({ tour: true }),
  setSearch: (searchOpen) => set({ searchOpen }),
}));

export const useSimple = (): boolean => useUi((s) => s.mode === "simple");
