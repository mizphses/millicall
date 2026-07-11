import { api } from "../api/client";
import type { components } from "../api/schema";

export type CurrentUser = components["schemas"]["UserRead"];

/**
 * 現在のログインユーザーを取得する。未認証（401）や失敗時は null。
 * 認証ガードの beforeLoad と、ヘッダのユーザー表示で使う。
 */
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  const { data, error } = await api.GET("/api/auth/me");
  if (error || !data) return null;
  return data;
}

/** 一般ユーザー（role=user）のホーム。アカウント（セキュリティ設定）ページ。 */
export const USER_HOME_PATH = "/settings/security";

/**
 * ログイン後の遷移先を返す。
 * admin はダッシュボード、それ以外（user など未知のロールを含む）は
 * アカウントページへ（安全側デフォルト）。
 */
export function postLoginPath(role: string | undefined): string {
  return role === "admin" ? "/" : USER_HOME_PATH;
}

/** ログイン画面向けの公開設定（未認証で取得可能）。 */
export interface LoginConfig {
  saml_enabled: boolean;
}

/**
 * 未知の値から LoginConfig を安全に取り出す。
 * 形が想定と異なる場合は SAML 無効として扱う（ボタンを出さない安全側）。
 */
export function parseLoginConfig(data: unknown): LoginConfig {
  if (
    data !== null &&
    typeof data === "object" &&
    typeof (data as { saml_enabled?: unknown }).saml_enabled === "boolean"
  ) {
    return { saml_enabled: (data as { saml_enabled: boolean }).saml_enabled };
  }
  return { saml_enabled: false };
}

/**
 * ログイン画面向けの公開設定を取得する。失敗時は SAML 無効として扱う。
 */
export async function fetchLoginConfig(): Promise<LoginConfig> {
  try {
    const { data, error } = await api.GET("/api/auth/config");
    if (error || !data) return { saml_enabled: false };
    return parseLoginConfig(data);
  } catch {
    return { saml_enabled: false };
  }
}
