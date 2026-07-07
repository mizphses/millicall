import type { components } from "../../api/schema";

export type ExtensionRead = components["schemas"]["ExtensionRead"];
export type ExtensionCreate = components["schemas"]["ExtensionCreate"];
export type ExtensionUpdate = components["schemas"]["ExtensionUpdate"];

/** 発信権限の選択肢。 */
export const CALLING_PERMISSIONS = ["internal", "domestic", "international"] as const;
export type CallingPermission = (typeof CALLING_PERMISSIONS)[number];

export const CALLING_PERMISSION_LABEL: Record<CallingPermission, string> = {
  internal: "内線のみ",
  domestic: "国内発信",
  international: "国際発信",
};

/** 未知値も安全に扱うためのナローイング。 */
export function toCallingPermission(v: string): CallingPermission {
  return (CALLING_PERMISSIONS as readonly string[]).includes(v)
    ? (v as CallingPermission)
    : "domestic";
}

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface ExtensionFormValues {
  number: string;
  display_name: string;
  enabled: boolean;
  calling_permission: CallingPermission;
}

/** 作成フォームの初期値。 */
export function emptyForm(): ExtensionFormValues {
  return { number: "", display_name: "", enabled: true, calling_permission: "domestic" };
}

/** 既存内線を編集フォーム値へ写像する。 */
export function formFromExtension(ext: ExtensionRead): ExtensionFormValues {
  return {
    number: ext.number,
    display_name: ext.display_name,
    enabled: ext.enabled,
    calling_permission: toCallingPermission(ext.calling_permission),
  };
}

/**
 * 作成 payload への変換。
 * backend の ExtensionCreate は number / display_name / calling_permission を受け付ける
 * （sip_password はサーバ側で自動生成される）。
 */
export function buildCreatePayload(form: ExtensionFormValues): ExtensionCreate {
  return {
    number: form.number.trim(),
    display_name: form.display_name.trim(),
    calling_permission: form.calling_permission,
  };
}

/**
 * 編集フォーム → PATCH payload への変換。
 *
 * 「変更のないフィールドは payload に含めない（omit-if-unchanged）」を実装する。
 * display_name は空文字なら「据え置き」として送らない（omit-if-empty）。
 *
 * これは Task 3 以降で扱う「秘密フィールド（password / api_key）は
 * 入力が空なら payload に含めない = 現状維持」パターンの原型。
 * 秘密フィールドの場合は original との比較をせず、
 * 「入力が空でない場合のみ含める」で置き換えて踏襲すること。
 */
export function buildUpdatePayload(
  form: ExtensionFormValues,
  original: ExtensionRead,
): ExtensionUpdate {
  const payload: ExtensionUpdate = {};
  const name = form.display_name.trim();
  if (name !== "" && name !== original.display_name) {
    payload.display_name = name;
  }
  if (form.enabled !== original.enabled) {
    payload.enabled = form.enabled;
  }
  if (form.calling_permission !== original.calling_permission) {
    payload.calling_permission = form.calling_permission;
  }
  return payload;
}

const NUMBER_PATTERN = /^[0-9]{2,6}$/;

/** クライアント側の軽いバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: ExtensionFormValues,
  mode: "create" | "edit",
): Partial<Record<"number" | "display_name", string>> {
  const errors: Partial<Record<"number" | "display_name", string>> = {};
  if (mode === "create" && !NUMBER_PATTERN.test(form.number.trim())) {
    errors.number = "2〜6 桁の数字で入力してください";
  }
  const name = form.display_name.trim();
  if (name.length < 1 || name.length > 100) {
    errors.display_name = "表示名は 1〜100 文字で入力してください";
  }
  return errors;
}
