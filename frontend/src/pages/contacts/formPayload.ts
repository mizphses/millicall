import type { components } from "../../api/schema";

export type ContactRead = components["schemas"]["ContactRead"];
export type ContactCreate = components["schemas"]["ContactCreate"];
export type ContactUpdate = components["schemas"]["ContactUpdate"];
export type CallCreate = components["schemas"]["CallCreate"];
export type ExtensionRead = components["schemas"]["ExtensionRead"];

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface ContactFormValues {
  name: string;
  phone_number: string;
  company: string;
  department: string;
  notes: string;
}

/** 作成フォームの初期値。 */
export function emptyForm(): ContactFormValues {
  return {
    name: "",
    phone_number: "",
    company: "",
    department: "",
    notes: "",
  };
}

/** 既存連絡先を編集フォーム値へ写像する。 */
export function formFromContact(contact: ContactRead): ContactFormValues {
  return {
    name: contact.name,
    phone_number: contact.phone_number,
    company: contact.company,
    department: contact.department,
    notes: contact.notes,
  };
}

/** 作成 payload への変換。 */
export function buildCreatePayload(form: ContactFormValues): ContactCreate {
  return {
    name: form.name.trim(),
    phone_number: form.phone_number.trim(),
    company: form.company.trim(),
    department: form.department.trim(),
    notes: form.notes,
  };
}

/**
 * 編集フォーム → PATCH payload への変換。
 * 「変更のないフィールドは payload に含めない（omit-if-unchanged）」を実装する。
 * name / phone_number は空文字なら「据え置き」として送らない（omit-if-empty）。
 * company / department / notes は "" も有効値なので変更があれば含める（omit-if-unchanged のみ）。
 */
export function buildUpdatePayload(
  form: ContactFormValues,
  original: ContactRead,
): ContactUpdate {
  const payload: ContactUpdate = {};

  const name = form.name.trim();
  if (name !== "" && name !== original.name) payload.name = name;

  const phoneNumber = form.phone_number.trim();
  if (phoneNumber !== "" && phoneNumber !== original.phone_number) {
    payload.phone_number = phoneNumber;
  }

  const company = form.company.trim();
  if (company !== original.company) payload.company = company;

  const department = form.department.trim();
  if (department !== original.department) payload.department = department;

  if (form.notes !== original.notes) payload.notes = form.notes;

  return payload;
}

/**
 * 発信 payload への変換。
 * POST /api/calls の body 形式: { from_extension: string, to: string }
 */
export function buildCallPayload(fromExtension: string, toNumber: string): CallCreate {
  return { from_extension: fromExtension, to: toNumber };
}

/** クライアント側の軽いバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: ContactFormValues,
  _mode: "create" | "edit",
): Partial<Record<keyof ContactFormValues, string>> {
  const errors: Partial<Record<keyof ContactFormValues, string>> = {};

  const name = form.name.trim();
  if (name.length < 1 || name.length > 100) {
    errors.name = "名前は 1〜100 文字で入力してください";
  }

  const phone = form.phone_number.trim();
  if (phone.length < 1 || phone.length > 30) {
    errors.phone_number = "電話番号は 1〜30 文字で入力してください";
  }

  if (form.company.length > 100) {
    errors.company = "会社名は 100 文字以内で入力してください";
  }

  if (form.department.length > 100) {
    errors.department = "部署名は 100 文字以内で入力してください";
  }

  if (form.notes.length > 2000) {
    errors.notes = "メモは 2000 文字以内で入力してください";
  }

  return errors;
}
