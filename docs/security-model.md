# セキュリティモデル

millicall のセキュリティ設計、脅威モデル、各防御レイヤの概要です。公開前に必ず確認してください。

詳細な設定手順と公開耐性チェックリストは RUNBOOK-phase6-auth.md（リポジトリ runbooks/ 参照） を参照してください。

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
| セッション乗っ取り | Secure/HttpOnly/SameSite Cookie + per-user session epoch + アカウント資格情報への結合（即時失効） |
| CSRF 攻撃 | Double-submit Cookie（`millicall_csrf` + `X-CSRF-Token` ヘッダ） |
| SIP フラッド / 不正発信 | nftables WAN 遮断 + FreeSWITCH ACL + 発信権限制御 |
| トールフラウド（無断国際発信） | 内線ごとの発信権限（`calling_permission`）、国際発信デフォルト禁止 |
| 秘密情報漏洩 | API キー・Tailscale auth key・TOTP secret・SIP 内線/トランクのパスワードを DB 内で Fernet 暗号化。SCIM トークンは Argon2 ハッシュ。バックアップはパスフレーズで暗号化 |
| IdP なりすまし（SAML） | IdP 証明書の out-of-band 事前共有、XSW/XXE 対策、origin 保護 |
| Docker socket 経由の特権昇格 | socket-proxy（Tecnativa）で最小 API のみ許可。core は raw socket に非接触 |
| netd 特権の悪用 | UNIX ソケット（SO_PEERCRED で core UID のみ接続可）。入力再検証。`shell=False` の argv |
| プロビジョニング情報の漏洩 | LAN CIDR・登録済み端末の現在 IP・active/enabled 状態を検証。設定済みトークンは追加ゲート |

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

TOTP チャレンジ（`/login/totp`・`/totp/setup`・`/totp/verify`・`/totp/disable`・MCP ログイン）も同一レート制限の対象です。

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
| SIP 内線・トランクのパスワード | DB 内 SecretBox（Fernet）暗号化。FreeSWITCH XML と電話機 cfg には仕様上平文で出力し、権限で保護（下記「SIP 認証情報の保存と鍵管理」） |
| SCIM トークン | Argon2 ハッシュ（平文は一度だけ表示） |
| TOTP リカバリコード | Argon2 ハッシュ（一度だけ表示） |
| 管理者初期パスワード | 起動ログに一度だけ表示。DB には Argon2 ハッシュ |

---

## SIP 認証情報の保存と鍵管理

### どこに平文が残るか

