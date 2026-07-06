import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { css, cx } from "styled-system/css";

export type ToastTone = "success" | "warn" | "danger" | "neutral";

/** トースト本文のベーススタイル（トークン経由）。 */
const TOAST_ITEM_BASE = css({
  display: "inline-flex",
  alignItems: "center",
  gap: "1",
  fontSize: "sm",
  fontWeight: "500",
  lineHeight: "1",
  px: "3",
  py: "2",
  borderRadius: "sm",
  borderWidth: "1px",
  borderStyle: "solid",
  whiteSpace: "nowrap",
  maxW: "toast",
  textAlign: "left",
  cursor: "pointer",
});

/** tone ごとの色クラス（badge recipe と同一パレット、インラインは使わない）。 */
const TONE_CLASS: Record<ToastTone, string> = {
  neutral: css({ bg: "gray.50", color: "text.muted", borderColor: "border" }),
  success: css({ bg: "success.soft", color: "success.text", borderColor: "success" }),
  warn: css({ bg: "warn.soft", color: "warn.text", borderColor: "warn" }),
  danger: css({ bg: "danger.soft", color: "danger.text", borderColor: "danger" }),
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

const DISMISS_MS = 4000;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (message: string, tone: ToastTone = "neutral") => {
      const id = nextId.current++;
      setToasts((prev) => [...prev, { id, message, tone }]);
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
      <div
        className={css({
          position: "fixed",
          bottom: "6",
          right: "6",
          display: "flex",
          flexDirection: "column",
          gap: "2",
          zIndex: 1000,
        })}
      >
        {toasts.map((t) => (
          <button
            type="button"
            key={t.id}
            onClick={() => dismiss(t.id)}
            className={cx(TOAST_ITEM_BASE, TONE_CLASS[t.tone])}
          >
            {t.message}
          </button>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast は ToastProvider の内側で使用してください");
  return ctx;
}
