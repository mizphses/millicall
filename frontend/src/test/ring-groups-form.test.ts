import { describe, expect, it } from "vitest";

import {
  buildUpsertPayload,
  emptyForm,
  formFromGroup,
  numberPlanKindLabel,
  validateForm,
  type RingGroupFormValues,
  type RingGroupRead,
} from "../pages/routes/formPayload";

const original: RingGroupRead = {
  id: 1,
  number: "200",
  name: "営業部",
  member_extension_ids: [1, 3],
  enabled: true,
};

function formOf(overrides: Partial<RingGroupFormValues>): RingGroupFormValues {
  return {
    number: "200",
    name: "営業部",
    member_extension_ids: [1, 3],
    enabled: true,
    ...overrides,
  };
}

describe("emptyForm / formFromGroup", () => {
  it("emptyForm はメンバーなし・有効で初期化する", () => {
    const form = emptyForm();
    expect(form.number).toBe("");
    expect(form.name).toBe("");
    expect(form.member_extension_ids).toEqual([]);
    expect(form.enabled).toBe(true);
  });

  it("formFromGroup は既存グループを写像する（メンバー配列はコピー）", () => {
    const form = formFromGroup(original);
    expect(form).toEqual({
      number: "200",
      name: "営業部",
      member_extension_ids: [1, 3],
      enabled: true,
    });
    // 参照共有しない（フォーム編集が元データへ波及しない）
    expect(form.member_extension_ids).not.toBe(original.member_extension_ids);
  });
});

describe("buildUpsertPayload（POST / PATCH 共通）", () => {
  it("全フィールドを含み、number / name を trim する", () => {
    const payload = buildUpsertPayload(formOf({ number: " 200 ", name: " 営業部 " }));
    expect(payload).toEqual({
      number: "200",
      name: "営業部",
      member_extension_ids: [1, 3],
      enabled: true,
    });
  });

  it("メンバーが空でも空配列を送る", () => {
    const payload = buildUpsertPayload(formOf({ member_extension_ids: [] }));
    expect(payload.member_extension_ids).toEqual([]);
  });

  it("enabled フラグを含む", () => {
    expect(buildUpsertPayload(formOf({ enabled: false })).enabled).toBe(false);
  });

  it("メンバー配列はフォーム値のコピー（参照共有しない）", () => {
    const form = formOf({});
    const payload = buildUpsertPayload(form);
    expect(payload.member_extension_ids).not.toBe(form.member_extension_ids);
  });
});

describe("validateForm", () => {
  it("有効なフォームはエラーなし", () => {
    expect(Object.keys(validateForm(formOf({})))).toHaveLength(0);
  });

  it("番号は数字 2〜6 桁のみ許可する", () => {
    expect(validateForm(formOf({ number: "" })).number).toBeTruthy();
    expect(validateForm(formOf({ number: "1" })).number).toBeTruthy();
    expect(validateForm(formOf({ number: "1234567" })).number).toBeTruthy();
    expect(validateForm(formOf({ number: "12a" })).number).toBeTruthy();
    expect(validateForm(formOf({ number: "20" })).number).toBeUndefined();
    expect(validateForm(formOf({ number: "123456" })).number).toBeUndefined();
  });

  it("name が空なら弾く", () => {
    expect(validateForm(formOf({ name: "  " })).name).toBeTruthy();
  });

  it("name が 101 文字以上なら弾く", () => {
    expect(validateForm(formOf({ name: "あ".repeat(101) })).name).toBeTruthy();
  });
});

describe("numberPlanKindLabel", () => {
  it("既知の kind を日本語ラベルへ変換する", () => {
    expect(numberPlanKindLabel("extension")).toBe("内線");
    expect(numberPlanKindLabel("ai_agent")).toBe("AI");
    expect(numberPlanKindLabel("workflow")).toBe("ワークフロー");
    expect(numberPlanKindLabel("ring_group")).toBe("グループ");
  });

  it("未知の kind はそのまま返す", () => {
    expect(numberPlanKindLabel("unknown_kind")).toBe("unknown_kind");
  });
});
