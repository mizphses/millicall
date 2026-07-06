import createClient, { type Middleware } from "openapi-fetch";

import type { paths } from "./schema";

/** ログイン画面のパス。401 横断ハンドリングで遷移する。 */
export const LOGIN_PATH = "/login";

/**
 * 401 応答を横断的に処理するミドルウェア。
 * 認証切れの API 応答を受けたらログイン画面へ強制遷移させる。
 * ただしログイン画面自身の /api/auth/login と /api/auth/me（認証確認）は除外し、
 * それらの 401 は呼び出し側（ログインフォーム / 認証ガード）に委ねる。
 */
const AUTH_PROBE_PATHS = ["/api/auth/login", "/api/auth/me"];

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

api.use(
  createUnauthorizedMiddleware((path) => {
    window.location.assign(path);
  }),
);
