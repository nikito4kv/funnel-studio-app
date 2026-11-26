// Иконки Студии – одна библиотека (lucide-react), одна толщина линии.
// Эмодзи в интерфейсе не используем: значки типов шагов, условий, меток – отсюда.
// Эмодзи внутри текстов сообщений бота – это контент людей, его не трогаем.
import {
  BadgeCheck, Ban, CircleCheckBig, CircleDot, CircleHelp, ClipboardList, Clock, CornerDownRight, CreditCard,
  Clock9, DoorOpen, EyeOff, Film, Gem, Gift, GraduationCap, Image, ImagePlay, Link, Mail, MessageCircle,
  MessageSquare, MessageSquareText, MousePointerClick, Newspaper, Phone, PhoneCall, Pin, Tv,
  type LucideIcon, type LucideProps,
} from "lucide-react";
import type { StepType } from "../model/types";

/** Толщина линии всех иконок интерфейса. */
export const STROKE = 1.75;

export const STEP_ICON: Record<StepType, LucideIcon> = {
  text: MessageSquareText,
  photo: Image,
  video_note: CircleDot,
  video: Film,
  animation: ImagePlay,
  rich: Newspaper,
  phone_request: Phone,
  email_request: Mail,
  jump: CornerDownRight,
  call: PhoneCall,
  sms: MessageSquare,
  air_pin: Pin,
};

export function StepIcon({ type, survey, ...p }: { type: StepType; survey?: boolean } & LucideProps) {
  const I = survey ? CircleHelp : STEP_ICON[type] ?? MessageSquareText;
  return <I size={16} strokeWidth={STROKE} aria-hidden {...p} />;
}

/** Подпись типа без значка в начале: «💬 Текст» → «Текст». Сами подписи общие с сервером
 *  (фразы «было / стало»), поэтому значок срезаем при показе, а не в словаре. */
export function plainLabel(label: string): string {
  return label.replace(/^[^\p{L}\p{N}«(]+/u, "");
}

/** Значки условий: в модели – эмодзи (они же в фразах сервера), на экране – иконки. */
const COND_ICON: Record<string, LucideIcon> = {
  "📺": Tv, "🙈": EyeOff, "💳": CreditCard, "💎": Gem, "🎓": GraduationCap, "🚪": DoorOpen,
  "📋": ClipboardList, "🎁": Gift, "🔗": Link, "👆": MousePointerClick, "💬": MessageCircle, "Ⓜ": BadgeCheck,
  "🕘": Clock9,
};

export function CondIcon({ emoji, ...p }: { emoji: string } & LucideProps) {
  const I = COND_ICON[emoji] ?? Ban;
  return <I size={12} strokeWidth={2} aria-hidden {...p} />;
}

export { CircleCheckBig, Clock };
