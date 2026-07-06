import type { components } from "../../api/schema";

export type TrunkRead = components["schemas"]["TrunkRead"];
export type TrunkCreate = components["schemas"]["TrunkCreate"];
export type TrunkUpdate = components["schemas"]["TrunkUpdate"];

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface TrunkFormValues {
  /** 作成時のみ使用。英数字・ハイフン・アンダースコア 1〜50 文字。 */
  name: string;
  display_name: string;
  host: string;
  username: string;
  /**
   * 書き込み専用。
   * - 作成時: 必須
   * - 編集時: 空ならペイロードに含めない（= 現状のパスワードを据え置き）
   */
  password: string;
  did_number: string;
  caller_id: string;
  enabled: boolean;
}

/** 作成フォームの初期値。 */
export function emptyForm(): TrunkFormValues {
  return {
    name: "",
    display_name: "",
    host: "",
    username: "",
    password: "",
    did_number: "",
    caller_id: "",
    enabled: true,
  };
}

/** 既存トランクを編集フォーム値へ写像する。password は読み出せないため空にする。 */
export function formFromTrunk(trunk: TrunkRead): TrunkFormValues {
  return {
    name: trunk.name,
    display_name: trunk.display_name,
    host: trunk.host,
    username: trunk.username,
    password: "",         // write-only: サーバから取得不可
    did_number: trunk.did_number,
    caller_id: trunk.caller_id,
    enabled: trunk.enabled,
  };
}

/** 作成 payload への変換。全フィールドを含める。 */
export function buildCreatePayload(form: TrunkFormValues): TrunkCreate {
  return {
    name: form.name.trim(),
    display_name: form.display_name.trim(),
    host: form.host.trim(),
    username: form.username.trim(),
    password: form.password,
    did_number: form.did_number.trim(),
    caller_id: form.caller_id.trim(),
    enabled: form.enabled,
  };
}

/**
 * 編集フォーム → PATCH payload 変換。
 *
 * omit-if-unchanged: 変更のないフィールドはペイロードに含めない。
 * omit-if-empty（秘密フィールド）: password は空なら含めない（= 据え置き）。
 *
 * - display_name / host / username: 空か unchanged なら含めない
 * - password: 書き込み専用。空なら据え置き（含めない）、非空なら含める（original との比較なし）
 * - did_number / caller_id: 空文字も有効値。変更があれば含める
 * - enabled: boolean 比較
 */
export function buildUpdatePayload(
  form: TrunkFormValues,
  original: TrunkRead,
): TrunkUpdate {
  const payload: TrunkUpdate = {};

  const displayName = form.display_name.trim();
  if (displayName !== "" && displayName !== original.display_name) {
    payload.display_name = displayName;
  }

  const host = form.host.trim();
  if (host !== "" && host !== original.host) {
    payload.host = host;
  }

  const username = form.username.trim();
  if (username !== "" && username !== original.username) {
    payload.username = username;
  }

  // password: write-only。空なら据え置き、非空なら送る。original との比較は不要。
  if (form.password !== "") {
    payload.password = form.password;
  }

  const didNumber = form.did_number.trim();
  if (didNumber !== original.did_number) {
    payload.did_number = didNumber;
  }

  const callerId = form.caller_id.trim();
  if (callerId !== original.caller_id) {
    payload.caller_id = callerId;
  }

  if (form.enabled !== original.enabled) {
    payload.enabled = form.enabled;
  }

  return payload;
}

const NAME_PATTERN = /^[A-Za-z0-9_-]{1,50}$/;

/** クライアント側のバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: TrunkFormValues,
  mode: "create" | "edit",
): Partial<Record<keyof TrunkFormValues, string>> {
  const errors: Partial<Record<keyof TrunkFormValues, string>> = {};

  if (mode === "create") {
    if (!NAME_PATTERN.test(form.name.trim())) {
      errors.name = "名前は英数字・ハイフン・アンダースコアで 1〜50 文字です";
    }
    if (form.display_name.trim().length < 1) {
      errors.display_name = "表示名を入力してください";
    }
    if (form.host.trim().length < 1) {
      errors.host = "ホスト名を入力してください";
    }
    if (form.username.trim().length < 1) {
      errors.username = "ユーザー名を入力してください";
    }
    if (form.password.length < 1) {
      errors.password = "パスワードを入力してください";
    }
  } else {
    // 編集: 空なら据え置き（エラーなし）。入力があれば長さをチェック。
    const displayName = form.display_name.trim();
    if (displayName.length > 100) {
      errors.display_name = "表示名は 100 文字以内で入力してください";
    }
    const host = form.host.trim();
    if (host.length > 100) {
      errors.host = "ホスト名は 100 文字以内で入力してください";
    }
    const username = form.username.trim();
    if (username.length > 50) {
      errors.username = "ユーザー名は 50 文字以内で入力してください";
    }
    if (form.password.length > 128) {
      errors.password = "パスワードは 128 文字以内で入力してください";
    }
  }

  if (form.did_number.trim().length > 30) {
    errors.did_number = "DID 番号は 30 文字以内で入力してください";
  }
  if (form.caller_id.trim().length > 30) {
    errors.caller_id = "発信者番号は 30 文字以内で入力してください";
  }

  return errors;
}
