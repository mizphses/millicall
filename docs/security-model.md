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
| 秘密情報漏洩 | API キー・Tailscale auth key・TOTP secret を DB 内で Fernet 暗号化。SCIM トークンは Argon2 ハッシュ |
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
| SIP 内線・トランクのパスワード | **現状は DB と生成 XML に平文保存**。data ディレクトリの権限で保護。暗号化・移行設計は [#87](https://github.com/mizphses/millicall/issues/87) |
| SCIM トークン | Argon2 ハッシュ（平文は一度だけ表示） |
| TOTP リカバリコード | Argon2 ハッシュ（一度だけ表示） |
| 管理者初期パスワード | 起動ログに一度だけ表示。DB には Argon2 ハッシュ |

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
| SIP 認証情報の保存暗号化と復旧設計 | [#87](https://github.com/mizphses/millicall/issues/87) |
| MCP ログインのブラウザフローと CSRF/CSP の整合 | [#88](https://github.com/mizphses/millicall/issues/88) |

監査はソース、API/プロトコルの自動テスト、依存監査を対象にした。実機の FreeSWITCH、電話機、外部 IdP、Linux netd/firewall、コンテナイメージの実行環境を使った侵入試験は含まない。SIP 保存暗号化を含む上記の課題は未解決。OAuth・SAML replay 状態の DB 移行（#86）は次節を参照。

## 認証状態の保存と対応プロセス構成（#86）

MCP OAuth と SAML のリプレイ防御の状態は、core の SQLite DB に保存する。

- **MCP OAuth**: クライアント登録（DCR）・認可コード・アクセス/リフレッシュトークンを `mcp_oauth_*` テーブルに保存する。認可コード・トークン・client_secret は SHA-256 ハッシュのみを保存し、DB ファイル・WAL・バックアップから有効な値を取り出せない。`/token` のクライアント認証もハッシュ同士で照合する。
- **一回限りの消費**: 認可コードの交換とリフレッシュトークンのローテーションは条件付き `DELETE ... RETURNING` をトランザクションの最初の文にして行い、新トークンの保存まで同じトランザクションで確定する（発行に失敗すると旧トークンも残る）。SQLite は書き込みを直列化するため、同じ値を複数のプロセスが同時に使っても成功は 1 回だけで、残りは `invalid_grant`（HTTP 400）になる。待機は `PRAGMA busy_timeout=5000`（5 秒）の範囲で行う。
- **SAML**: 消費済みのアサーション ID と AuthnRequest ID（InResponseTo）を `saml_consumed_ids` に SHA-256 で記録する。PRIMARY KEY への `INSERT ... ON CONFLICT DO NOTHING` の影響行数で判定し、2 つの ID は同じトランザクションで消費する。保持期間はアサーションの残り有効期間（最低で許容時刻ずれの 2 倍）と AuthnRequest の 10 分。ログイン開始時の AuthnRequest ID と遷移先は、従来どおり署名付き Cookie に保持する。
- **失効**: ユーザーの無効化・削除・資格情報変更・session_epoch の更新は DB の値で判定するため、同じ DB を使う全プロセスで次のトークン検証から反映される。TOTP 必須化（`totp_required`）は設定キャッシュ経由で判定するため、設定を変更したプロセスでは即時に、それ以外のプロセスでは再起動後に反映される（対応構成の 1 プロセスでは即時）。リフレッシュトークンの再利用は拒否するが、再利用を検知して同じ認可の全トークンを失効させる処理は行わない。
- **清掃と上限**: 期限切れの行は参照時に削除し、登録時と発行時に一括で削除する。DCR は未認証で呼べるため、登録数（1,000 件）とメタデータの大きさ（16 KiB）に上限を設ける。満杯時は一度も認可されていない登録を古い順に追い出す。値の一覧は [MCP 利用](mcp.md) を参照。

### 対応構成と非対応構成

| 構成 | 対応 | 理由 |
|---|---|---|
| core 1 プロセス（既定の `uvicorn millicall.main:app`、`--workers` なし） | **対応** | — |
| core の再起動・更新（`millicallctl update`、`docker compose restart core`） | **対応** | MCP の再認証は不要。SAML の消費済み ID も保持される |
| 同じ data_dir / DB に対する core の複数プロセス（`uvicorn --workers N`、同じボリュームを共有する複数コンテナ） | **非対応（起動を拒否）** | 下記の音声・MCP セッション状態がプロセスローカル |
| 複数ホストで DB を共有（NFS 上の SQLite 等） | **非対応** | SQLite のロックがネットワーク FS で保証されない |

認証状態はプロセス間で共有できるが、core には次の状態がプロセス内に残る。

- **ESL イベント購読**: 各プロセスが FreeSWITCH のイベントを個別に購読する。CDR の記録、着信 AI の `uuid_audio_stream` 発行、ワークフローの実行が、通話ごとにプロセスの数だけ重複する。
- **音声 WebSocket**: 着信 AI の一回限りのチケット、AI セッション、MCP `converse` の一時エージェント、DTMF の収集、応答・終話の待機がメモリ上にある。FreeSWITCH からの WebSocket が別プロセスに届くと照合できない。
- **MCP セッション**: Streamable HTTP の `mcp-session-id` はプロセス内に保持される。
- **設定キャッシュ**: 管理画面で変更した設定（`totp_required` など）は、書き込んだプロセスのキャッシュだけが破棄される。

core は起動時に `data_dir/core.lock` の排他ロック（flock）を取得する。同じ data_dir で 2 つ目の core が起動すると、エラーを出して起動しない。ロックはプロセスの終了時にカーネルが解放するため、異常終了後の再起動を誤って拒否しない。flock を使えないファイルシステムでは警告を出し、ガードを無効にして起動を続ける。ロックは data_dir 単位のため、`MILLICALL_DATABASE_URL` で別々の data_dir から同じ DB を指す 2 つの core は検出できない（非対応構成）。

