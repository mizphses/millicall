# セキュリティモデル

millicall のセキュリティ設計、脅威モデル、各防御レイヤの概要です。公開前に必ず確認してください。

詳細な設定手順と公開耐性チェックリストは [RUNBOOK-phase6-auth.md](RUNBOOK-phase6-auth.md) を参照してください。

---

## デプロイモードと HTTP/TLS の扱い

millicall は「**平文 core + 任意 TLS フロント**」設計です。core 自身は証明書を持たず、
ポート **80**（または `MILLICALL_HTTP_PORT`）で平文 HTTP を配信します。TLS/443 はフロント層が担います。

| モード | LAN/外部アクセス | TLS | `COOKIE_SECURE` | セキュリティ前提 |
|---|---|---|---|---|
| **閉域** | LAN ポート 80 平文のみ | なし | `false` | **LAN の物理隔離に依存**。セッション Cookie は LAN 上で盗聴可能（設計上の受容リスク）。閉域で MCP を LAN 越しに使うことは非対応（OAuth issuer が HTTPS を要求するため） |
| **Cloudflare Tunnel** | 公開 HTTPS（`cloudflared` が終端） | Cloudflare が終端 | `true` | Cloudflare Access 併用を強く推奨。オリジン（core）との通信は HTTP だが Cloudflare 内で終端済み |
| **Tailscale Serve** | tailnet HTTPS（`tailscale serve` が終端） | Tailscale が終端 | `true` | tailnet メンバー限定。Tailscale の認証が前提 |

> **閉域モードの注意**: 平文 HTTP のため `MILLICALL_COOKIE_SECURE=false` となり、
> セッション Cookie は LAN 上で傍受可能です。物理的に隔離された信頼できる LAN での使用を前提としています。
> インターネット接続がある環境では Cloudflare または Tailscale モードを使用してください。

---

## 脅威モデルと防御方針

millicall はフレッツ光電話の HGW 配下に設置する LAN 内 PBX ですが、管理 UI を Cloudflare Tunnel 等で外部公開するユースケースを想定し、以下の脅威に対して多層防御を実装しています。

| 脅威 | 防御策 |
|---|---|
| 不正ログイン（総当たり攻撃） | Argon2 パスワードハッシュ + レート制限 + ロックアウト + TOTP 2FA |
| セッション乗っ取り | Secure/HttpOnly/SameSite Cookie + per-user session epoch（即時失効） |
| CSRF 攻撃 | Double-submit Cookie（`millicall_csrf` + `X-CSRF-Token` ヘッダ） |
| SIP フラッド / 不正発信 | nftables WAN 遮断 + FreeSWITCH ACL + 発信権限制御 |
| トールフラウド（無断国際発信） | 内線ごとの発信権限（`calling_permission`）、国際発信デフォルト禁止 |
| 秘密情報漏洩 | API キー・Tailscale auth key を DB 内で Fernet 暗号化。SCIM トークン・TOTP secret も暗号化 |
| IdP なりすまし（SAML） | IdP 証明書の out-of-band 事前共有、XSW/XXE 対策、origin 保護 |
| Docker socket 経由の特権昇格 | socket-proxy（Tecnativa）で最小 API のみ許可。core は raw socket に非接触 |
| netd 特権の悪用 | UNIX ソケット（SO_PEERCRED で core UID のみ接続可）。入力再検証。`shell=False` の argv |
| プロビジョニング情報の漏洩 | LAN CIDR 限定 + 端末ワンタイムトークン + 登録済みデバイス限定の三重ゲート |

---

## 認証・アクセス制御

### ローカル認証

- **パスワード**: Argon2id ハッシュ。デフォルト認証情報は存在せず、初回起動時に強力なランダムパスワードを自動生成
- **セッション Cookie**: `Secure`（本番）/ `HttpOnly` / `SameSite=Lax`
- **セッション失効（epoch 方式）**: ユーザーの無効化・パスワード変更・ロール変更・ログアウト全デバイスで**即時失効**。マルチプロセス構成でも DB の epoch 値が正となるため一貫した失効が保証されます
- **TOTP 2FA**: RFC 6238 準拠。リカバリコード 10 個（Argon2 ハッシュ保存）。`MILLICALL_TOTP_REQUIRED=true` で全ユーザーに登録を強制可能

### レート制限・ロックアウト

| パラメータ | デフォルト | 環境変数 |
|---|---|---|
| IP しきい値 | 10 回/300 秒 | `MILLICALL_LOGIN_MAX_ATTEMPTS` |
| ユーザー名しきい値 | 30 回/300 秒 | `MILLICALL_LOGIN_USERNAME_MAX_ATTEMPTS` |
| ロックアウト期間 | 300 秒 | `MILLICALL_LOGIN_LOCKOUT_SECONDS` |

TOTP チャレンジ（`/login/totp`・`/totp/verify`・`/totp/disable`）も同一レート制限の対象です。

### SSO（SAML / SCIM）

- SAML: 設定した IdP 証明書のみ信頼。`origin="saml"` アカウントのみ採用（ローカル admin の乗っ取り防止）
- SCIM: `origin="scim"` アカウントのみ操作可能。deprovision で既存セッション即時失効

