# millicall 運用手順（デプロイ / 更新 / ロールバック / バックアップ）

対象ホスト: **amd64 Linux**（FreeSWITCH イメージが amd64 専用のため）。Docker Engine + Compose v2 が必要。

> **arm64 について:** runtime base `safarov/freeswitch`（pinned digest `sha256:b31c743f…`）は単一 amd64 マニフェストであり arm64 タグが存在しません。arm64 ホストへの完全スタックのデプロイは将来課題です（FreeSWITCH を arm64 向けにソースビルドするか arm64 対応 base イメージを選定する別スパイクが必要）。

---

## デプロイモード

millicall は以下の 3 つのモードで運用できます。用途に応じて選択してください。

| モード | 概要 | COOKIE_SECURE | MCP |
|---|---|---|---|
| **閉域（air-gapped）** | LAN 内 HTTP 平文のみ。インターネット不要 | `false` | localhost 限定（LAN 越し MCP 非対応） |
| **Cloudflare Tunnel** | `cloudflared` が公開 HTTPS を張る。インターネット必要 | `true` | 公開 HTTPS URL を issuer に設定 |
| **Tailscale Serve** | `tailscale serve` が tailnet HTTPS を張る。tailnet メンバー限定 | `true` | tailnet ホスト名を issuer に設定 |

core は常にポート **80**（平文 HTTP）で動作します。TLS/443 はフロント（Cloudflare / Tailscale）に委譲され、
core 自身は証明書を持ちません。

### 閉域モード（追加設定なし）

```bash
# ~/millicall/.env
MILLICALL_COOKIE_SECURE=false   # デフォルト値
```

追加コンテナの起動は不要です。`docker compose up -d`（プロファイルなし）で起動します。

### Cloudflare Tunnel モード

```bash
# ~/millicall/.env
MILLICALL_CLOUDFLARE_TUNNEL_TOKEN=<Cloudflare で発行したトークン>
MILLICALL_COOKIE_SECURE=true
MILLICALL_MCP_ISSUER_URL=https://millicall.example.com
MILLICALL_MCP_ALLOWED_HOSTS=millicall.example.com,localhost,127.0.0.1
```

```bash
cd ~/millicall
docker compose --profile cloudflare up -d
```

詳細: [Cloudflare Tunnel 公開](cloudflare.md)

### Tailscale Serve モード

```bash
# ~/millicall/.env（Tailscale auth key は GUI /network で設定）
MILLICALL_TAILSCALE_SERVE_ENABLED=true
MILLICALL_COOKIE_SECURE=true
MILLICALL_MCP_ISSUER_URL=https://millicall.your-tailnet-name.ts.net
MILLICALL_MCP_ALLOWED_HOSTS=millicall.your-tailnet-name.ts.net,localhost,127.0.0.1
```

詳細: [Tailscale](tailscale.md)

---

## 0. 初回セットアップ（メンテナ、一度きり）

GHCR パッケージを無認証 pull できるよう public 公開する:

1. 初回リリース後、`https://github.com/users/mizphses/packages` で
   `millicall-core` / `millicall-freeswitch` を開く。
2. Package settings → Danger Zone → **Change visibility → Public**。

これにより `install.sh` の `docker compose pull` が `docker login` なしで成功する。
（private 運用にする場合は各ホストで `echo $PAT | docker login ghcr.io -u mizphses --password-stdin` が必要。）

## 1. インストール

```bash
curl -fsSL https://raw.githubusercontent.com/mizphses/millicall/main/install.sh | bash
```

対話項目: LAN IP、リリース版（`latest` 推奨）、`cookie_secure`。
生成物は `~/millicall/`（`docker-compose.yml` / `.env` / `data/`）。

インストール完了後、ブラウザで `http://<サーバのLAN-IP>/`（ポート 80、省略可）にアクセスします。

初期管理者パスワード:

```bash
millicallctl logs core | grep 初期管理者
```

## 2. 更新

```bash
millicallctl update      # docker compose pull && up -d
```

`.env` の `MILLICALL_VERSION` が参照タグ（既定 `latest`）。dev を試すには:

```bash
sed -i 's/^MILLICALL_VERSION=.*/MILLICALL_VERSION=dev/' ~/millicall/.env
millicallctl update
```

## 3. ロールバック（特定タグへ pin）

```bash
# 例: v1.2.3 へ固定
sed -i 's/^MILLICALL_VERSION=.*/MILLICALL_VERSION=v1.2.3/' ~/millicall/.env
millicallctl update
millicallctl version     # 稼働 digest を確認
```

タグは immutable（`vX.Y.Z`）を推奨。`latest` は移動するためロールバック先には使わない。

## 4. バックアップ / リストア

```bash
millicallctl backup      # ~/millicall/backups/millicall-<UTC>.tar.gz (data/ と .env)
```

整合性を最優先する場合は停止してから取得:

```bash
millicallctl down && millicallctl backup && millicallctl up
```

リストア:

```bash
cd ~/millicall
millicallctl down
tar xzf backups/millicall-<UTC>.tar.gz
millicallctl up
```

## 5. トラブルシュート

- `millicallctl ps` / `millicallctl logs core` / `millicallctl logs freeswitch`
- healthcheck: `curl http://127.0.0.1/healthz`（ポート 80）
- freeswitch が起動しない: `~/millicall/data/freeswitch/*.xml` を core が生成済みか確認
  （core が healthy になるまで freeswitch は起動しない = `depends_on: service_healthy`）。
- arm64 ホスト: 現時点で全イメージ（core / freeswitch）が amd64 専用のため pull できない。arm64 対応は将来課題。
