import type { components } from "../../api/schema";

export type RouteRead = components["schemas"]["RouteRead"];
export type RouteCreate = components["schemas"]["RouteCreate"];
export type RouteUpdate = components["schemas"]["RouteUpdate"];
export type RouteTargetType = components["schemas"]["RouteTargetType"];

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface RouteFormValues {
  /** 作成時のみ使用。数字・*・# で 1〜30 文字。 */
  match_number: string;
  target_type: RouteTargetType;
  /**
   * 転送先の識別子（文字列）。
   * - extension の場合: extension.number（内線番号文字列）
   * - ai_agent の場合:  String(agent.id)（数値 id を文字列化）
   */
  target_value: string;
  enabled: boolean;
}

/** 作成フォームの初期値。 */
export function emptyForm(): RouteFormValues {
  return {
    match_number: "",
    target_type: "extension",
    target_value: "",
    enabled: true,
  };
}

/** 既存ルートを編集フォーム値へ写像する。 */
export function formFromRoute(route: RouteRead): RouteFormValues {
  const targetType: RouteTargetType =
    route.target_type === "ai_agent" ? "ai_agent" : "extension";
  return {
    match_number: route.match_number,
    target_type: targetType,
    target_value: route.target_value,
    enabled: route.enabled,
  };
}

/** 作成 payload への変換。target_value は既に文字列（内線番号 or エージェント id 文字列）。 */
export function buildCreatePayload(form: RouteFormValues): RouteCreate {
  return {
    match_number: form.match_number.trim(),
    target_type: form.target_type,
    target_value: form.target_value,
    enabled: form.enabled,
  };
}

/**
 * 編集フォーム → PATCH payload 変換。
 *
 * - target_type / target_value: 片方でも変更があれば両方送る（バックエンドが一括検証するため）。
 * - enabled: boolean 比較。
 * - match_number は更新不可（backend の RouteUpdate に存在しない）。
 */
export function buildUpdatePayload(
  form: RouteFormValues,
  original: RouteRead,
): RouteUpdate {
  const payload: RouteUpdate = {};

  const targetTypeChanged = form.target_type !== original.target_type;
  const targetValueChanged = form.target_value !== original.target_value;

  if (targetTypeChanged || targetValueChanged) {
    payload.target_type = form.target_type;
    payload.target_value = form.target_value;
  }

  if (form.enabled !== original.enabled) {
    payload.enabled = form.enabled;
  }

  return payload;
}

const MATCH_NUMBER_PATTERN = /^[0-9*#]{1,30}$/;

/** クライアント側のバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: RouteFormValues,
  mode: "create" | "edit",
): Partial<Record<keyof RouteFormValues, string>> {
  const errors: Partial<Record<keyof RouteFormValues, string>> = {};

  if (mode === "create") {
    if (!MATCH_NUMBER_PATTERN.test(form.match_number.trim())) {
      errors.match_number = "マッチ番号は数字・*・# で 1〜30 文字です";
    }
  }

  if (form.target_value.trim() === "") {
    errors.target_value = "転送先を選択してください";
  }

  return errors;
}
