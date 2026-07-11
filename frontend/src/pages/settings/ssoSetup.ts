/**
 * SSO / プロビジョニング設定の入力補助ロジック（純関数）。
 *
 * SettingsPage の SSO セクションで使う:
 *   - IdP フェデレーションメタデータ取込レスポンス → フォーム値への変換
 *   - 現在アクセス中のドメイン（origin）からの SP 設定 / SCIM テナント URL の組み立て
 *
 * DOM に依存しない純関数のみを置く（vitest environment: node でテストするため）。
 */

/** POST /api/settings/saml/fetch-idp-metadata のレスポンス型。 */
export type IdpMetadataResponse = {
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
};

/** IdP メタデータ取込レスポンスをフォームの values（設定キー名）に変換する。 */
export function idpMetadataToFormValues(resp: IdpMetadataResponse): Record<string, string> {
  return {
    saml_idp_entity_id: resp.idp_entity_id,
    saml_idp_sso_url: resp.idp_sso_url,
    saml_idp_x509_cert: resp.idp_x509_cert,
  };
}

/** origin（例 https://pbx.example.com）から SP Entity ID / ACS URL のフォーム値を組み立てる。 */
export function spValuesFromOrigin(origin: string): Record<string, string> {
  const base = origin.replace(/\/+$/, "");
  return {
    saml_sp_entity_id: `${base}/saml/metadata`,
    saml_sp_acs_url: `${base}/saml/acs`,
  };
}

/** origin から SCIM テナント URL（IdP のプロビジョニング設定に入れる値）を組み立てる。 */
export function scimTenantUrlFromOrigin(origin: string): string {
  return `${origin.replace(/\/+$/, "")}/scim/v2`;
}

/** location.protocol が https かどうか（自動入力ボタンの有効化判定）。 */
export function isHttpsProtocol(protocol: string): boolean {
  return protocol === "https:";
}

/**
 * メタデータ URL の事前バリデーション。
 * エラーメッセージを返す（問題なければ null）。サーバー側でも同じ検証を行う。
 */
export function validateMetadataUrl(url: string): string | null {
  const trimmed = url.trim();
  if (trimmed === "") return "メタデータ URL を入力してください";
  if (!/^https:\/\//i.test(trimmed)) return "メタデータ URL は https のみ使用できます";
  return null;
}
