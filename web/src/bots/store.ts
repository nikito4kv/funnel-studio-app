// Раздел «Боты»: открыт ли экран, какой бот выбран, и лёгкий индекс «@username → id, имя» для
// аватара в шапке и в диалоге запуска. Адрес экрана – #bots (или #bots=<id бота>).
import { create } from "zustand";
import { botsApi, type BotsList } from "./api";

export type BotsSelection = number | "add" | null;

interface IndexItem { id: number; first_name: string }

interface BotsState {
  open: boolean;
  selected: BotsSelection;
  list: BotsList | null;
  listErr: string | null;
  loading: boolean;
  /** username (в нижнем регистре) → бот Студии; null – ещё не загружен */
  index: Record<string, IndexItem> | null;
  /** версия аватаров: растёт после смены аватара, чтобы браузер не показывал старый из кэша */
  avatarRev: number;

  show: (sel?: BotsSelection) => void;
  hide: () => void;
  select: (sel: BotsSelection) => void;
  loadList: () => Promise<void>;
  loadIndex: () => Promise<void>;
  bumpAvatar: () => void;
}

function writeHash(open: boolean, sel: BotsSelection) {
  const h = new URLSearchParams(window.location.hash.slice(1));
  if (open) h.set("bots", typeof sel === "number" ? String(sel) : "");
  else h.delete("bots");
  const s = h.toString().replace(/^bots=$/, "bots").replace(/([&])bots=$/, "$1bots");
  history.replaceState(null, "", s ? "#" + s : window.location.pathname + window.location.search);
}

export function hashSelection(): BotsSelection | undefined {
  const h = new URLSearchParams(window.location.hash.slice(1));
  if (!h.has("bots")) return undefined;
  const v = Number(h.get("bots"));
  return Number.isFinite(v) && v > 0 ? v : null;
}

export const useBots = create<BotsState>((set, get) => ({
  open: false,
  selected: null,
  list: null,
  listErr: null,
  loading: false,
  index: null,
  avatarRev: 0,

  show: (sel) => {
    const selected = sel === undefined ? get().selected : sel;
    set({ open: true, selected });
    writeHash(true, selected);
    void get().loadList();
  },
  hide: () => {
    set({ open: false });
    writeHash(false, null);
    void get().loadIndex();
  },
  select: (sel) => {
    set({ selected: sel });
    writeHash(true, sel);
  },
  loadList: async () => {
    set({ loading: true });
    try {
      const list = await botsApi.list();
      const index: Record<string, IndexItem> = {};
      for (const b of list.bots) index[b.username.toLowerCase()] = { id: b.id, first_name: b.first_name };
      set({ list, listErr: null, index });
      const sel = get().selected;
      if (sel === null && list.bots.length) get().select(list.bots[0].id);
      if (sel === null && !list.bots.length) get().select("add");
    } catch (e) {
      set({ listErr: (e as Error).message });
    } finally {
      set({ loading: false });
    }
  },
  loadIndex: async () => {
    try {
      const { bots } = await botsApi.index();
      const index: Record<string, IndexItem> = {};
      for (const b of bots) index[b.username.toLowerCase()] = { id: b.id, first_name: b.first_name };
      set({ index });
    } catch {
      set({ index: {} });
    }
  },
  bumpAvatar: () => set({ avatarRev: get().avatarRev + 1 }),
}));
