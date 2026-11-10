import type { ErrorComponentProps } from "@tanstack/react-router";

import { css } from "styled-system/css";
import { button } from "styled-system/recipes";

/**
 * 動的 import の失敗（デプロイ直後の古い index.html、ネットワーク断）を判定する。
 * ブラウザごとにメッセージが異なるため文言で判定する。
 */
export function isChunkLoadError(error: unknown): boolean {
  if (!(error instanceof Error)) return false;
  return /dynamically imported module|Importing a module script failed|Unable to preload CSS/i.test(
    error.message,
  );
}

/** ルートの遅延チャンク取得中に表示するプレースホルダ。 */
export function RoutePending() {
  return (
    <div
      role="status"
      aria-live="polite"
      className={css({
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        minH: "50dvh",
        color: "text.muted",
        fontSize: "md",
      })}
    >
      読み込み中...
    </div>
  );
}

/**
 * ルートのロード失敗（チャンク取得失敗・loader/beforeLoad の例外）時の表示。
 * 日本語メッセージと再読み込み導線を出す。
 */
export function RouteError({ error }: ErrorComponentProps) {
  const chunkFailed = isChunkLoadError(error);
  return (
    <div
      role="alert"
      className={css({
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        minH: "50dvh",
        gap: "4",
        px: "6",
        textAlign: "center",
      })}
    >
      <h1 className={css({ fontSize: "2xl", fontWeight: "700", color: "text" })}>
        {chunkFailed ? "画面の読み込みに失敗しました" : "予期せぬエラーが発生しました"}
      </h1>
      <p className={css({ color: "text.muted", maxW: "md" })}>
        {chunkFailed
          ? "ネットワークの接続を確認するか、システムが更新された可能性があります。ページを再読み込みしてください。"
          : "ページを再読み込みしてもう一度お試しください。"}
      </p>
      <button
        type="button"
        className={button({ variant: "primary" })}
        onClick={() => window.location.reload()}
      >
        ページを再読み込み
      </button>
    </div>
  );
}
