import { describe, expect, it } from "vitest";

import {
  buildCallPayload,
  buildCreatePayload,
  buildUpdatePayload,
  validateForm,
  type ContactFormValues,
  type ContactRead,
} from "../pages/contacts/formPayload";

const original: ContactRead = {
  id: 1,
  name: "田中 太郎",
  phone_number: "0312345678",
  company: "株式会社サンプル",
  department: "営業部",
  notes: "重要顧客",
};

function formOf(overrides: Partial<ContactFormValues>): ContactFormValues {
  return {
    name: "田中 太郎",
    phone_number: "0312345678",
    company: "株式会社サンプル",
    department: "営業部",
    notes: "重要顧客",
    ...overrides,
  };
}

describe("buildCreatePayload", () => {
  it("全フィールドを含む payload を返す", () => {
    const payload = buildCreatePayload(formOf({}));
    expect(payload).toEqual({
      name: "田中 太郎",
      phone_number: "0312345678",
      company: "株式会社サンプル",
      department: "営業部",
      notes: "重要顧客",
    });
  });

  it("name / phone_number の前後の空白を除去する", () => {
    const payload = buildCreatePayload(
      formOf({ name: "  鈴木 花子  ", phone_number: "  09012345678  " }),
    );
    expect(payload.name).toBe("鈴木 花子");
    expect(payload.phone_number).toBe("09012345678");
  });

  it("company / department が空文字でも含める", () => {
    const payload = buildCreatePayload(formOf({ company: "", department: "" }));
    expect(payload.company).toBe("");
    expect(payload.department).toBe("");
  });
});

describe("buildUpdatePayload（編集フォーム → PATCH payload 変換）", () => {
  it("変更がなければ空 payload", () => {
    expect(buildUpdatePayload(formOf({}), original)).toEqual({});
  });

  it("name を変更したとき name を含める", () => {
    expect(buildUpdatePayload(formOf({ name: "佐藤 次郎" }), original)).toEqual({
      name: "佐藤 次郎",
    });
  });

  it("name が空文字なら含めない（据え置き）", () => {
    expect(buildUpdatePayload(formOf({ name: "" }), original)).toEqual({});
  });

  it("phone_number を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ phone_number: "0398765432" }), original)).toEqual({
      phone_number: "0398765432",
    });
  });

  it("phone_number が空文字なら含めない（据え置き）", () => {
    expect(buildUpdatePayload(formOf({ phone_number: "" }), original)).toEqual({});
  });

  it("company を空文字に変更したとき含める（空文字は有効値）", () => {
    expect(buildUpdatePayload(formOf({ company: "" }), original)).toEqual({ company: "" });
  });

  it("notes を変更したとき含める", () => {
    expect(buildUpdatePayload(formOf({ notes: "最重要顧客" }), original)).toEqual({
      notes: "最重要顧客",
    });
  });

  it("notes を空文字にしたとき含める（空文字は有効値）", () => {
    expect(buildUpdatePayload(formOf({ notes: "" }), original)).toEqual({ notes: "" });
  });

  it("複数フィールドを変更したとき全て含める", () => {
    expect(
      buildUpdatePayload(
        formOf({ name: "山田 三郎", phone_number: "0311112222", department: "開発部" }),
        original,
      ),
    ).toEqual({
      name: "山田 三郎",
      phone_number: "0311112222",
      department: "開発部",
    });
  });
});

describe("buildCallPayload", () => {
  it("POST /api/calls の body 形式を返す", () => {
    const payload = buildCallPayload("1001", "0312345678");
    expect(payload).toEqual({ from_extension: "1001", to: "0312345678" });
  });

  it("from_extension と to をそのまま渡す", () => {
    const payload = buildCallPayload("2001", "09012345678");
    expect(payload.from_extension).toBe("2001");
    expect(payload.to).toBe("09012345678");
  });
});

describe("validateForm", () => {
  it("有効なフォームはエラーなし", () => {
    expect(Object.keys(validateForm(formOf({}), "create"))).toHaveLength(0);
  });

  it("name が空ならエラー", () => {
    expect(validateForm(formOf({ name: "  " }), "create").name).toBeTruthy();
  });

  it("name が 101 文字以上ならエラー", () => {
    expect(validateForm(formOf({ name: "a".repeat(101) }), "create").name).toBeTruthy();
  });

  it("phone_number が空ならエラー", () => {
    expect(validateForm(formOf({ phone_number: "" }), "create").phone_number).toBeTruthy();
  });

  it("phone_number が 31 文字以上ならエラー", () => {
    expect(
      validateForm(formOf({ phone_number: "0".repeat(31) }), "create").phone_number,
    ).toBeTruthy();
  });

  it("company が 101 文字以上ならエラー", () => {
    expect(
      validateForm(formOf({ company: "a".repeat(101) }), "create").company,
    ).toBeTruthy();
  });

  it("notes が 2001 文字以上ならエラー", () => {
    expect(
      validateForm(formOf({ notes: "a".repeat(2001) }), "create").notes,
    ).toBeTruthy();
  });

  it("company が空文字ならエラーなし（省略可）", () => {
    expect(validateForm(formOf({ company: "" }), "create").company).toBeUndefined();
  });

  it("notes が 2000 文字ならエラーなし（上限境界値）", () => {
    expect(validateForm(formOf({ notes: "a".repeat(2000) }), "create").notes).toBeUndefined();
  });

  it("phone_number が 30 文字ならエラーなし（上限境界値）", () => {
    expect(
      validateForm(formOf({ phone_number: "0".repeat(30) }), "create").phone_number,
    ).toBeUndefined();
  });
});
