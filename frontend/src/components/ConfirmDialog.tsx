import { useEffect } from "react";

import { css, cx } from "styled-system/css";
import { button, panel } from "styled-system/recipes";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  /** 破壊的操作なら true で確認ボタンを danger 表示にする。 */
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * 確認ダイアログ。削除など破壊的操作の前に使う（契約）。
 */
export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "実行",
  cancelLabel = "キャンセル",
  destructive = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  if (!open) return null;
  return (
    <div
      className={css({
        position: "fixed",
        inset: 0,
        zIndex: 950,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: "4",
      })}
    >
      <div
        aria-hidden
        onClick={onCancel}
        className={css({ position: "absolute", inset: 0, bg: "gray.900", opacity: 0.2 })}
      />
      <div
        role="alertdialog"
        aria-modal="true"
        aria-label={title}
        className={cx(panel(), css({ position: "relative", w: "dialog", maxW: "100%", p: "5" }))}
      >
        <h2 className={css({ fontSize: "lg", fontWeight: "600", mb: "2" })}>{title}</h2>
        <p className={css({ fontSize: "md", color: "text.muted", mb: "5" })}>{message}</p>
        <div className={css({ display: "flex", justifyContent: "flex-end", gap: "2" })}>
          <button type="button" className={button({ variant: "secondary" })} onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button
            type="button"
            className={button({ variant: destructive ? "danger" : "primary" })}
            onClick={onConfirm}
            disabled={busy}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
