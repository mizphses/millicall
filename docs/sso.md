# SSO 設定（SAML / SCIM）

millicall は SAML 2.0 SP（SP-initiated SSO）と SCIM 2.0（自動プロビジョニング）に対応しています。設定は管理 GUI `/sso` と環境変数（`.env`）で行います。

詳細な設定手順と制限事項は [RUNBOOK-phase6-auth.md](RUNBOOK-phase6-auth.md) を参照してください。

## SAML 2.0 SP 設定

### 環境変数

`~/millicall/.env` に以下を追記して `millicallctl update` で再起動します。

```bash
# SAML を有効化
MILLICALL_SAML_ENABLED=true

# SP の Entity ID（IdP に登録する識別子）
MILLICALL_SAML_SP_ENTITY_ID=https://millicall.example.com/saml/metadata

# Assertion Consumer Service URL（POST binding）
MILLICALL_SAML_SP_ACS_URL=https://millicall.example.com/saml/acs

# IdP の Entity ID（IdP のメタデータ EntityDescriptor/@entityID）
MILLICALL_SAML_IDP_ENTITY_ID=https://sts.windows.net/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx/

# IdP の SSO URL（HTTP-Redirect binding）
MILLICALL_SAML_IDP_SSO_URL=https://login.microsoftonline.com/xxxx.../saml2

# IdP の X.509 署名証明書（PEM 形式、out-of-band で取得）
MILLICALL_SAML_IDP_X509_CERT="-----BEGIN CERTIFICATE-----
MIIxxxx...
-----END CERTIFICATE-----"

# SSO 経由で新規作成するユーザーのデフォルトロール（user または admin）
MILLICALL_SAML_DEFAULT_ROLE=user
```

> 各環境変数は `MILLICALL_SAML_` プレフィックスで設定します（`config.py` の `saml_*` フィールドに対応）。

### SP メタデータの取得

`MILLICALL_SAML_ENABLED=true` が設定されると以下の URL で SP メタデータが公開されます。

```
GET https://millicall.example.com/saml/metadata
```

このメタデータを IdP に登録してください。

### Microsoft Entra ID（旧 Azure AD）での設定例

1. Azure Portal → Entra ID → エンタープライズアプリケーション → 「新しいアプリケーション」→「ギャラリー以外」
2. 「シングル サインオン」→「SAML」を選択
3. 基本 SAML 構成:
   - **識別子（エンティティ ID）**: `MILLICALL_SAML_SP_ENTITY_ID` と同じ値
   - **応答 URL（ACS URL）**: `MILLICALL_SAML_SP_ACS_URL` と同じ値
4. 「SAML 署名証明書」セクションから証明書（Base64）をダウンロードし、`MILLICALL_SAML_IDP_X509_CERT` に設定
5. 「ログイン URL」（SSO URL）を `MILLICALL_SAML_IDP_SSO_URL` に設定

### Google Workspace での設定例

1. Google Admin コンソール → アプリ → ウェブアプリとモバイルアプリ → 「アプリを追加」→「カスタム SAML アプリ」
2. IdP 情報（SSO URL・証明書・エンティティ ID）を控え、env に設定
3. ACS URL と エンティティ ID に millicall の SP 情報を入力

### ログインフロー

SSO ログインは `/saml/login` から開始します。

```
ユーザー → /saml/login → （302 リダイレクト）→ IdP ログイン画面
→ POST /saml/acs → セッション発行 → 管理 GUI
```

### セキュリティ仕様

- 署名の検証: `MILLICALL_SAML_IDP_X509_CERT` に設定した証明書のみを信頼
- XSW（XML 署名ラッピング）対策: 署名済みサブツリーのみ参照
- XXE 対策: XML 外部エンティティを無効化
- リプレイ攻撃対策: replay キャッシュを使用
- **アカウント保護**: IdP メールで照合するが、`origin="saml"` のアカウントのみ採用（ローカル admin アカウントの乗っ取りを防止）
- 新規作成ユーザーは `role=user`（SAML 経由で admin は作れません）

> **制限事項**: InResponseTo 未検証（unsolicited 受理）。完全な SP-initiated 検証が必要な場合は [RUNBOOK-phase6-auth.md § 11](RUNBOOK-phase6-auth.md) を参照。

---

## SCIM 2.0 自動プロビジョニング

SCIM を使うと、IdP（Entra ID / Google Workspace 等）からユーザーの作成・更新・無効化を自動化できます。

### 有効化

```bash
# .env に追記
MILLICALL_SCIM_ENABLED=true
```

`millicallctl update` で再起動後、管理 GUI `/sso` に「SCIM」セクションが表示されます。

### SCIM トークンの生成

1. 管理 GUI `/sso` を開く
2. 「SCIM トークンを生成」ボタンをクリック
3. 表示された Bearer トークンを**必ず控える**（**一度だけ表示されます**）
4. DB にはトークンの Argon2 ハッシュのみ保存されます（平文は保持されません）

### IdP への設定値

| 項目 | 値 |
|---|---|
| SCIM ベース URL | `https://millicall.example.com/scim/v2` |
| 認証方式 | Bearer トークン |
| トークン | 上記で生成したトークン |

### SCIM 仕様

- **対象**: `origin="scim"` のユーザーのみ。ローカルアカウントや SAML アカウントは SCIM で操作できません
- **操作**: ユーザーの CRUD / PATCH + グループ（最小実装）+ discovery
- **デプロビジョニング**: `active: false`（PATCH/PUT）または DELETE → ユーザー無効化 + **既存セッション即時失効**
- **ロール**: 新規 SCIM ユーザーは `role=user` 固定（SCIM 経由で admin は作れません）

> Groups はインメモリ管理のため、プロセス再起動でリセットされます。role 昇格には使えません。

---

## TOTP 2FA（個人設定）

SAML / SCIM とは独立して、ローカルアカウントに TOTP 2FA を設定できます。

1. 管理 GUI `/settings/security` → 「2FA を設定」
2. QR コードまたはシークレットを認証アプリ（Google Authenticator / Authy 等）に登録
3. ワンタイムコードを入力して有効化
4. **リカバリコード 10 個が一度だけ表示されます**（安全な場所に保管してください）

`MILLICALL_TOTP_REQUIRED=true` を設定すると、全ユーザーに TOTP 登録が促されます。

詳細: [RUNBOOK-phase6-auth.md § 1](RUNBOOK-phase6-auth.md)
