/**
 * ログインページ（Phase 6 T9b 更新）。
 *
 * - 通常ログイン: POST /api/auth/login → UserRead → ロール別の遷移先へ
 *   （admin はダッシュボード、user はアカウントページ）
 * - TOTP 有効ユーザー: 同エンドポイントが {totp_required: true, ticket} を返す
 *   → 2 段階目: TOTP コード（6 桁）入力 → POST /api/auth/login/totp → ロール別の遷移先へ
 *   → "リカバリコードを使う" トグルで code フィールドの制約を解除
 * - 401 → 誤りメッセージ、429 → ロックアウトメッセージ
 * - SAML SSO 有効時（GET /api/auth/config）: 「SSO でログイン」ボタンを表示し、
 *   /saml/login へフル遷移する（SPA ルーターを通さない）
 */

import { useRouter } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { css, cx } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api, ensureCsrfCookie } from "../api/client";
import { fetchLoginConfig, postLoginPath } from "../auth/auth";

type LoginStep =
  | { kind: "credentials" }
  | { kind: "totp"; ticket: string };

export function LoginPage() {
  const router = useRouter();

  // ─── ステップ共通状態 ───
  const [step, setStep] = useState<LoginStep>({ kind: "credentials" });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // ─── 認証情報ステップ ───
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  // ─── TOTP ステップ ───
  const [totpCode, setTotpCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);

  // ─── SAML SSO ───
  // 公開設定（未認証で取得可能）から SAML 有効フラグを読む。無効/取得失敗時はボタン非表示。
  const [samlEnabled, setSamlEnabled] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void fetchLoginConfig().then((cfg) => {
      if (!cancelled) setSamlEnabled(cfg.saml_enabled);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  // ─── Step 1: 認証情報送信 ───
  async function handleCredentials(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const { data, response } = await api.POST("/api/auth/login", {
        body: { username, password },
      });
      if (response.status === 401) {
        setError("ユーザー名またはパスワードが正しくありません");
        return;
      }
      if (response.status === 429) {
        setError("試行回数の上限に達しました。しばらくしてからお試しください");
        return;
      }
      if (!response.ok) {
        setError("ログインに失敗しました");
        return;
      }
      // TOTP 必要チェック: data の shape を確認する
      const body = data as
        | { totp_required?: boolean; ticket?: string; role?: string }
        | undefined;
      if (body?.totp_required && body.ticket) {
        setStep({ kind: "totp", ticket: body.ticket });
        setTotpCode("");
        setUseRecovery(false);
        return;
      }
      // 通常ログイン成功: ロールに応じた遷移先へ（user はアカウントページ）
      await ensureCsrfCookie();
      router.navigate({ to: postLoginPath(body?.role) });
    } finally {
      setBusy(false);
    }
  }

  // ─── Step 2: TOTP コード送信 ───
  async function handleTotp(e: React.FormEvent) {
    e.preventDefault();
    if (step.kind !== "totp") return;
    setError(null);
    setBusy(true);
    try {
      const { data, response } = await api.POST("/api/auth/login/totp", {
        body: { ticket: step.ticket, code: totpCode },
      });
      if (response.status === 401) {
        setError(
          useRecovery
            ? "リカバリコードが正しくありません"
            : "認証コードが正しくありません"
        );
        return;
      }
      if (response.status === 429) {
        setError("試行回数の上限に達しました。しばらくしてからお試しください");
        return;
      }
      if (!response.ok) {
        setError("認証に失敗しました");
        return;
      }
      // TOTP ログイン成功: ロールに応じた遷移先へ（user はアカウントページ）
      await ensureCsrfCookie();
      router.navigate({ to: postLoginPath(data?.role) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className={css({
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        p: "4",
      })}
    >
      {step.kind === "credentials" ? (
        <form
          onSubmit={handleCredentials}
          className={cx(panel(), css({ w: "loginCard", maxW: "100%", p: "8" }))}
        >
          <div className={css({ textAlign: "center", mb: "6" })}>
            <div className={css({ fontSize: "xl", fontWeight: "600", color: "accent.text" })}>
              millicall
            </div>
            <div className={css({ fontSize: "md", color: "text.muted", mt: "1" })}>
              管理コンソール
            </div>
          </div>

          <label className={css({ display: "block" })}>
            <span
              className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
            >
              ユーザー名
            </span>
            <input
              className={input()}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
            />
          </label>

          <label className={css({ display: "block", mt: "4" })}>
            <span
              className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
            >
              パスワード
            </span>
            <input
              className={input()}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>

          {error ? (
            <p className={css({ color: "danger.text", fontSize: "sm", mt: "3" })}>{error}</p>
          ) : null}

          <button
            type="submit"
            className={cx(button({ variant: "primary" }), css({ w: "100%", mt: "6" }))}
            style={{ height: "40px" }}
            disabled={busy || !username || !password}
          >
            {busy ? "ログイン中…" : "ログイン"}
          </button>

          {/* SAML SSO 有効時のみ表示。SPA ルーターを通さず /saml/login へフル遷移する */}
          {samlEnabled ? (
            <a
              href="/saml/login"
              className={cx(
                button({ variant: "secondary" }),
                css({
                  w: "100%",
                  mt: "2",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  textDecoration: "none",
                }),
              )}
              style={{ height: "40px" }}
            >
              SSO でログイン
            </a>
          ) : null}
        </form>
      ) : (
        /* ─── TOTP ステップ ─── */
        <form
          onSubmit={handleTotp}
          className={cx(panel(), css({ w: "loginCard", maxW: "100%", p: "8" }))}
        >
          <div className={css({ textAlign: "center", mb: "6" })}>
            <div className={css({ fontSize: "xl", fontWeight: "600", color: "accent.text" })}>
              millicall
            </div>
            <div className={css({ fontSize: "md", color: "text.muted", mt: "1" })}>
              2 段階認証
            </div>
          </div>

          <p className={css({ fontSize: "sm", color: "text.muted", mb: "4" })}>
            {useRecovery
              ? "リカバリコードを入力してください。"
              : "認証アプリに表示されている 6 桁のコードを入力してください。"}
          </p>

          <label className={css({ display: "block" })}>
            <span
              className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}
            >
              {useRecovery ? "リカバリコード" : "認証コード（6 桁）"}
            </span>
            <input
              className={input()}
              value={totpCode}
              onChange={(e) => setTotpCode(e.target.value)}
              inputMode={useRecovery ? "text" : "numeric"}
              pattern={useRecovery ? undefined : "[0-9]*"}
              maxLength={useRecovery ? 32 : 6}
              autoFocus
              autoComplete="one-time-code"
            />
          </label>

          {error ? (
            <p className={css({ color: "danger.text", fontSize: "sm", mt: "3" })}>{error}</p>
          ) : null}

          <button
            type="submit"
            className={cx(button({ variant: "primary" }), css({ w: "100%", mt: "6" }))}
            style={{ height: "40px" }}
            disabled={busy || !totpCode}
          >
            {busy ? "確認中…" : "確認"}
          </button>

          {/* リカバリコード切り替え */}
          <button
            type="button"
            className={cx(button({ variant: "ghost" }), css({ w: "100%", mt: "2" }))}
            style={{ height: "36px" }}
            onClick={() => {
              setUseRecovery((v) => !v);
              setTotpCode("");
              setError(null);
            }}
          >
            {useRecovery ? "認証コードを使う" : "リカバリコードを使う"}
          </button>

          {/* 最初のステップへ戻る */}
          <button
            type="button"
            className={cx(button({ variant: "ghost" }), css({ w: "100%", mt: "1" }))}
            style={{ height: "36px" }}
            onClick={() => {
              setStep({ kind: "credentials" });
              setError(null);
            }}
          >
            ← ログイン画面に戻る
          </button>
        </form>
      )}
    </div>
  );
}
