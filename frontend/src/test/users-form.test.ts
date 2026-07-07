import { describe, expect, it } from "vitest";

import {
  buildCreatePayload,
  buildPatchPayload,
  canResetPassword,
  editFormFromUser,
  emptyCreateForm,
  hasCreateErrors,
  validateCreateForm,
  type UserRead,
} from "../pages/users/formPayload";

const baseUser: UserRead = {
  id: 1,
  username: "alice",
  display_name: "Alice",
  role: "user",
  email: "alice@example.com",
  enabled: true,
  origin: "local",
  totp_enabled: false,
};

describe("buildCreatePayload", () => {
  it("トリムして必須フィールドを組み立て、email 空は undefined", () => {
    const form = {
      ...emptyCreateForm(),
      username: "  bob ",
      display_name: " Bob ",
      password: "secret123",
      role: "admin",
      email: "  ",
    };
    expect(buildCreatePayload(form)).toEqual({
      username: "bob",
      display_name: "Bob",
      password: "secret123",
      role: "admin",
      email: undefined,
    });
  });

  it("email が入力されていれば含める", () => {
    const form = {
      ...emptyCreateForm(),
      username: "bob",
      display_name: "Bob",
      password: "secret123",
      email: "bob@example.com",
    };
    expect(buildCreatePayload(form).email).toBe("bob@example.com");
  });
});

describe("buildPatchPayload", () => {
  it("無変更なら空オブジェクト", () => {
    const form = editFormFromUser(baseUser);
    expect(buildPatchPayload(form, baseUser)).toEqual({});
  });

  it("display_name / role / enabled の変更を個別に反映する", () => {
    const form = { ...editFormFromUser(baseUser), display_name: "Alice B", role: "admin", enabled: false };
    expect(buildPatchPayload(form, baseUser)).toEqual({
      display_name: "Alice B",
      role: "admin",
      enabled: false,
    });
  });

  it("email を空にすると null を送る（クリア）", () => {
    const form = { ...editFormFromUser(baseUser), email: "" };
    expect(buildPatchPayload(form, baseUser)).toEqual({ email: null });
  });

  it("display_name が空白のみなら含めない", () => {
    const form = { ...editFormFromUser(baseUser), display_name: "   " };
    expect("display_name" in buildPatchPayload(form, baseUser)).toBe(false);
  });
});

describe("validateCreateForm / hasCreateErrors", () => {
  it("必須欠落を検出する", () => {
    const errors = validateCreateForm(emptyCreateForm());
    expect(errors.username).toBeDefined();
    expect(errors.display_name).toBeDefined();
    expect(errors.password).toBeDefined();
    expect(hasCreateErrors(errors)).toBe(true);
  });

  it("短いパスワードを拒否する", () => {
    const form = { ...emptyCreateForm(), username: "x", display_name: "X", password: "short" };
    expect(validateCreateForm(form).password).toBeDefined();
  });

  it("不正な email を拒否する", () => {
    const form = {
      ...emptyCreateForm(),
      username: "x",
      display_name: "X",
      password: "longenough",
      email: "notanemail",
    };
    expect(validateCreateForm(form).email).toBeDefined();
  });

  it("正常入力はエラーなし", () => {
    const form = {
      ...emptyCreateForm(),
      username: "x",
      display_name: "X",
      password: "longenough",
      email: "x@example.com",
    };
    expect(hasCreateErrors(validateCreateForm(form))).toBe(false);
  });
});

describe("canResetPassword", () => {
  it("origin=local のみリセット可能", () => {
    expect(canResetPassword({ ...baseUser, origin: "local" })).toBe(true);
    expect(canResetPassword({ ...baseUser, origin: "saml" })).toBe(false);
    expect(canResetPassword({ ...baseUser, origin: "scim" })).toBe(false);
  });
});
