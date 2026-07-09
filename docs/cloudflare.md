# Cloudflare Tunnel による外部公開

millicall の管理 UI（および MCP）を外部に公開する場合、Cloudflare Tunnel を使うことを推奨します。

## 基本方針

| サービス | 公開範囲 |
|---|---|
| 管理 UI・MCP（ポート 80） | **Cloudflare Tunnel 経由で HTTPS 公開**（Cloudflare Access 併用を強く推奨） |
| SIP（UDP 5060/5080） | **Tunnel 対象外**。SIP は UDP プロトコルを使うため Cloudflare Tunnel では転送できません |
| RTP（UDP 16384-32768） | **Tunnel 対象外**（同上） |

> SIP/RTP は引き続き LAN 内または Tailscale 経由で使用してください。

core は常に**平文 HTTP をポート 80（既定）で配信**します。TLS/443 は Cloudflare が終端するため、
core 自身は証明書を持ちません。

---

## 管理 UI 公開の手順

### 1. Cloudflare Tunnel の作成

[Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels → 「Create a tunnel」

- Tunnel 名を設定（例: `millicall`）
- トークンが発行されるのでメモしておく

### 2. .env にトークンを設定

```bash
# ~/millicall/.env
MILLICALL_CLOUDFLARE_TUNNEL_TOKEN=<発行されたトークン>
```

### 3. cloudflared の起動

`.env` に `MILLICALL_CLOUDFLARE_TUNNEL_TOKEN` を設定すると、`millicallctl up`（または `millicallctl update`）の実行時に cloudflared が**自動で起動**します。

```bash
cd ~/millicall
millicallctl up
# → "Cloudflare Tunnel トークンを検出 → cloudflared を強制起動します" とログが出て自動起動する
```

手動でプロファイルを指定して起動することも引き続き可能です。

```bash
cd ~/millicall
docker compose --profile cloudflare up -d
```

これにより `cloudflare/cloudflared:2025.5.0` が起動し、Cloudflare のエッジに接続します。

### 4. パブリックホスト名の設定

Cloudflare Zero Trust Dashboard → Networks → Tunnels → 該当トンネル → Public Hostnames:

| 項目 | 値 |
|---|---|
| サブドメイン | `millicall` 等 |
| ドメイン | 管理しているドメイン（例: `example.com`） |
| サービス | `http://localhost:80` |

これで `https://millicall.example.com` が millicall の管理 UI にマップされます。

> `localhost:80` の `:80` は省略可能ですが、明示することで `MILLICALL_HTTP_PORT` 変更時の
> 確認漏れを防げます。

### 5. Cloudflare Access の設定（強く推奨）

管理 UI を Cloudflare Tunnel で公開する場合、**Cloudflare Access** で認証レイヤを追加することを強く推奨します。

Zero Trust Dashboard → Access → Applications → 「Add an application」:

- Application type: `Self-hosted`
- Application domain: `millicall.example.com`
- ポリシー: メールアドレス、GitHub 組織、Google グループ等で限定

Cloudflare Access が追加認証レイヤとなり、millicall のログイン画面に到達できる人を事前にフィルタリングできます。

### 6. millicall の HTTPS 関連設定

Cloudflare Tunnel で HTTPS が終端されるため、millicall core 側は引き続き HTTP（ポート 80）で動作します。
以下の環境変数を `.env` で設定してください。

```bash
# ~/millicall/.env

# Cloudflare Tunnel でアクセスされるため Cookie に Secure フラグを付ける
MILLICALL_COOKIE_SECURE=true

# MCP の OAuth 2.1 issuer を公開 HTTPS URL に設定
MILLICALL_MCP_ISSUER_URL=https://millicall.example.com

# DNS リバインド保護: 公開ホスト名を許可 Host に追加
MILLICALL_MCP_ALLOWED_HOSTS=millicall.example.com,localhost,127.0.0.1
```

---

## MCP（/mcp）の HTTPS 要件

MCP の OAuth 2.1 実装は **issuer URL が HTTPS であることを要求します**（`localhost` / `127.0.0.1` を除く）。
Cloudflare Tunnel で `https://millicall.example.com` を払い出すことで、この要件を満たします。

詳細は [MCP 利用](mcp.md) を参照してください。

---

## セキュリティ上の注意

- **SIP ポートを公開しない**: UDP 5060/5080 は外部からのアクセスを nftables でブロックしてください（[セキュリティモデル](security-model.md) 参照）
- **Cloudflare Access を必ず使う**: 管理 UI が公衆インターネットに素でさらされると、ブルートフォース攻撃のリスクがあります。Cloudflare Access の前段フィルタリングと、millicall のログイン試行レート制限を組み合わせてください
- **Cloudflare の TLS 証明書**: Cloudflare が HTTPS を終端するため、オリジンとの通信は HTTP で構いません。Cloudflare Dashboard の「SSL/TLS」設定を「Flexible」ではなく「Full」以上にすることを推奨します
- **core は証明書を扱いません**: TLS/443 はすべて Cloudflare 側に委譲されます。core に証明書ファイルは不要です

詳細: [セキュリティモデル](security-model.md) / RUNBOOK-phase6-auth.md § 10（リポジトリ runbooks/ 参照）
