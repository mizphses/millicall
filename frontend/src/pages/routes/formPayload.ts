import type { components } from "../../api/schema";

export type NumberPlanEntryRead = components["schemas"]["NumberPlanEntryRead"];
export type RingGroupRead = components["schemas"]["RingGroupRead"];
export type RingGroupUpsert = components["schemas"]["RingGroupUpsert"];

/** 番号プランの kind → 日本語ラベル。バッジ・トランクの着信先 select で共用する。 */
export const NUMBER_PLAN_KIND_LABELS: Record<string, string> = {
  extension: "内線",
  ai_agent: "AI",
  workflow: "ワークフロー",
  ring_group: "グループ",
};

/** kind の日本語ラベルを引く（未知の kind はそのまま返す）。 */
export function numberPlanKindLabel(kind: string): string {
  return NUMBER_PLAN_KIND_LABELS[kind] ?? kind;
}

/** SlidePanel フォームが保持する値。API 型とは分離し、UI 都合の型に寄せる。 */
export interface RingGroupFormValues {
  /** グループ番号。数字 2〜6 桁。 */
  number: string;
  name: string;
  /** 鳴動メンバーの内線 id（チェックボックスで複数選択）。 */
  member_extension_ids: number[];
  enabled: boolean;
}

/** 作成フォームの初期値。 */
export function emptyForm(): RingGroupFormValues {
  return {
    number: "",
    name: "",
    member_extension_ids: [],
    enabled: true,
  };
}

/** 既存グループを編集フォーム値へ写像する。 */
export function formFromGroup(group: RingGroupRead): RingGroupFormValues {
  return {
    number: group.number,
    name: group.name,
    member_extension_ids: [...group.member_extension_ids],
    enabled: group.enabled,
  };
}

/**
 * POST / PATCH 共通の payload 変換。
 * バックエンドは Upsert 型（全フィールド送信）のため、差分計算は行わない。
 */
export function buildUpsertPayload(form: RingGroupFormValues): RingGroupUpsert {
  return {
    number: form.number.trim(),
    name: form.name.trim(),
    member_extension_ids: [...form.member_extension_ids],
    enabled: form.enabled,
  };
}

const NUMBER_PATTERN = /^[0-9]{2,6}$/;

/** クライアント側のバリデーション。フィールド名 → エラーメッセージ。 */
export function validateForm(
  form: RingGroupFormValues,
): Partial<Record<keyof RingGroupFormValues, string>> {
  const errors: Partial<Record<keyof RingGroupFormValues, string>> = {};

  if (!NUMBER_PATTERN.test(form.number.trim())) {
    errors.number = "番号は数字 2〜6 桁で入力してください";
  }

  const name = form.name.trim();
  if (name.length < 1 || name.length > 100) {
    errors.name = "名前は 1〜100 文字で入力してください";
  }

  return errors;
}
