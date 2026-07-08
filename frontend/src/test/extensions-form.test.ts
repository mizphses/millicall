import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildUpdatePayload,
  validateForm,
  type ExtensionFormValues,
  type ExtensionRead,
} from "../pages/extensions/formPayload";

const original: ExtensionRead = {
  id: 1,
  number: "1001",
  display_name: "営業部 田中",
  enabled: true,
  calling_permission: "domestic",
};

function formOf(overrides: Partial<ExtensionFormValues>): ExtensionFormValues {
  return {
    number: "1001",
    display_name: "営業部 田中",
    enabled: true,
    calling_permission: "domestic",
    ...overrides,
  };
}

describe("buildCreatePayload", () => {
  it("number / display_name / calling_permission を送り、前後の空白を除去する", () => {
    const payload = buildCreatePayload(formOf({ number: " 1002 ", display_name: " 経理 佐藤 " }));
    expect(payload).toEqual({
      number: "1002",
      display_name: "経理 佐藤",
      calling_permission: "domestic",
    });
  });

  it("発信権限を選択したら payload に反映する", () => {
    const payload = buildCreatePayload(formOf({ calling_permission: "international" }));
    expect(payload.calling_permission).toBe("international");
  });
});

describe("buildUpdatePayload（編集フォーム → PATCH payload 変換）", () => {
  it("変更がなければ空 payload（何も送らない）", () => {
    expect(buildUpdatePayload(formOf({}), original)).toEqual({});
  });

  it("表示名が空文字なら含めない（据え置き）", () => {
    // 「秘密フィールドが空なら送らない」パターンと同型の omit-if-empty。
    expect(buildUpdatePayload(formOf({ display_name: "   " }), original)).toEqual({});
  });

  it("表示名を変更したときだけ display_name を含める", () => {
    expect(buildUpdatePayload(formOf({ display_name: "営業部 鈴木" }), original)).toEqual({
      display_name: "営業部 鈴木",
    });
  });

  it("enabled を切り替えたときだけ enabled を含める", () => {
    expect(buildUpdatePayload(formOf({ enabled: false }), original)).toEqual({ enabled: false });
  });

  it("両方変更したら両方含める", () => {
    expect(
      buildUpdatePayload(formOf({ display_name: "新名称", enabled: false }), original),
    ).toEqual({ display_name: "新名称", enabled: false });
  });

  it("発信権限を変更したときだけ calling_permission を含める", () => {
    expect(buildUpdatePayload(formOf({ calling_permission: "internal" }), original)).toEqual({
      calling_permission: "internal",
    });
  });
});

describe("validateForm", () => {
  it("作成時は 2〜6 桁の数字以外の番号を弾く", () => {
    expect(validateForm(formOf({ number: "abc" }), "create").number).toBeTruthy();
    expect(validateForm(formOf({ number: "1" }), "create").number).toBeTruthy();
    expect(validateForm(formOf({ number: "1001" }), "create").number).toBeUndefined();
  });

  it("編集時は番号を検証しない（番号は変更不可のため）", () => {
    expect(validateForm(formOf({ number: "" }), "edit").number).toBeUndefined();
  });

  it("表示名が空なら弾く", () => {
    expect(validateForm(formOf({ display_name: "  " }), "create").display_name).toBeTruthy();
  });
});