詳細: [SSO 設定](sso.md)

---

## SIP 多層防御

SIP は UDP プロトコルを使うため、以下の多層防御で不正アクセスを防ぎます。

### 第一層: nftables（netd）

```
WAN インターフェースから SIP/RTP ポートへのアクセスをブロック
```

netd が管理する nftables テーブル（`millicall_nat`）で WAN 側からの SIP（UDP 5060/5080）および RTP（UDP 16384-32768）を遮断します。

### 第二層: FreeSWITCH ACL

`MILLICALL_SIP_TRUSTED_CIDRS`（デフォルト: RFC1918 全帯域 + loopback）で ACL `millicall_trusted` を生成し、internal/external プロファイルに `apply-inbound-acl` を適用。信頼 CIDR 以外は FreeSWITCH レベルで拒否されます。

```bash
# 信頼 CIDR の確認・変更
# ~/millicall/.env
MILLICALL_SIP_TRUSTED_CIDRS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,127.0.0.1/32
```

HGW（`192.168.1.1`）はデフォルトの `192.168.0.0/16` に内包されるため、このデフォルト設定で実着信が維持されます。

### 匿名着信拒否オプション

```bash
MILLICALL_SIP_REJECT_ANONYMOUS=false   # デフォルト・変更しないこと
```

> **警告**: NTT ひかり電話 HGW 回線は `186` プレフィックス未付与の着信では caller-ID が anonymous（非通知）になります。このオプションを `true` にすると**実着信がすべて拒否されます**。絶対に `true` にしないでください。

---

## 発信権限（トールフラウド対策）

内線ごとに発信権限を設定できます。管理 GUI `/extensions` の「発信権限」設定で変更します。

| 権限 | 発信可能範囲 |
|---|---|
| `internal` | 内線のみ |
| `domestic` | 内線 + 国内 PSTN（デフォルト） |
| `international` | 内線 + 国内 + 国際（`MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW` の許可プレフィックスとの AND） |

国際発信は**デフォルトでブロック**されます。有効化するには `/extensions` で権限を `international` に変更し、`.env` に許可プレフィックスを設定します。

---

## 秘密情報の保管

| 種類 | 保管方法 |
|---|---|
| プロバイダ API キー | DB 内 Fernet 暗号化 |
| Tailscale auth key | DB 内 SecretBox 暗号化 |
| TOTP シークレット | DB 内 Fernet 暗号化 |
| SIP パスワード | DB 内 Fernet 暗号化（内線作成時に自動生成） |
| SCIM トークン | Argon2 ハッシュ（平文は一度だけ表示） |
| TOTP リカバリコード | Argon2 ハッシュ（一度だけ表示） |
| 管理者初期パスワード | 起動ログに一度だけ表示。DB には Argon2 ハッシュ |

---

## Docker socket-proxy

`ghcr.io/tecnativa/docker-socket-proxy` が `docker.sock` を `/ro` でマウントし、以下の API のみを core に公開します。

| 許可 API | 用途 |
|---|---|
| `CONTAINERS=1` | コンテナ一覧・検査（`/system` ページ） |
| `POST=1` | POST メソッド（`restart` に必要） |
| `INFO=1` | `/info` エンドポイント |
| `VERSION=1` | `/version` エンドポイント |

IMAGES・EXEC・VOLUMES・NETWORKS・SWARM・SECRETS 等はすべて `0`（デフォルト）のままです。core・freeswitch・netd は `docker.sock` を一切マウントしません。

再起動を許可するコンテナは `MILLICALL_SYSTEM_MANAGED_CONTAINERS`（デフォルト: `core,freeswitch,netd,docker-proxy`）の allowlist で制限されます。

---

## 公開耐性チェックリスト

管理 UI を外部公開する前に以下を確認してください（[RUNBOOK-phase6-auth.md § 10](RUNBOOK-phase6-auth.md) より）。

- [ ] 初期管理者パスワードをランダム生成パスワードから変更済み（または安全に保管済み）
- [ ] `MILLICALL_COOKIE_SECURE=true` を設定済み（HTTPS 環境 = Cloudflare / Tailscale モード）
- [ ] TOTP 2FA を admin アカウントに設定済み（または `MILLICALL_TOTP_REQUIRED=true`）
- [ ] Cloudflare Access 等の前段認証を有効化済み（Cloudflare モードの場合）
- [ ] `MILLICALL_MCP_ALLOWED_HOSTS` に正しいホスト名を設定済み
- [ ] `MILLICALL_MCP_ISSUER_URL` に公開 HTTPS URL を設定済み（MCP 利用時）
- [ ] SIP ポート（UDP 5060/5080）と RTP ポート（UDP 16384-32768）が外部からアクセスできないことを確認
- [ ] 国際発信を利用しない場合は `calling_permission=domestic`（デフォルト）のままであることを確認
- [ ] 監査ログ（`/audit`）で不審なログインがないことを定期確認

詳細: [RUNBOOK-phase6-auth.md](RUNBOOK-phase6-auth.md)
