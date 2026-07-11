/**
 * SSO / プロビジョニング入力補助（settings/ssoSetup.ts）のユニットテスト。
 *
 * DOM 不要の純関数のみをテストする（network-form.test.ts と同スタイル）。
 */
import { describe, it, expect } from "vitest";

import {
  idpMetadataToFormValues,
  isHttpsProtocol,
  scimTenantUrlFromOrigin,
  spValuesFromOrigin,
  validateMetadataUrl,
} from "../pages/settings/ssoSetup";

describe("idpMetadataToFormValues — 取込レスポンスをフォーム値に変換", () => {
  it("設定キー名（saml_idp_*）にマッピングする", () => {
    const values = idpMetadataToFormValues({
      idp_entity_id: "https://sts.windows.net/tenant/",
      idp_sso_url: "https://login.microsoftonline.com/tenant/saml2",
      idp_x509_cert: "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----",
    });
    expect(values).toEqual({
      saml_idp_entity_id: "https://sts.windows.net/tenant/",
      saml_idp_sso_url: "https://login.microsoftonline.com/tenant/saml2",
      saml_idp_x509_cert: "-----BEGIN CERTIFICATE-----\nMIIC\n-----END CERTIFICATE-----",
    });
  });
});

describe("spValuesFromOrigin — SP 設定の自動入力値", () => {
  it("origin から SP Entity ID / ACS URL を組み立てる", () => {
    expect(spValuesFromOrigin("https://pbx.example.com")).toEqual({
      saml_sp_entity_id: "https://pbx.example.com/saml/metadata",
      saml_sp_acs_url: "https://pbx.example.com/saml/acs",
    });
  });

  it("末尾スラッシュ付き origin でも二重スラッシュにならない", () => {
    expect(spValuesFromOrigin("https://pbx.example.com/")).toEqual({
      saml_sp_entity_id: "https://pbx.example.com/saml/metadata",
      saml_sp_acs_url: "https://pbx.example.com/saml/acs",
    });
  });
});

describe("scimTenantUrlFromOrigin — SCIM テナント URL", () => {
  it("origin + /scim/v2 を返す", () => {
    expect(scimTenantUrlFromOrigin("https://pbx.example.com")).toBe(
      "https://pbx.example.com/scim/v2",
    );
  });
});

describe("isHttpsProtocol — HTTPS 判定", () => {
  it("https: のみ true", () => {
    expect(isHttpsProtocol("https:")).toBe(true);
    expect(isHttpsProtocol("http:")).toBe(false);
    expect(isHttpsProtocol("")).toBe(false);
  });
});

describe("validateMetadataUrl — メタデータ URL の事前検証", () => {
  it("https URL は null（エラーなし）", () => {
    expect(
      validateMetadataUrl(
        "https://login.microsoftonline.com/tenant/federationmetadata/2007-06/federationmetadata.xml?appid=x",
      ),
    ).toBeNull();
  });

  it("空文字・http はエラーメッセージを返す", () => {
    expect(validateMetadataUrl("")).toMatch(/入力/);
    expect(validateMetadataUrl("   ")).toMatch(/入力/);
    expect(validateMetadataUrl("http://idp.example.com/metadata.xml")).toMatch(/https/);
  });
});
