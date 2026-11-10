import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useState,
  type ReactNode,
  type ReactElement,
} from "react";
import { X } from "lucide-react";
import { css } from "styled-system/css";
import { button } from "styled-system/recipes";
import { Modal } from "./Modal";
import { ConfirmDialog } from "./ConfirmDialog";
import { UnsavedChangesGuard } from "./UnsavedChangesGuard";

interface SlidePanelProps {
  open: boolean;
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  busy?: boolean;
  dirty?: boolean;
}

function guardCancel(
  children: ReactNode,
  close: () => void,
  busy: boolean,
): ReactNode {
  return Children.map(children, (child) => {
    if (!isValidElement(child)) return child;
    const element = child as ReactElement<{
      children?: ReactNode;
      onClick?: () => void;
      disabled?: boolean;
    }>;
    if (
      element.type === "button" &&
      typeof element.props.children === "string" &&
      /^(キャンセル|閉じる)$/.test(element.props.children.trim())
    ) {
      return cloneElement(element, {
        onClick: close,
        disabled: busy || element.props.disabled,
      });
    }
    return element.props.children
      ? cloneElement(
          element,
          {},
          guardCancel(element.props.children, close, busy),
        )
      : element;
  });
}
export function SlidePanel({
  open,
  title,
  onClose,
  children,
  footer,
  busy = false,
  dirty,
}: SlidePanelProps) {
  const [touched, setTouched] = useState(false);
  const [confirm, setConfirm] = useState(false);
  useEffect(() => {
    if (!open) {
      setTouched(false);
      setConfirm(false);
    }
  }, [open]);
  const changed = dirty ?? touched;
  const requestClose = () => {
    if (!busy) {
      if (changed) setConfirm(true);
      else onClose();
    }
  };
  return (
    <>
      <Modal open={open} title={title} onClose={requestClose} busy={busy} side>
        <div
          className={css({
            height: "100%",
            display: "flex",
            flexDirection: "column",
          })}
          onChangeCapture={() => setTouched(true)}
        >
          <header
            className={css({
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "3",
              px: "5",
              py: "4",
              flexShrink: 0,
              borderBottom: "1px solid",
              borderColor: "border",
            })}
          >
            <h2 className={css({ fontSize: "lg", fontWeight: "600" })}>
              {title}
            </h2>
            <button
              type="button"
              aria-label="閉じる"
              className={button({ variant: "ghost", size: "sm" })}
              onClick={requestClose}
              disabled={busy}
            >
              <X size={16} />
            </button>
          </header>
          <div
            className={css({
              flex: 1,
              minHeight: 0,
              overflowY: "auto",
              p: "5",
            })}
          >
            <fieldset
              disabled={busy}
              className={css({ border: 0, p: 0, m: 0, minWidth: 0 })}
            >
              {children}
            </fieldset>
          </div>
          {footer && (
            <footer
              className={css({
                display: "flex",
                flexWrap: "wrap",
                justifyContent: "flex-end",
                gap: "2",
                px: "5",
                py: "4",
                flexShrink: 0,
                borderTop: "1px solid",
                borderColor: "border",
              })}
            >
              {guardCancel(footer, requestClose, busy)}
            </footer>
          )}
        </div>
      </Modal>
      <UnsavedChangesGuard dirty={open && changed} busy={open && busy} />
      <ConfirmDialog
        open={open && confirm}
        title="変更を破棄しますか？"
        message="入力した未保存の変更は失われます。"
        confirmLabel="変更を破棄"
        cancelLabel="編集を続ける"
        destructive
        busy={busy}
        onConfirm={() => {
          setConfirm(false);
          setTouched(false);
          onClose();
        }}
        onCancel={() => setConfirm(false)}
      />
    </>
  );
}
