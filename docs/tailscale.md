# Tailscale 設定

Tailscale の用途は **2 種類**あります。混同しないよう注意してください。

| 用途 | 説明 | 設定場所 |
|---|---|---|
| **SIP 内線（tailnet 経由）** | tailnet 上の PC/スマホから SIP クライアント（Zoiper 等）で内線発着信する | 管理 GUI `/network` |
| **Tailscale Serve（HTTPS 公開）** | tailnet メンバーに管理 UI・MCP を HTTPS で公開する | `.env` |

---

## 1. SIP 内線（tailnet 経由）

詳細な実機確認手順は RUNBOOK-phase5-netd.md（リポジトリ runbooks/ 参照） を参照してください。

### 仕組み

- **netd** が tailscaled を内部で管理します。`/dev/net/tun` が利用可能な場合はカーネル TUN モード、ない場合は userspace-networking で動作します
- FreeSWITCH が `tailscale0` インターフェースにも待受けするため、tailnet 上の SIP クライアント（Zoiper 等）から普通の内線として発着信できます
- Tailscale の auth key は DB 内で SecretBox 暗号化保存されます（再表示されません）

```
[ tailnet 上の PC/スマホ (Zoiper) ]
        │  SIP over tailscale0
    [ millicall (FreeSWITCH) ]
        │  内線
    [ 他の内線電話機 / HGW 外線 ]
```

### /network ページでの設定手順

1. 管理 GUI `/network` を開く
2. 「Tailscale」セクションで「有効」トグルをオンにする
3. **auth key**（`tskey-auth-...`）を入力する
   - auth key は入力後に保存されますが**再表示されません**（書込専用）
   - auth key の取得: [Tailscale Admin](https://login.tailscale.com/admin/settings/keys) → Keys → Generate auth key
4. 「接続」ボタンを押す
5. 状態がページ上に表示されます（15 秒ポーリング）

> Tailscale の状態は `GET /api/network/tailscale/status` でも確認できます。

### Zoiper で内線登録する手順（tailnet 経由）

1. Tailscale が接続状態（`Status: Running`）であることを確認
2. `tailscale ip` コマンドまたは [Tailscale Admin](https://login.tailscale.com/admin/machines) でサーバの tailnet IP（例: `100.x.x.x`）を確認
3. Zoiper（または他の SIP クライアント）でアカウント設定:

| 項目 | 値 |
|---|---|
| Username | 内線番号（例: `1001`）|
| Password | 内線の SIP パスワード（`/extensions` で確認） |
| Domain / Host | millicall サーバの **tailnet IP**（例: `100.x.x.x`） |
| Transport | UDP |

4. 「Registered（緑）」になれば登録成功
5. tailnet 上から他の内線番号に発信したり、外線（HGW 経由）に着信を転送できます

### Tailscale の切断

1. 管理 GUI `/network` → Tailscale セクションで「切断」ボタンをクリック
2. netd が `tailscale down` を実行します

---

## 2. Tailscale Serve（管理 UI・MCP を tailnet で HTTPS 公開）

### 概要

`MILLICALL_TAILSCALE_SERVE_ENABLED=true` を設定すると、netd は Tailscale が接続状態（`up`）になった後に
`tailscale serve` を実行し、tailnet メンバーに対して core（ポート 80）を HTTPS で公開します。

- core 自身は引き続き平文 HTTP（ポート 80）で動作します
- TLS 終端は Tailscale が行います（core は証明書を持ちません）
- tailnet 外からはアクセスできません（tailnet メンバー限定）

### 有効化手順

1. **SIP 内線用の Tailscale 接続**（上記「1. SIP 内線」）を先に設定しておく
2. `.env` に以下を追加する

```bash
# ~/millicall/.env
MILLICALL_TAILSCALE_SERVE_ENABLED=true
MILLICALL_COOKIE_SECURE=true
```

3. netd を再起動する

```bash
cd ~/millicall
docker compose restart netd
```

4. netd が `tailscale up` 成功後に `tailscale serve` を自動実行します
5. tailnet ホスト名（例: `https://millicall.your-tailnet-name.ts.net`）でアクセス可能になります

tailnet ホスト名は [Tailscale Admin](https://login.tailscale.com/admin/machines) または
`tailscale status` で確認できます。

### MCP を Tailscale 経由で使う場合

MCP の OAuth 2.1 issuer は HTTPS URL が必要です。Tailscale Serve 経由では tailnet ホスト名が HTTPS になるため、以下を設定します。

```bash
# ~/millicall/.env
MILLICALL_MCP_ISSUER_URL=https://millicall.your-tailnet-name.ts.net
MILLICALL_MCP_ALLOWED_HOSTS=millicall.your-tailnet-name.ts.net,localhost,127.0.0.1
```

### SIP 内線との違い

| | SIP 内線（tailnet 経由） | Tailscale Serve |
|---|---|---|
| 目的 | 電話（SIP/RTP）の tailnet 転送 | 管理 UI・MCP の HTTPS 公開 |
| プロトコル | UDP（SIP/RTP） | HTTPS（TCP） |
| 設定場所 | 管理 GUI `/network` | `.env` |
| env 変数 | なし（GUI で設定） | `MILLICALL_TAILSCALE_SERVE_ENABLED=true` |
| COOKIE_SECURE | 不要 | `true` に設定すること |

> Tailscale Serve を使う前提として Tailscale 接続（SIP 内線用の auth key 設定）が完了している必要があります。

---

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| 接続ボタンを押しても状態が変わらない | `millicallctl logs netd` で tailscale の起動ログを確認 |
| auth key エラー | Tailscale Admin で有効な auth key か確認。使い捨て（one-off）か再利用可能か確認 |
| tailnet 上から SIP 登録できない | `tailscale ip` でサーバの tailnet IP を確認。Zoiper の Domain に tailnet IP を設定しているか確認 |
| 音声が通じない（片方向） | FreeSWITCH が `tailscale0` に bind しているか確認。`MILLICALL_SIP_BIND_IP` の設定を確認 |
| `/dev/net/tun` がない | userspace-networking で動作。`millicallctl logs netd` で確認 |
| Serve 有効化後に HTTPS でアクセスできない | `millicallctl logs netd` で `tailscale serve` の実行ログを確認。Tailscale が `Running` 状態か確認 |

詳細: RUNBOOK-phase5-netd.md § 4（リポジトリ runbooks/ 参照）
