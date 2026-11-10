import { createPortal } from "react-dom";
import { getActiveModal } from "../components/Modal";
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  useEffect,
} from "react";
import type { ReactNode } from "react";

import { css, cx } from "styled-system/css";

export type ToastTone = "success" | "warn" | "danger" | "neutral";

/** トースト本文のベーススタイル（トークン経由）。 */
const TOAST_ITEM_BASE = css({
  display: "inline-flex",
  gap: "1",
  fontSize: "sm",
  fontWeight: "500",
  lineHeight: "1.5",
  px: "3",
  py: "2",
  borderRadius: "sm",
  borderWidth: "1px",
  borderStyle: "solid",
  whiteSpace: "normal",
  overflowWrap: "anywhere",
  maxW: "100%",
  textAlign: "left",
  alignItems: "flex-start",
});

/** tone ごとの色クラス（badge recipe と同一パレット、インラインは使わない）。 */
const TONE_CLASS: Record<ToastTone, string> = {
  neutral: css({ bg: "gray.50", color: "text.muted", borderColor: "border" }),
  success: css({
    bg: "success.soft",
    color: "success.text",
    borderColor: "success",
  }),
  warn: css({ bg: "warn.soft", color: "warn.text", borderColor: "warn" }),
  danger: css({
    bg: "danger.soft",
    color: "danger.text",
    borderColor: "danger",
  }),
};

export interface Toast {
  id: number;
  message: string;
  tone: ToastTone;
}

interface ToastContextValue {
  /** トーストを表示する。tone 省略時は neutral。 */
  show: (message: string, tone?: ToastTone) => void;
  success: (message: string) => void;
  error: (message: string) => void;
  warn: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const DISMISS_MS = 10000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);
  const viewport = useRef<HTMLDivElement>(null);
  const [host, setHost] = useState<HTMLElement>(document.body);
  useEffect(() => {
    const syncHost = () => setHost(getActiveModal() ?? document.body);
    syncHost();
    document.addEventListener("millicall:modalchange", syncHost);
    return () =>
      document.removeEventListener("millicall:modalchange", syncHost);
  }, []);
  useEffect(() => {
    const element = viewport.current;
    if (!element?.isConnected) return;
    element.setAttribute("popover", "manual");
    element.showPopover();
  }, [host]);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, tone: ToastTone = "neutral") => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, message, tone }]);
      if (tone === "success" || tone === "neutral")
        window.setTimeout(() => dismiss(id), DISMISS_MS);
    },
    [dismiss],
  );

  const value = useMemo<ToastContextValue>(
    () => ({
      show,
      success: (m) => show(m, "success"),
      error: (m) => show(m, "danger"),
      warn: (m) => show(m, "warn"),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {createPortal(
        <div
          ref={viewport}
          style={{ bottom: host === document.body ? 16 : 80 }}
          role="region"
          aria-label="通知"
          aria-live="polite"
          aria-relevant="additions text"
          className={css({
            position: "fixed",
            inset: "auto",
            margin: 0,
            border: 0,
            p: 0,
            bg: "transparent",
            pointerEvents: "none",
            maxWidth: "calc(100vw - 32px)",
            width: "360px",
            bottom: "4",
            right: "4",
            display: "flex",
            flexDirection: "column",
            gap: "2",
            zIndex: 1000,
          })}
        >
          {toasts.map((t) => (
            <div
              key={t.id}
              role={t.tone === "danger" ? "alert" : "status"}
              className={cx(TOAST_ITEM_BASE, TONE_CLASS[t.tone])}
              style={{ pointerEvents: "auto" }}
            >
              <span style={{ flex: 1 }}>{t.message}</span>
              <button
                type="button"
                aria-label="通知を閉じる"
                onClick={() => dismiss(t.id)}
                style={{ flexShrink: 0, padding: 4, cursor: "pointer" }}
              >
                ×
              </button>
            </div>
          ))}
        </div>,
        host,
      )}
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx)
    throw new Error("useToast は ToastProvider の内側で使用してください");
  return ctx;
}
