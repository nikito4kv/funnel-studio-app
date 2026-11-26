import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@xyflow/react/dist/style.css";
import "./design/tokens.css";
import "./design/base.css";
import "./styles.css";
import "./editor.css";
import "./stage2c.css";
import App from "./App";
import { useStudio } from "./store";

// Ручка для автоматизации (скриншоты, отладка из консоли): window.__studio.getState()
(window as unknown as { __studio: typeof useStudio }).__studio = useStudio;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
