import { describe, expect, it } from "vitest";

import {
  buildSetupBody,
  initialState,
  isValidTotpCode,
  toEnrolling,
  toRecovery,
} from "../pages/security/state";

describe("2FA 登録ステートマシン", () => {
  it("初期状態は idle", () => {
    expect(initialState()).toEqual({ step: "idle" });
  });

  it("setup 成功で enrolling へ（secret / URI を保持）", () => {
    const s = toEnrolling("SECRET", "otpauth://totp/x");
    expect(s).toEqual({ step: "enrolling", secret: "SECRET", provisioningUri: "otpauth://totp/x" });
  });

  it("verify 成功で recovery へ（リカバリコードを保持）", () => {
    const codes = ["aaa", "bbb"];
    expect(toRecovery(codes)).toEqual({ step: "recovery", recoveryCodes: codes });
  });
});

describe("buildSetupBody", () => {
  it("2FA 未有効なら body なし（初回登録はコード不要）", () => {
    expect(buildSetupBody(false, "")).toBeUndefined();
  });

  it("2FA 有効なら現行コードを含める（本人再確認）", () => {
    expect(buildSetupBody(true, " 123456 ")).toEqual({ code: "123456" });
  });
});

describe("isValidTotpCode", () => {
  it("6 桁の数字を受理する", () => {
    expect(isValidTotpCode("123456")).toBe(true);
    expect(isValidTotpCode("  654321 ")).toBe(true);
  });

  it("桁数不足 / 非数字を拒否する", () => {
    expect(isValidTotpCode("12345")).toBe(false);
    expect(isValidTotpCode("1234567")).toBe(false);
    expect(isValidTotpCode("abcdef")).toBe(false);
  });
});
