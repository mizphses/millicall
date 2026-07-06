import { Outlet } from "@tanstack/react-router";

import { css } from "styled-system/css";

import { Header } from "./Header";
import { SideNav } from "./SideNav";

/**
 * 認証済み領域のシェル。サイドナビ + ヘッダ + 本文（Outlet）。
 * username は認証ガードの loader から context 経由で渡す。
 */
export function AppShell({ username }: { username?: string }) {
  return (
    <div className={css({ display: "flex", height: "100vh", overflow: "hidden" })}>
      <SideNav />
      <div className={css({ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 })}>
        <Header username={username} />
        <main className={css({ flex: 1, overflowY: "auto", p: "6" })}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
