# Cloudflare Tunnel による外部公開

millicall の管理 UI を外部に公開する場合、Cloudflare Tunnel を使うことを推奨します。

## 基本方針

| サービス | 公開範囲 |
|---|---|
| 管理 UI（ポート 8000） | **Cloudflare Tunnel 経由で HTTPS 公開**（Cloudflare Access 併用を強く推奨） |
| SIP（UDP 5060/5080） | **Tunnel 対象外**。SIP は UDP プロトコルを使うため Cloudflare Tunnel では転送できません |
| RTP（UDP 16384-32768） | **Tunnel 対象外**（同上） |

> SIP/RTP は引き続き LAN 内または Tailscale 経由で使用してください。

## 管理 UI 公開の手順

### 1. Cloudflare Tunnel の作成

[Cloudflare Zero Trust](https://one.dash.cloudflare.com/) → Networks → Tunnels → 「Create a tunnel」

- Tunnel 名を設定（例: `millicall`）
- トークンが発行されるのでメモしておく

### 2. Cloudflare Tunnel をホストで起動

公式の `cloudflared` を使う方法（Docker 外で直接起動する例）:

```bash
# cloudflared インストール（例: Ubuntu amd64）
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb

# サービスとして登録
sudo cloudflared service install <トークン>
sudo systemctl start cloudflared
```

または `docker-compose.prod.yml` に `cloudflared` サービスを追記して管理する方法でも構いません（ミリコールのコアサービスとは独立して運用できます）。

### 3. パブリックホスト名の設定

Cloudflare Zero Trust Dashboard → Tunnel → Public Hostnames:

| 項目 | 値 |
|---|---|
| サブドメイン | `millicall` 等 |
| ドメイン | 管理しているドメイン（例: `example.com`） |
| サービス | `http://127.0.0.1:8000` |

これで `https://millicall.example.com` が millicall の管理 UI にマップされます。

### 4. Cloudflare Access の設定（強く推奨）

管理 UI を Cloudflare Tunnel で公開する場合、**Cloudflare Access** で認証レイヤを追加することを強く推奨します。

Zero Trust Dashboard → Access → Applications → 「Add an application」:

- Application type: `Self-hosted`
- Application domain: `millicall.example.com`
- ポリシー: メールアドレス、GitHub 組織、Google グループ等で限定

Cloudflare Access が追加認証レイヤとなり、millicall のログイン画面に到達できる人を事前にフィルタリングできます。

### 5. millicall の HTTPS 設定

Cloudflare Tunnel で HTTPS が終端されるため、millicall サーバー側は引き続き HTTP（ポート 8000）で動作します。ただし Cookie の `Secure` フラグを有効化します。

```bash
# ~/millicall/.env
MILLICALL_COOKIE_SECURE=true
```

---

## MCP（/mcp）の HTTPS 要件

MCP の OAuth 2.1 実装は **issuer URL が HTTPS であることを要求します**（`localhost` / `127.0.0.1` を除く）。Cloudflare Tunnel で `https://millicall.example.com` を払い出すことで、この要件を満たします。

```bash
# .env の MCP 設定
MILLICALL_MCP_ISSUER_URL=https://millicall.example.com
MILLICALL_MCP_ALLOWED_HOSTS=millicall.example.com,localhost,127.0.0.1
```

詳細は [MCP 利用](mcp.md) を参照してください。

---

## セキュリティ上の注意

- **SIP ポートを公開しない**: UDP 5060/5080 は外部からのアクセスを nftables でブロックしてください（[セキュリティモデル](security-model.md) 参照）
- **Cloudflare Access を必ず使う**: 管理 UI が公衆インターネットに素でさらされると、ブルートフォース攻撃のリスクがあります。Cloudflare Access の前段フィルタリングと、millicall のログイン試行レート制限を組み合わせてください
- **Cloudflare の TLS 証明書**: Cloudflare が HTTPS を終端するため、オリジンとの通信は HTTP でも構いませんが、Cloudflare Dashboard の「SSL/TLS」設定を「Flexible」から「Full」以上にすることを推奨します

詳細: [セキュリティモデル](security-model.md) / [RUNBOOK-phase6-auth.md § 10](RUNBOOK-phase6-auth.md)
