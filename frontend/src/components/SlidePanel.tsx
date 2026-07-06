import { useEffect, type ReactNode } from "react";
import { X } from "lucide-react";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

interface SlidePanelProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** フッタ（保存/キャンセルなどのアクション）。 */
  footer?: ReactNode;
}

/**
 * 右からスライドインするパネル。作成/編集フォームの器。
 * 一覧の文脈を保つためモーダルではなくスライドを採用する（設計方針）。
 * 後続タスクは <SlidePanel open title onClose footer><form/></SlidePanel> で使う。
 */
export function SlidePanel({ open, title, onClose, children, footer }: SlidePanelProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div
      className={css({
        position: "fixed",
        inset: 0,
        zIndex: 900,
        display: "flex",
        justifyContent: "flex-end",
      })}
    >
      <div
        aria-hidden
        onClick={onClose}
        className={css({ position: "absolute", inset: 0, bg: "gray.900", opacity: 0.2 })}
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={css({
          position: "relative",
          width: "panel",
          maxWidth: "100%",
          height: "100%",
          bg: "white",
          borderLeftWidth: "1px",
          borderLeftStyle: "solid",
          borderLeftColor: "border",
          display: "flex",
          flexDirection: "column",
        })}
      >
        <header
          className={css({
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            px: "5",
            h: "header",
            flexShrink: 0,
            borderBottomWidth: "1px",
            borderBottomStyle: "solid",
            borderBottomColor: "border",
          })}
        >
          <h2 className={css({ fontSize: "lg", fontWeight: "600" })}>{title}</h2>
          <button type="button" aria-label="閉じる" className={button({ variant: "ghost", size: "sm" })} onClick={onClose}>
            <X size={16} />
          </button>
        </header>
        <div className={css({ flex: 1, overflowY: "auto", p: "5" })}>{children}</div>
        {footer ? (
          <footer
            className={css({
              display: "flex",
              justifyContent: "flex-end",
              gap: "2",
              px: "5",
              py: "4",
              flexShrink: 0,
              borderTopWidth: "1px",
              borderTopStyle: "solid",
              borderTopColor: "border",
            })}
          >
            {footer}
          </footer>
        ) : null}
      </aside>
    </div>
  );
}
