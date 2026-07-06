import { useRouter } from "@tanstack/react-router";
import { useState } from "react";

import { css } from "styled-system/css";
import { button, input, panel } from "styled-system/recipes";

import { api } from "../api/client";

export function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    const { error: apiError } = await api.POST("/api/auth/login", {
      body: { username, password },
    });
    setBusy(false);
    if (apiError) {
      setError("ユーザー名またはパスワードが正しくありません");
      return;
    }
    router.navigate({ to: "/" });
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
      <form
        onSubmit={handleSubmit}
        className={panel()}
        style={{ width: "360px", maxWidth: "100%", padding: "32px" }}
      >
        <div className={css({ textAlign: "center", mb: "6" })}>
          <div className={css({ fontSize: "xl", fontWeight: "600", color: "accent.text" })}>millicall</div>
          <div className={css({ fontSize: "md", color: "text.muted", mt: "1" })}>管理コンソール</div>
        </div>

        <label className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1" })}>
          ユーザー名
        </label>
        <input
          className={input()}
          value={username}
          onChange={(e) => setUsername(e.target.value)}
          autoComplete="username"
          autoFocus
        />

        <label className={css({ display: "block", fontSize: "sm", color: "text.muted", mb: "1", mt: "4" })}>
          パスワード
        </label>
        <input
          className={input()}
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password"
        />

        {error ? (
          <p className={css({ color: "danger.text", fontSize: "sm", mt: "3" })}>{error}</p>
        ) : null}

        <button
          type="submit"
          className={button({ variant: "primary" })}
          style={{ width: "100%", marginTop: "24px" }}
          disabled={busy || !username || !password}
        >
          {busy ? "ログイン中…" : "ログイン"}
        </button>
      </form>
    </div>
  );
}
