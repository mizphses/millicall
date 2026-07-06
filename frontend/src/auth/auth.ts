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
