import { useRouter } from "@tanstack/react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { LogOut, Menu, X } from "lucide-react";
import { css } from "styled-system/css";
import { button } from "styled-system/recipes";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { getDirtyState } from "../hooks/useDirtyState";
import { useToast } from "../toast/ToastProvider";

export function Header({ username, menuOpen = false, onToggleMenu }: {
  username?: string;
  menuOpen?: boolean;
  onToggleMenu?: () => void;
}) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const [confirmLogout, setConfirmLogout] = useState(false);
  function requestLogout() {
    const state = getDirtyState();
    if (state.busy) { toast.warn("処理が完了してからログアウトしてください"); return; }
    if (state.dirty) { setConfirmLogout(true); return; }
    void handleLogout();
  }
  async function handleLogout() {
    setBusy(true);
    try {
      const { response, error } = await api.POST("/api/auth/logout");
      if (!response.ok || error) throw new Error("logout failed");
      queryClient.clear();
      toast.success("ログアウトしました");
      setConfirmLogout(false);
      await router.navigate({ to: "/login", ignoreBlocker: true });
    } catch {
      toast.error("ログアウトに失敗しました。接続を確認して、もう一度お試しください。");
    } finally {
      setBusy(false);
    }
  }
  return (
    <header className={css({ h: "header", flexShrink: 0, display: "flex", alignItems: "center", justifyContent: "space-between", gap: "2", px: "6", bg: "white", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "border", "@media (max-width: 860px)": { px: "3" } })}>
      <div className={css({ display: "flex", alignItems: "center", gap: "2", minW: 0 })}>
        {onToggleMenu && <button type="button" aria-label={menuOpen ? "メニューを閉じる" : "メニューを開く"} aria-expanded={menuOpen} aria-controls="primary-navigation" onClick={onToggleMenu} className={css({ display: "none", "@media (max-width: 860px)": { display: "inline-flex", alignItems: "center", justifyContent: "center", width: "44px", height: "44px" } })}>{menuOpen ? <X size={22} /> : <Menu size={22} />}</button>}
        <span className={css({ fontSize: "sm", color: "text.muted" })}>管理コンソール</span>
      </div>
      <div className={css({ display: "flex", alignItems: "center", gap: "3", minW: 0 })}>
        {username && <span title={username} className={css({ fontSize: "sm", color: "text.muted", maxW: "160px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", "@media (max-width: 479px)": { maxW: "88px" } })}>{username}</span>}
        <button type="button" className={button({ variant: "secondary", size: "sm" })} disabled={busy} onClick={requestLogout}><LogOut size={16} />{busy ? "ログアウト中…" : "ログアウト"}</button>
      </div>
      <ConfirmDialog open={confirmLogout} title="変更を破棄してログアウトしますか？" message="未保存の変更は失われます。編集を続ける場合はキャンセルしてください。" confirmLabel="破棄してログアウト" destructive busy={busy} onConfirm={() => void handleLogout()} onCancel={() => setConfirmLogout(false)} />
    </header>
  );
}