| 場所 | 形式 | 保護 |
|---|---|---|
| DB（`data/millicall.db` と WAL） | `extensions.sip_password_encrypted` / `trunks.password_encrypted` に Fernet 暗号文のみ | master_key（`data/secrets.json`） |
| 通常ログ | 平文・暗号文とも出さない（`Extension`/`Trunk` の `repr` からも除外） | — |
| バックアップ（`millicallctl backup`） | tar.gz を AES-256-CBC（PBKDF2-SHA256 60 万回）で暗号化 | パスフレーズ。成果物は 0600 |
| FreeSWITCH 設定（`data/freeswitch/directory/default/*.xml`, `sip_profiles/external_*.xml`） | **平文**（FreeSWITCH の仕様上必要） | ファイル 0600・ディレクトリ 0700・`data/` 0700。core と FreeSWITCH は root 実行 |
| 電話機 cfg（`/provisioning/...` の応答） | **平文**（端末が登録に使う） | LAN 制限 + 登録済み端末の IP 一致 + ワンタイムトークン（[#85](https://github.com/mizphses/millicall/issues/85)） |
| credentials API（`GET /api/extensions/{id}/credentials`） | **平文** | 管理者限定。閲覧は監査ログに記録（パスワードは記録しない） |

平文が必要になるのは、FreeSWITCH 設定の生成、電話機 cfg の描画、credentials API の応答の直前だけです。復号はそこで都度行います。

### 復号に失敗したとき

master_key が DB と一致しない場合（`secrets.json` を失った、別環境のものを置いた等）、空パスワードで動作を続けることはありません（fail-closed）。

- 起動時に全件の復号を試し、失敗した内線とトランクを ERROR ログに出します。`/healthz` は HTTP 200 のまま `{"status": "degraded", "sip_credentials": "undecryptable"}` を返します。200 を維持するのは、compose の healthcheck が core を落とすと復旧操作もできなくなるためです。
- FreeSWITCH 設定は書き換えず、直前に生成した設定のまま動作します。
- credentials API・電話機 cfg・設定の再生成を伴う API は 503 を返します。

### 移行（マイグレーション 0027）

既存の平文列は 0027 で暗号化列へ移し、旧列は削除します。平文と暗号文が混在する期間はありません。core 起動時に `secrets.json` を読み、その master_key を alembic へ渡します。暗号化すべき行があるのに鍵を渡せない場合、0027 は失敗します。移行直後には `VACUUM` と `wal_checkpoint(TRUNCATE)` を実行し、旧列の平文を SQLite の空きページと WAL から消します。downgrade では鍵で復号して平文列へ戻します。

移行してもパスワード自体は変わりません。移行前の DB ファイル・バックアップ・ログに平文が残っている可能性がある場合は、移行後に内線パスワードを再生成してください（[運用手順](ops/deployment.md#sip-認証情報の再生成と復旧)）。トランクのパスワードは HGW またはプロバイダ側で変更してから、管理画面で再入力します。

### 将来の検討事項

- **内線の a1-hash 化**: FreeSWITCH は `a1-hash`（`MD5(user:realm:password)`）で平文パスワードを XML に置かずに認証できます。ただし realm に依存するため、子 LAN の適用や tailnet 経由の内線で realm が変わると登録できなくなる問題が報告されており、今回は見送りました。トランクは相手先へ認証するため平文が必要です。
- **鍵ローテーション**: SecretBox は単一鍵です。MultiFernet による鍵の追加と再暗号化は別 issue の候補です。現状、鍵を変えるには `recover_sip` で内線パスワードを再生成し、トランクを再入力する必要があります。鍵の喪失と同じ扱いです。

---

## Docker socket-proxy

`ghcr.io/tecnativa/docker-socket-proxy` が `docker.sock` を `/ro` でマウントし、以下の API のみを core に公開します。

| 許可 API | 用途 |
|---|---|
| `CONTAINERS=1` | コンテナ一覧・検査（`/system` ページ） |
| `ALLOW_RESTARTS=1` | コンテナ再起動のみ（汎用 `POST` は許可しない） |
| `INFO=1` | `/info` エンドポイント |
| `VERSION=1` | `/version` エンドポイント |

IMAGES・EXEC・VOLUMES・NETWORKS・SWARM・SECRETS 等はすべて `0`（デフォルト）のままです。core・freeswitch・netd は `docker.sock` を一切マウントしません。

再起動を許可するコンテナは `MILLICALL_SYSTEM_MANAGED_CONTAINERS`（デフォルト: `core,freeswitch,netd,docker-proxy`）の allowlist で制限されます。

---

## 公開耐性チェックリスト

管理 UI を外部公開する前に以下を確認してください（RUNBOOK-phase6-auth.md § 10（リポジトリ runbooks/ 参照） より）。

- [ ] 初期管理者パスワードをランダム生成パスワードから変更済み（または安全に保管済み）
- [ ] `MILLICALL_COOKIE_SECURE=true` を設定済み（HTTPS 環境 = Cloudflare / Tailscale モード）
- [ ] TOTP 2FA を admin アカウントに設定済み（または `MILLICALL_TOTP_REQUIRED=true`）
- [ ] Cloudflare Access 等の前段認証を有効化済み（Cloudflare モードの場合）
- [ ] `MILLICALL_MCP_ALLOWED_HOSTS` に正しいホスト名を設定済み
- [ ] `MILLICALL_MCP_ISSUER_URL` に公開 HTTPS URL を設定済み（MCP 利用時）
- [ ] SIP ポート（UDP 5060/5080）と RTP ポート（UDP 16384-32768）が外部からアクセスできないことを確認
- [ ] 国際発信を利用しない場合は `calling_permission=domestic`（デフォルト）のままであることを確認
- [ ] 監査ログ（`/audit`）で不審なログインがないことを定期確認

詳細: RUNBOOK-phase6-auth.md（リポジトリ runbooks/ 参照）


## 2026-11-10 コード監査での強化

- 音声 WebSocket は、ESL の通話開始イベントから発行した60秒有効・一回限りのトークンを要求する。通話 ID とエージェントの一致を確認し、検証前には AI セッションを生成しない。
- REST/MCP のローカルログインは有効な local アカウントに限定する。MCP でも TOTP を確認し、パスワードだけでは2FAの試行カウンタをリセットしない。
- `totp_required=true` の未登録ユーザーは、自己情報・TOTP 登録・ログアウトだけを利用できる。管理画面はセキュリティ設定へ誘導する。
- TOTP の登録・再設定で既存セッションを失効させ、操作中のブラウザに新しい Cookie を発行する。SCIM グループによるロール変更でも epoch を更新する。
- Cookie と OAuth 認可は、ユーザー ID/epoch だけでなくアカウントの資格情報にも結合する。削除後に同じ ID/ユーザー名が再利用されても以前の認可は復活しない。**この更新以前の Cookie は再ログインが必要**。
- OAuth の認可コードと refresh token は一回限りの交換とし、交換前のユーザー無効化・削除・資格情報変更も検証する。resource を refresh 後も維持し、MCP の audience 検証を有効化する。
- SAML は署名済み Assertion の issuer、全 AudienceRestriction、有効期限、bearer の Recipient / InResponseTo を確認する。ログイン開始ブラウザの署名付き Cookie と照合し、遷移先も Cookie 内の値を使う。未開始の IdP-initiated POST は受け付けない。
- SAML の本番利用は HTTPS と `cookie_secure=true` が前提。認証往復 Cookie は Secure/HttpOnly/SameSite=None を使う。HTTP 開発設定では Lax となるため、実ブラウザの cross-site POST は対象外。
- 電話機設定は登録中の端末 IP と一致する送信元のみ取得できる。DHCP による IP 変更後は端末同期が必要。LAN の IP/MAC なりすましまで認証するものではなく、LAN 隔離の前提は残る。
- 再生パスから FreeSWITCH の `app::args` を呼べないようにし、ESL・ネットワーク設定の許可文字列では末尾改行も拒否する。[FreeSWITCH のコマンド仕様](https://developer.signalwire.com/freeswitch/reference/cli-and-api/) を参照。
- 公開 HTTP 向け SSRF ガードは CGNAT などの非グローバル IP も拒否し、検証済みホストと異なる送信先を transport で拒否する。
- workflow の API 応答は展開後1 MiB・HTTP処理全体の期限を上限とする。連続音声は30秒ごとに区切り、VAD バッファの無制限成長を防ぐ。
- `secrets.json` の同時初期化で既存の鍵を上書きせず、既存ファイルにも600、data ディレクトリにも700の権限を適用する。

SAML の bearer 条件は [OASIS SAML Profiles](https://docs.oasis-open.org/security/saml/v2.0backup/saml-profiles-2.0-os.pdf) に基づく。依存関係も lockfile を更新し、MCP の修正版1.30系と修正版フロントエンドツールを使用する。

### 検証結果

- `uv run --extra dev pytest -q`: **1,461 passed**。監査前の1,422件に対して39件の回帰ケースを追加。Python 3.12 の `audioop` 非推奨警告が1件あり、失敗なし。
- `cd frontend && npm test`: **20 files / 330 tests passed**。
- `cd frontend && npm run build`: 成功。初期 JavaScript が676.71 kBとなる警告は [#82](https://github.com/mizphses/millicall/issues/82) で追跡。
- `uv run ruff check .`、`git diff --check`: 成功。
- `npm audit`: 既知の脆弱性 **0件**。
- `uv export --all-extras --no-emit-project --no-hashes` の固定バージョン一覧を `pip-audit --no-deps --disable-pip` で検査: 対象環境の87パッケージで既知の脆弱性 **0件**。Google STT などのオプション依存も含む。

### 残る実装・運用課題

| 課題 | Issue |
|---|---|
| TTS キャッシュの重複合成・設定変更・複数プロセスの一時ファイル競合 | [#80](https://github.com/mizphses/millicall/issues/80) |
| 同期ハッシュ検証・プロバイダ DNS がイベントループを停止する | [#81](https://github.com/mizphses/millicall/issues/81) |
| 管理画面の初期 JavaScript の分割 | [#82](https://github.com/mizphses/millicall/issues/82) |
| 端末同期・一覧の N+1 SQL | [#83](https://github.com/mizphses/millicall/issues/83) |
| 日付を跨ぐワークフロー時間条件 | [#84](https://github.com/mizphses/millicall/issues/84) |
| 電話機への provision_token 配送 | [#85](https://github.com/mizphses/millicall/issues/85) |
| OAuth・SAML・音声接続状態の単一ワーカー前提、登録数・TTL管理 | [#86](https://github.com/mizphses/millicall/issues/86) |
| SIP 認証情報の保存暗号化と復旧設計（対応済み: 「SIP 認証情報の保存と鍵管理」参照） | [#87](https://github.com/mizphses/millicall/issues/87) |
| MCP ログインのブラウザフローと CSRF/CSP の整合 | [#88](https://github.com/mizphses/millicall/issues/88) |

監査はソース、API/プロトコルの自動テスト、依存監査を対象にした。実機の FreeSWITCH、電話機、外部 IdP、Linux netd/firewall、コンテナイメージの実行環境を使った侵入試験は含まない。OAuth と SAML replay 管理は現在も単一プロセス前提であり、上記の課題は SIP 保存暗号化（#87）を除き未解決。
