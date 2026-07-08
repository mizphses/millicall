# Phase 6: 認証強化 RUNBOOK + 公開耐性チェックリスト

ローカル認証（Argon2 + セッション Cookie + CSRF + TOTP 2FA）、SAML 2.0 SP、SCIM 2.0、
監査ログ、ログイン試行レート制限/ロックアウト、内線ごと発信権限、SIP 多層防御（FreeSWITCH ACL）、
Docker socket-proxy を追加した。本書は設定・検証手順と、公開前チェックリストを示す。

## 0. 前提

- Phase 1–5 デプロイ済み。管理者アカウントは `ensure_admin_user` で自動作成（初回パスワードは
  自動生成／強制設定。既定認証情報は存在しない）。
- 追加 migration: 0014（User 拡張 + audit_logs）/0015（TOTP）/0016（login_attempts）/
  0017（email UNIQUE）/0018（calling_permission）。`alembic upgrade head`。

## 1. TOTP 2FA

- **登録**: GUI `/settings/security` → 「2FA を設定」→ QR/シークレットを認証アプリに登録 →
  コード入力で有効化 → **リカバリコード 10 個を一度だけ表示**（保存必須）。
- **ログイン**: パスワード認証後、TOTP 有効ユーザーはコード入力（2 段階）。リカバリコードも使用可。
- **無効化/再設定**: 現行 TOTP/リカバリコード必須（セッションのみ奪取した攻撃者による差し替え防止）。
- `MILLICALL_TOTP_REQUIRED=true` で UI が全ユーザーに登録を促す。チケット寿命 `TOTP_TICKET_MAX_AGE`（既定 120s）。

## 2. SAML 2.0 SP（SP-initiated SSO）

env（全て `MILLICALL_SAML_*`）:
```bash
MILLICALL_SAML_ENABLED=true
MILLICALL_SAML_SP_ENTITY_ID=https://<host>/saml/metadata
MILLICALL_SAML_SP_ACS_URL=https://<host>/saml/acs
MILLICALL_SAML_IDP_ENTITY_ID=<IdP EntityID>
MILLICALL_SAML_IDP_SSO_URL=<IdP SSO URL>
MILLICALL_SAML_IDP_X509_CERT="-----BEGIN CERTIFICATE-----..."   # IdP 署名証明書（out-of-band 共有）
MILLICALL_SAML_DEFAULT_ROLE=user
```
- SP メタデータ: `GET /saml/metadata`（有効時のみ）を IdP に登録。
- ログイン開始: `/saml/login`（302 で IdP へ）。ACS: `POST /saml/acs`。
- **セキュリティ**: 署名必須・設定した IdP 証明書のみ信頼・署名済みサブツリーのみ参照（XSW 対策）・
  XXE 遮断・conditions/audience/recipient/replay 検証・RelayState はローカルパス限定。
- **アカウント方針**: email で照合、`origin="saml"` のアカウントのみ採用（ローカル admin の乗っ取り防止）。
  新規は origin=saml・default_role で作成。
- **制限事項**: InResponseTo 未検証（unsolicited 受理）。login-CSRF は replay キャッシュで緩和するが
  完全ではない。厳格化が必要なら SP 発行 request-ID の永続化を追加（フォローアップ）。

### 実機 IdP 検証（要協力・未実施）
Keycloak / Entra ID / Google Workspace に対する実 SAML フロー:
1. IdP に SP メタデータを登録、IdP 署名証明書を env へ。
2. `/saml/login` → IdP ログイン → ACS 経由でセッション発行 → GUI にログインできること。
3. Keycloak をローカルコンテナで立て（`quay.io/keycloak/keycloak`）、SAML クライアントを構成して
   E2E を確認する手順を CI 化するのが理想（本環境では xmlsec1 不在のため署名検証は signxml を使用。
   実 IdP との相互運用は要実機確認）。

## 3. SCIM 2.0（自動プロビジョニング）

```bash
MILLICALL_SCIM_ENABLED=true
```
- **トークン発行**: GUI `/sso` → 「SCIM トークンを生成」→ 表示された Bearer トークンを IdP の SCIM 設定へ
  （**一度だけ表示**、DB には Argon2 ハッシュのみ保存）。
- ベース URL: `<host>/scim/v2`。Users（CRUD/PATCH）+ Groups（最小）+ discovery。
- **deprovision**: `active:false`（PATCH/PUT）または DELETE → ユーザー無効化 + **既存セッション即時失効**。
- SCIM が操作できるのは `origin="scim"` のユーザーのみ（ローカル/SAML アカウントは不可視・不可変）。
- 新規 SCIM ユーザーは role=user 固定（SCIM 経由で管理者は作れない）。

## 4. ログイン試行レート制限 / ロックアウト

