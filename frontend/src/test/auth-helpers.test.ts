import { describe, expect, it } from "vitest";

import { USER_HOME_PATH, parseLoginConfig, postLoginPath } from "../auth/auth";

/**
 * auth.ts のロール別遷移先とログイン公開設定パースのテスト。
 * - postLoginPath: admin はダッシュボード、それ以外はアカウントページ（安全側）
 * - parseLoginConfig: 想定外の形は SAML 無効として扱う（SSO ボタンを出さない安全側）
 */

describe("postLoginPath", () => {
  it("admin はダッシュボード（/）へ", () => {
    expect(postLoginPath("admin")).toBe("/");
  });

  it("user はアカウントページへ", () => {
    expect(postLoginPath("user")).toBe(USER_HOME_PATH);
    expect(USER_HOME_PATH).toBe("/settings/security");
  });

  it("未知のロール / 未定義は安全側（アカウントページ）に倒す", () => {
    expect(postLoginPath("superuser")).toBe(USER_HOME_PATH);
    expect(postLoginPath(undefined)).toBe(USER_HOME_PATH);
    expect(postLoginPath("")).toBe(USER_HOME_PATH);
  });
});

describe("parseLoginConfig", () => {
  it("saml_enabled=true を読み取る", () => {
    expect(parseLoginConfig({ saml_enabled: true })).toEqual({ saml_enabled: true });
  });

  it("saml_enabled=false を読み取る", () => {
    expect(parseLoginConfig({ saml_enabled: false })).toEqual({ saml_enabled: false });
  });

  it("想定外の形は SAML 無効として扱う（安全側）", () => {
    expect(parseLoginConfig(null)).toEqual({ saml_enabled: false });
    expect(parseLoginConfig(undefined)).toEqual({ saml_enabled: false });
    expect(parseLoginConfig({})).toEqual({ saml_enabled: false });
    expect(parseLoginConfig({ saml_enabled: "true" })).toEqual({ saml_enabled: false });
    expect(parseLoginConfig("saml_enabled")).toEqual({ saml_enabled: false });
    expect(parseLoginConfig(42)).toEqual({ saml_enabled: false });
  });
});
