import { css } from "styled-system/css";
import { button } from "styled-system/recipes";
import { Modal } from "./Modal";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}
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
  return (
    <Modal open={open} title={title} onClose={onCancel} busy={busy} alert>
      <div className={css({ p: "5" })}>
        <h2 className={css({ fontSize: "lg", fontWeight: "600", mb: "2" })}>
          {title}
        </h2>
        <p
          className={css({
            color: "text.muted",
            mb: "5",
            overflowWrap: "anywhere",
          })}
        >
          {message}
        </p>
        <div
          className={css({
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "flex-end",
            gap: "2",
          })}
        >
          <button
            autoFocus
            type="button"
            className={button({ variant: "secondary" })}
            onClick={onCancel}
            disabled={busy}
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            className={button({ variant: destructive ? "danger" : "primary" })}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? "処理中…" : confirmLabel}
          </button>
        </div>
      </div>
    </Modal>
  );
}
