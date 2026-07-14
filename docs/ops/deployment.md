# millicall 運用手順（デプロイ / 更新 / ロールバック / バックアップ）

対象ホスト: **amd64 / arm64 Linux**。Docker Engine + Compose v2 が必要。

> **マルチアーチについて:** 全イメージ（core / freeswitch / netd）を amd64 / arm64 のマルチアーチ manifest list で公開している。FreeSWITCH は amd64 専用の `safarov/freeswitch` ベースを撤廃し、ランタイムをソースからビルドする構成に変更したため arm64 でも動作する。CI は各アーキをネイティブランナー（`ubuntu-24.04` / `ubuntu-24.04-arm`）でビルドし `docker buildx imagetools` で束ねる。

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
   `millicall-core` / `millicall-freeswitch` / `millicall-netd` を開く。
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

稼働中の millicall を新しいリリースへ更新する標準手順。**DB マイグレーションは
core 起動時に自動実行**されるため手動操作は不要。ただし更新前のバックアップを強く推奨する。

```bash
# 1) 念のためバックアップ（data/ と .env を backups/ に tar.gz）
millicallctl backup

# 2) 更新：compose を最新へ再取得 → イメージ pull → 再起動
millicallctl update

# 3) 確認：全コンテナが Up か、core ログにエラーが無いか
millicallctl ps
millicallctl logs core        # Ctrl-C で抜ける
curl -fsS http://127.0.0.1/healthz   # {"status":"ok"}（HTTP_PORT を変えている場合は :<port>）
```

`millicallctl update` は以下を行う:

1. **compose の再取得** — `docker-compose.prod.yml` を最新へ更新する（既存は `docker-compose.yml.bak`
   に退避）。**これがないと新しく追加されたサービス（例: `netd` / `docker-proxy` / `cloudflared`）が
   起動しない**ため、旧 `pull` のみの更新から挙動を変更している。
2. **`.env.example` の再取得と差分通知** — 新しく増えた `MILLICALL_*` 設定変数のうち `.env` に
   未設定のものを一覧表示する。**`.env` 本体は上書きしない**ので、必要な項目だけ手動で `.env` に
   追記する（すべて安全なデフォルトを持つため、追記は任意）。
3. **イメージ pull + `up -d`** — 変更のあったコンテナのみ再作成される。

> **millicallctl 自体の更新**: `millicallctl` は `.env`/compose とは別に配布されるため、
> コマンド自身を新しくするには `millicallctl self-update`（または install.sh の再実行）を使う。

### バージョンの指定

`.env` の `MILLICALL_VERSION` が参照タグ（既定 `latest`）。dev を試すには:

```bash
sed -i 's/^MILLICALL_VERSION=.*/MILLICALL_VERSION=dev/' ~/millicall/.env
millicallctl update
```

### 更新時の移行注意

- **HTTP ポートが 80 になった**: 以前 `:8000` で運用していた場合、管理 GUI/API のアクセス先が
  `http://<LAN-IP>/`（80）に変わる。`.env` に `MILLICALL_HTTP_PORT` を明示していなければ既定 80 が
  適用される。従来ポートを維持したい場合は `MILLICALL_HTTP_PORT=8000` を `.env` に設定する。
- **TLS フロント / cookie_secure**: Cloudflare Tunnel・Tailscale Serve 経由で公開する場合は
  `MILLICALL_COOKIE_SECURE=true` に。閉域（平文 LAN のみ）は `false` のまま。→ 「デプロイモード」節参照。
- **ネットワーク設定の再適用**: netd 関連（DHCP/NAT/ファイアウォール）の設定を変更した場合は、
  更新後に管理 GUI の `/network` から「適用」を実行する（compose 更新だけでは host 側 nftables/
  dnsmasq は再適用されない）。

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
- arm64 ホスト: 全イメージがマルチアーチ対応。Docker が manifest list からホストのアーキ（aarch64/arm64）に合ったイメージを自動 pull する。古い単一アーキイメージを掴んでいる場合は `millicallctl update` で取り直す。
