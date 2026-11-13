import { useState } from "react";
import { Outlet } from "@tanstack/react-router";
import { css } from "styled-system/css";
import { button } from "styled-system/recipes";
import { Header } from "./Header";
import { SideNav } from "./SideNav";
import { Modal } from "../components/Modal";

export function AppShell({
  username,
  role,
}: {
  username?: string;
  role?: string;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div
      className={css({ display: "flex", height: "100dvh", overflow: "hidden" })}
    >
      <div
        className={css({
          flexShrink: 0,
          "@media (max-width: 860px)": { display: "none" },
        })}
      >
        <SideNav role={role} />
      </div>
      <div
        className={css({
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
        })}
      >
        <Header
          username={username}
          menuOpen={menuOpen}
          onToggleMenu={() => setMenuOpen((value) => !value)}
        />
        <main
          id="main-content"
          className={css({
            flex: 1,
            minHeight: 0,
            overflowY: "auto",
            p: "6",
            "@media (max-width: 860px)": { p: "4" },
          })}
        >
          <Outlet />
        </main>
      </div>
      <Modal
        open={menuOpen}
        title="メインメニュー"
        onClose={() => setMenuOpen(false)}
        side
      >
        <div
          className={css({
            display: "flex",
            justifyContent: "flex-end",
            p: "2",
          })}
        >
          <button
            type="button"
            onClick={() => setMenuOpen(false)}
            className={button({ variant: "secondary" })}
          >
            メニューを閉じる
          </button>
        </div>
        <SideNav role={role} mobile onNavigate={() => setMenuOpen(false)} />
      </Modal>
    </div>
  );
}
