// Аватар бота: картинка из Telegram (через сервер Студии – адрес с токеном в браузер не попадает)
// или кружок с первой буквой имени, если аватара нет или бот не добавлен в раздел «Боты».
import { useEffect, useState } from "react";
import { useBots } from "./store";
import { initial } from "./text";

export function BotAvatar({ id, username, name, size = 32 }: {
  id?: number | null; username?: string; name?: string; size?: number;
}) {
  const index = useBots((s) => s.index);
  const rev = useBots((s) => s.avatarRev);
  const loadIndex = useBots((s) => s.loadIndex);
  const known = id ?? (username ? index?.[username.toLowerCase()]?.id : undefined) ?? null;
  const label = name || (username ? index?.[username.toLowerCase()]?.first_name : "") || "";
  const [broken, setBroken] = useState(false);
  useEffect(() => { if (id == null && username && index === null) void loadIndex(); }, [id, username, index, loadIndex]);
  useEffect(() => setBroken(false), [known, rev]);
  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) };
  if (known !== null && !broken) {
    return <img className="bot-ava" style={style} alt="" src={`api/bots/${known}/avatar${rev ? `?v=${rev}` : ""}`}
      onError={() => setBroken(true)} data-testid="bot-avatar" />;
  }
  return <span className="bot-ava bot-ava-empty" style={style} aria-hidden>{initial(label, username ?? "")}</span>;
}

/** Бот словами: аватар, имя из Telegram и @username (имя – если бот есть в разделе «Боты»). */
export function BotChip({ username, onOpen }: { username: string; onOpen?: () => void }) {
  const index = useBots((s) => s.index);
  const item = index?.[username.toLowerCase()];
  return (
    <span className="bot-chip" data-testid="bot-chip">
      <BotAvatar username={username} size={24} />
      <span className="bot-chip-text">
        {item?.first_name && <b>{item.first_name}</b>}
        <span className="muted">@{username}</span>
      </span>
      {onOpen && item && (
        <button type="button" className="linkish" onClick={onOpen} data-testid="bot-chip-open">профиль бота</button>
      )}
    </span>
  );
}
