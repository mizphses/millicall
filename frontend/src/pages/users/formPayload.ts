/**
 * ユーザー管理フォームのペイロードビルダーとバリデーション。
 * テスト可能なピュア関数として切り出す。
 */

import type { components } from "../../api/schema.d";

export type UserRead = components["schemas"]["UserRead"];
export type UserCreate = components["schemas"]["UserCreate"];
export type UserPatch = components["schemas"]["UserPatch"];

// ─────────────────────────────────────────────────────────
// フォーム型
// ─────────────────────────────────────────────────────────

export type UserCreateForm = {
  username: string;
  display_name: string;
  password: string;
  role: string;
  email: string;
};

export type UserEditForm = {
  display_name: string;
  role: string;
  email: string;
  enabled: boolean;
};

export type ResetPasswordForm = {
  new_password: string;
  confirm_password: string;
};

export type UserFormErrors = {
  username?: string;
  display_name?: string;
  password?: string;
  role?: string;
  email?: string;
};

// ─────────────────────────────────────────────────────────
// 定数
// ─────────────────────────────────────────────────────────

export const ROLES = ["admin", "user"] as const;

export const ROLE_LABEL: Record<string, string> = {
  admin: "管理者",
  user: "一般",
};

export const ORIGIN_LABEL: Record<string, string> = {
  local: "ローカル",
  saml: "SAML",
  scim: "SCIM",
};

// ─────────────────────────────────────────────────────────
// フォーム初期値
// ─────────────────────────────────────────────────────────

export function emptyCreateForm(): UserCreateForm {
  return {
    username: "",
    display_name: "",
    password: "",
    role: "user",
    email: "",
  };
}

export function editFormFromUser(user: UserRead): UserEditForm {
  return {
    display_name: user.display_name,
    role: user.role,
    email: user.email ?? "",
    enabled: user.enabled,
  };
}

// ─────────────────────────────────────────────────────────
// ペイロードビルダー
// ─────────────────────────────────────────────────────────

export function buildCreatePayload(form: UserCreateForm): UserCreate {
  return {
    username: form.username.trim(),
    display_name: form.display_name.trim(),
    password: form.password,
    role: form.role,
    email: form.email.trim() || undefined,
  };
}

export function buildPatchPayload(form: UserEditForm, original: UserRead): UserPatch {
  const patch: UserPatch = {};
  const trimmedDisplay = form.display_name.trim();
  if (trimmedDisplay && trimmedDisplay !== original.display_name) {
    patch.display_name = trimmedDisplay;
  }
  if (form.role !== original.role) {
    patch.role = form.role;
  }
  const trimmedEmail = form.email.trim();
  const originalEmail = original.email ?? "";
  if (trimmedEmail !== originalEmail) {
    patch.email = trimmedEmail || null;
  }
  if (form.enabled !== original.enabled) {
    patch.enabled = form.enabled;
  }
  return patch;
}

// ─────────────────────────────────────────────────────────
// バリデーション
// ─────────────────────────────────────────────────────────

export function validateCreateForm(form: UserCreateForm): UserFormErrors {
  const errors: UserFormErrors = {};
  if (!form.username.trim()) errors.username = "ユーザー名は必須です";
  if (!form.display_name.trim()) errors.display_name = "表示名は必須です";
  if (!form.password) errors.password = "パスワードは必須です";
  if (form.password && form.password.length < 8) errors.password = "パスワードは 8 文字以上必要です";
  if (!form.role) errors.role = "ロールは必須です";
  if (form.email.trim() && !form.email.includes("@")) {
    errors.email = "有効なメールアドレスを入力してください";
  }
  return errors;
}

export function hasCreateErrors(errors: UserFormErrors): boolean {
  return Object.values(errors).some(Boolean);
}

/** origin=local のユーザーのみパスワードリセットが可能。 */
export function canResetPassword(user: UserRead): boolean {
  return user.origin === "local";
}