- IP しきい値 `LOGIN_MAX_ATTEMPTS`（既定 10）/ ユーザー名しきい値 `LOGIN_USERNAME_MAX_ATTEMPTS`（既定 30）/
  期間 `LOGIN_LOCKOUT_SECONDS`（既定 300s）。超過で 429 + Retry-After。TOTP 経路（`/login/totp`・
  `/totp/verify`・`/totp/disable`）も対象。単一 IP の攻撃者は自 IP が先にロックされるため
  正規アカウントの DoS ロックアウトは困難。

## 5. CSRF

- double-submit cookie（`millicall_csrf` non-HttpOnly + `X-CSRF-Token` ヘッダ）。Cookie 認証の
  非 GET リクエストに必須。SAML ACS / SCIM（Bearer）/ MCP / login は除外。SPA は自動送信。

## 6. 内線ごと発信権限（トールフラウド対策）

- 内線に `calling_permission`（internal / domestic / international、既定 domestic）。GUI `/extensions` で設定。
- dialplan が発信先クラス × 権限でゲート: internal は内線のみ、domestic は国内 PSTN まで、
  international のみ国際（かつ global allowlist との AND）。国際は既定ブロック。

## 7. SIP 多層防御（FreeSWITCH ACL）

- `MILLICALL_SIP_TRUSTED_CIDRS`（既定 RFC1918 + loopback）で ACL `millicall_trusted`（default deny）を生成、
  internal/external プロファイルに `apply-inbound-acl` 適用。**HGW 192.168.1.1 は 192.168.0.0/16 に
  内包され着信は維持**。nftables（Phase 5）に次ぐ第二層。
- `MILLICALL_SIP_REJECT_ANONYMOUS`（既定 **false**）: この HGW 回線は既定非通知のため、有効化すると
  実着信を落とす。186 通知運用に切り替えた場合のみ true 検討。

## 8. Docker socket-proxy + system 管理

- compose の `docker-proxy`（Tecnativa、CONTAINERS/POST/INFO/VERSION のみ）に docker.sock を ro マウント。
  core は raw socket 非接触、`MILLICALL_DOCKER_PROXY_URL=http://127.0.0.1:2375` 経由。
- GUI `/system`: コンテナ状態表示・再起動（allowlist `SYSTEM_MANAGED_CONTAINERS` 限定）・情報。

## 9. 監査ログ

- 認証イベント（login.success/failure/lockout、totp.*、saml.*、scim.*、user.*、system.*）を
  `audit_logs` に記録。GUI `/audit`（admin）で閲覧。

## 10. 公開耐性チェックリスト（design §7 対応）

- [x] デフォルト認証情報の全廃（管理者 PW 自動生成、SIP PW 自動生成、SCIM トークン自動生成、SAML 証明書 env）
- [x] ローカル認証: Argon2 + セッション Cookie（Secure/HttpOnly/SameSite）+ CSRF + TOTP 2FA
- [x] セッション失効: per-user epoch（無効化/PW 変更/role 変更/deprovision/logout-all で即時失効）
- [x] ログイン試行レート制限・ロックアウト・監査ログ
- [x] SAML SP（署名検証・XSW/XXE 対策・origin 保護）／SCIM（Bearer・origin=scim 限定・即時失効）
- [x] トールフラウド: 国際発信デフォルト禁止 + 内線ごと発信権限
- [x] SIP 多層防御: nftables WAN 遮断（P5）+ FreeSWITCH ACL 二重拒否 + 匿名着信拒否オプション
- [x] プロビジョニング配布は LAN 限定 + ワンタイムトークン（P5）
- [x] netd=UNIX ソケットのみ（P5）、Docker 操作は socket-proxy で最小 API 限定
- [x] API キー・秘密は DB 内暗号化（Fernet: providers/tailscale/TOTP secret、Argon2: recovery/SCIM token）
- [ ] **要実機/インフラ検証**: Keycloak 実 SAML/SCIM E2E、実 IdP（Entra/Google）、FreeSWITCH ACL の実 WAN 遮断、
      socket-proxy 経由 Docker 制御、Cloudflare Tunnel 公開時は管理 UI のみ + Cloudflare Access 併用推奨

## 11. フォローアップ（既知の制限・非ブロッカー）

- SAML: InResponseTo 未検証（unsolicited 受理）。厳格化は SP request-ID 永続化で対応可。
- SCIM Groups はインメモリ（プロセスローカル、role 昇格なし）。永続化が要るなら DB 化。
- TOTP QR は自前エンコーダ。認証アプリでの実スキャン確認を推奨（手入力パスは常に動作）。
- session_epoch はステートレス Cookie 方式。マルチプロセス/水平スケール時も DB 上の epoch が正のため一貫。
