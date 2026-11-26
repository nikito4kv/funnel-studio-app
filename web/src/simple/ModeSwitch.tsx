// Переключатель «Постановщик / Разработчик» – в строке состояния внизу: это настройка вида,
// к ней возвращаются редко, в шапке она занимала место главных действий.
import { useUi, type UiMode } from "./mode";

export function ModeSwitch() {
  const mode = useUi((s) => s.mode);
  const setMode = useUi((s) => s.setMode);
  const opts: { value: UiMode; label: string; tip: string }[] = [
    { value: "simple", label: "Постановщик", tip: "Человеческие слова: без технических имён, HTML и {скобок}" },
    { value: "dev", label: "Разработчик", tip: "Всё как в yaml: имена шагов, плейсхолдеры, HTML, расширенные настройки" },
  ];
  return (
    <div className="seg seg-sm mode-switch" data-testid="mode-switch">
      {opts.map((o) => (
        <button type="button" key={o.value} data-tip={o.tip} data-tip-side="top" className={mode === o.value ? "seg-on" : ""} onClick={() => setMode(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}
