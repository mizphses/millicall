import { useEffect } from "react";
import { useDirtyState } from "../hooks/useDirtyState";
import { useBlocker } from "@tanstack/react-router";
import { ConfirmDialog } from "./ConfirmDialog";

export function UnsavedChangesGuard({
  dirty,
  busy = false,
}: {
  dirty: boolean;
  busy?: boolean;
}) {
  useDirtyState(dirty, busy);
  const blocker = useBlocker({
    shouldBlockFn: () => dirty || busy,
    enableBeforeUnload: dirty || busy,
    withResolver: true,
  });
  useEffect(() => {
    if (blocker.status === "blocked" && !dirty && !busy) blocker.proceed();
  }, [blocker, dirty, busy]);
  return (
    <ConfirmDialog
      open={blocker.status === "blocked"}
      title={
        busy ? "処理が完了するまでお待ちください" : "未保存の変更があります"
      }
      message={
        busy
          ? "保存中はこの画面から移動できません。"
          : "この画面を離れると、未保存の変更は破棄されます。"
      }
      confirmLabel="変更を破棄して移動"
      cancelLabel="編集を続ける"
      destructive
      busy={busy}
      onConfirm={() => blocker.proceed?.()}
      onCancel={() => blocker.reset?.()}
    />
  );
}
