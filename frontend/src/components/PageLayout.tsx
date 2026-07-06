import type { ReactNode } from "react";

import { css } from "styled-system/css";

interface PageLayoutProps {
  /** ページ見出し（日本語）。 */
  title: string;
  /** 見出し下の補足説明（任意）。 */
  description?: string;
  /** 見出し右の操作領域（作成ボタンなど）。 */
  actions?: ReactNode;
  children: ReactNode;
}

/**
 * 各ページの共通レイアウト。見出し + 操作領域 + 本文。
 * 後続タスクの全画面がこれでラップされることを契約とする。
 */
export function PageLayout({ title, description, actions, children }: PageLayoutProps) {
  return (
    <div className={css({ display: "flex", flexDirection: "column", gap: "5" })}>
      <div
        className={css({
          display: "flex",
          alignItems: "flex-start",
          justifyContent: "space-between",
          gap: "4",
        })}
      >
        <div>
          <h1 className={css({ fontSize: "xl", fontWeight: "600", color: "text" })}>{title}</h1>
          {description ? (
            <p className={css({ fontSize: "md", color: "text.muted", mt: "1" })}>{description}</p>
          ) : null}
        </div>
        {actions ? <div className={css({ flexShrink: 0 })}>{actions}</div> : null}
      </div>
      <div>{children}</div>
    </div>
  );
}
