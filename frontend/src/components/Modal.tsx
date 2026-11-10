import { useEffect, useId, useRef, type ReactNode } from "react";
import { css } from "styled-system/css";

const modalStack: HTMLDialogElement[] = [];
export function getActiveModal(): HTMLDialogElement | null {
  return modalStack.at(-1) ?? null;
}

export interface ModalProps {
  open: boolean;
  title: string;
  onClose: () => void;
  busy?: boolean;
  side?: boolean;
  alert?: boolean;
  children: ReactNode;
}

/** Native modal provides inert background, focus containment and nested-dialog support. */
export function Modal({
  open,
  title,
  onClose,
  busy = false,
  side = false,
  alert = false,
  children,
}: ModalProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const id = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog) return;
    const previous = document.activeElement as HTMLElement | null;
    dialog.showModal();
    modalStack.push(dialog);
    document.dispatchEvent(new Event("millicall:modalchange"));
    return () => {
      dialog.close();
      const index = modalStack.indexOf(dialog);
      if (index !== -1) modalStack.splice(index, 1);
      document.dispatchEvent(new Event("millicall:modalchange"));
      if (previous?.isConnected) previous.focus();
    };
  }, [open]);
  if (!open) return null;
  return (
    <dialog
      ref={ref}
      aria-label={title}
      aria-modal="true"
      aria-busy={busy || undefined}
      role={alert ? "alertdialog" : undefined}
      data-modal-id={id}
      onCancel={(event) => {
        event.preventDefault();
        if (!busy) onClose();
      }}
      onClick={(event) => {
        if (event.target !== event.currentTarget || busy) return;
        const rect = event.currentTarget.getBoundingClientRect();
        if (
          event.clientX < rect.left ||
          event.clientX > rect.right ||
          event.clientY < rect.top ||
          event.clientY > rect.bottom
        )
          onClose();
      }}
      className={css({
        bg: "white",
        color: "text",
        p: 0,
        border: "none",
        maxWidth: "100vw",
        maxHeight: "100dvh",
        overflow: "auto",
        "&::backdrop": { bg: "rgba(20,24,28,0.35)" },
      })}
      style={
        side
          ? {
              margin: "0 0 0 auto",
              width: "min(440px, 100vw)",
              height: "100dvh",
            }
          : {
              margin: "auto",
              width: "min(440px, calc(100vw - 32px))",
              borderRadius: 10,
            }
      }
    >
      {children}
    </dialog>
  );
}
