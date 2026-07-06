import { useRouter, useRouterState } from "@tanstack/react-router";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

import { api } from "../api/client";
import { useToast } from "../toast/ToastProvider";
import { titleForPath } from "./nav";

export function Header({ username }: { username?: string }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const router = useRouter();
  const toast = useToast();

  async function handleLogout() {
    await api.POST("/api/auth/logout");
    toast.success("ログアウトしました");
    router.navigate({ to: "/login" });
  }

  return (
    <header
      className={css({
        h: "header",
        flexShrink: 0,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        px: "6",
        bg: "white",
        borderBottomWidth: "1px",
        borderBottomStyle: "solid",
        borderBottomColor: "border",
      })}
    >
      <h1 className={css({ fontSize: "lg", fontWeight: "600" })}>{titleForPath(pathname)}</h1>
      <div className={css({ display: "flex", alignItems: "center", gap: "4" })}>
        {username ? (
          <span className={css({ fontSize: "md", color: "text.muted" })}>{username}</span>
        ) : null}
        <button type="button" className={button({ variant: "secondary", size: "sm" })} onClick={handleLogout}>
          ログアウト
        </button>
      </div>
    </header>
  );
}
