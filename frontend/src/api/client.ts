import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** ログイン画面のパス。401 横断ハンドリングで遷移する。 */
export const LOGIN_PATH = "/login";

/** CSRF Cookie 名（バックエンドと一致させる）。 */
const CSRF_COOKIE_NAME = "millicall_csrf";

/** CSRF ヘッダ名。 */
const CSRF_HEADER_NAME = "X-CSRF-Token";

/** 変更系メソッド（CSRF ヘッダが必要）。 */
const CSRF_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * document.cookie から指定 Cookie を取得する。
 * 見つからなければ null を返す。
 */
export function getCsrfCookie(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie
    .split(";")
    .map((c) => c.trim())
    .find((c) => c.startsWith(CSRF_COOKIE_NAME + "="));
  return match ? decodeURIComponent(match.slice(CSRF_COOKIE_NAME.length + 1)) : null;
}

/**
 * CSRF Cookie が存在しない場合に GET /api/auth/csrf を呼んでサーバーにセットさせる。
 * ログイン後 / アプリ起動時のブートストラップで使う。
 */
export async function ensureCsrfCookie(): Promise<void> {
  if (getCsrfCookie()) return;
  // CSRF エンドポイント自身は GET なので CSRF ヘッダ不要
  await fetch("/api/auth/csrf", { credentials: "include" }).catch(() => {
    // ネットワークエラーは無視（Cookie なしでもアプリは続行できる）
  });
}

/**
 * CSRF ヘッダ注入ミドルウェア。
 * 変更系リクエスト（POST/PUT/PATCH/DELETE）に Cookie から読んだトークンを付与する。
 */
export function createCsrfMiddleware(): Middleware {
  return {
    async onRequest({ request }) {
      if (!CSRF_METHODS.has(request.method)) return request;
      const token = getCsrfCookie();
      if (!token) return request;
      const next = request.clone();
      next.headers.set(CSRF_HEADER_NAME, token);
      return next;
    },
  };
}

/**
 * 401 応答を横断的に処理するミドルウェア。
 * 認証切れの API 応答を受けたらログイン画面へ強制遷移させる。
 * ただしログイン画面自身の /api/auth/login と /api/auth/me（認証確認）は除外し、
 * それらの 401 は呼び出し側（ログインフォーム / 認証ガード）に委ねる。
 */
const AUTH_PROBE_PATHS = ["/api/auth/login", "/api/auth/me", "/api/auth/login/totp"];

export function createUnauthorizedMiddleware(redirect: (path: string) => void): Middleware {
  return {
    async onResponse({ request, response }) {
      if (response.status !== 401) return response;
      const url = new URL(request.url);
      if (AUTH_PROBE_PATHS.some((p) => url.pathname.endsWith(p))) {
        return response;
      }
      if (typeof window !== "undefined" && window.location.pathname !== LOGIN_PATH) {
        redirect(LOGIN_PATH);
      }
      return response;
    },
  };
}

/**
 * openapi-fetch クライアント。
 * - credentials: "include" で同一オリジン Cookie セッションを送る。
 * - baseUrl は空（同一オリジン）。開発時は Vite dev proxy が /api を core へ転送する。
 */
export const api = createClient<paths>({
  credentials: "include",
});

// CSRF ヘッダを注入するミドルウェアを先に登録する
api.use(createCsrfMiddleware());

api.use(
  createUnauthorizedMiddleware((path) => {
    window.location.assign(path);
  }),
);
