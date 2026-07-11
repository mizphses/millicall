import { Link, useRouterState } from "@tanstack/react-router";

import { css } from "styled-system/css";

import { activeNavPath, navSectionsForRole } from "./nav";

/**
 * サイドナビ。role に応じて表示項目をフィルタする。
 * role 未指定時は安全側（一般ユーザー相当 = 管理系項目なし）で描画する。
 */
export function SideNav({ role }: { role?: string }) {
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  // 入れ子パス（/network と /network/remote）でも 1 件だけを強調する
  const activePath = activeNavPath(pathname);
  const sections = navSectionsForRole(role ?? "user");

  return (
    <nav
      className={css({
        width: "sidebar",
        flexShrink: 0,
        height: "100%",
        bg: "white",
        borderRightWidth: "1px",
        borderRightStyle: "solid",
        borderRightColor: "border",
        display: "flex",
        flexDirection: "column",
      })}
    >
      <div
        className={css({
          h: "header",
          display: "flex",
          alignItems: "center",
          px: "5",
          fontWeight: "600",
          fontSize: "lg",
          color: "accent.text",
          borderBottomWidth: "1px",
          borderBottomStyle: "solid",
          borderBottomColor: "border",
        })}
      >
        millicall
      </div>
      <div className={css({ p: "2", overflowY: "auto" })}>
        {sections.map((section, i) => (
          <div key={section.title ?? `section-${i}`}>
            {section.title ? (
              <div
                className={css({
                  fontSize: "xs",
                  fontWeight: "600",
                  color: "text.subtle",
                  px: "3",
                  mt: "4",
                  mb: "1",
                })}
              >
                {section.title}
              </div>
            ) : null}
            <ul className={css({ listStyle: "none", m: 0, p: 0, display: "flex", flexDirection: "column", gap: "1" })}>
              {section.items.map((item) => {
                const active = item.path === activePath;
                return (
                  <li key={item.path}>
                    <Link
                      to={item.path}
                      className={css({
                        display: "flex",
                        alignItems: "center",
                        gap: "3",
                        px: "3",
                        py: "2",
                        borderRadius: "md",
                        fontSize: "md",
                        color: active ? "accent.text" : "text.muted",
                        bg: active ? "accent.soft" : "transparent",
                        fontWeight: active ? "600" : "400",
                        textDecoration: "none",
                        _hover: { bg: active ? "accent.soft" : "gray.50", color: active ? "accent.text" : "text" },
                      })}
                    >
                      <span className={css({ display: "flex", alignItems: "center", width: "20px" })} aria-hidden>
                        {item.icon}
                      </span>
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </nav>
  );
}
