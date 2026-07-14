# クイックスタート

millicall を最短でセットアップして最初のログインまで行う手順です。

## 前提条件

| 項目 | 要件 |
|---|---|
| OS | amd64 / arm64 Linux（例: Ubuntu 24.04）。全イメージをマルチアーチで公開しているため arm64 でも動作する。 |
| Docker | Docker Engine + **Compose v2**（`docker compose version` で確認） |
| ネットワーク | NTT フレッツ光 HGW（192.168.1.1 等）と同一 LAN に接続していること |
| インターネット | GHCR (`ghcr.io`) へのアクセスが必要（イメージ pull） |

## 1. ワンライナーインストール

```bash
curl -fsSL https://raw.githubusercontent.com/mizphses/millicall/main/install.sh | bash
```

スクリプトは以下を自動実行します。

1. Docker / Compose v2 の存在チェック
2. `~/millicall/` ディレクトリを作成
3. `deploy/docker-compose.prod.yml` と `.env.example` をダウンロード
4. 対話形式で `.env` を生成（初回のみ）
   - **サーバの LAN IP**: ホストの LAN 側 IP（例: `192.168.1.10`）。`MILLICALL_SIP_DOMAIN` に設定されます
   - **リリース版**: `latest`（推奨）/ `dev` / `vX.Y.Z`
   - **cookie_secure**: HTTPS 公開（Cloudflare/Tailscale）なら `true`、LAN 閉域運用なら `false`
5. `millicallctl` を `/usr/local/bin`（または `~/.local/bin`）に配置
6. `docker compose pull && docker compose up -d` でサービスを起動

> **.env は初回のみ生成されます。** 既に存在する場合は上書きされません。

## 2. 起動確認

```bash
millicallctl ps
# または
cd ~/millicall && docker compose ps
```

`core`（healthy）と `freeswitch`（Up）が表示されれば起動完了です。  
freeswitch は core が healthy になるまで起動待ちをするため、初回は数十秒かかることがあります。

## 3. 初期管理者パスワードの確認

初期管理者パスワードは **起動ログに一度だけ** 表示されます。

```bash
millicallctl logs core | grep 初期管理者
# 例: username=admin password=XXXXXXXXXXXXXXXXXXXXXXXX
```

> このパスワードは DB に Argon2 ハッシュで保存されます。ログに残らない形で安全な場所にメモしてください。

## 4. 管理 GUI へのアクセス

ブラウザで `http://<サーバのLAN-IP>/`（ポート 80、省略可）を開き、`admin` とメモしたパスワードでログインします。

ログイン後に確認すべきページ:

| ページ | URL | 確認内容 |
|---|---|---|
| ダッシュボード | `/` | サービス状態の概要 |
| 内線 | `/extensions` | SIP 内線の登録 |
| トランク | `/trunks` | HGW 外線トランクの設定 |
| ネットワーク | `/network` | LAN/DHCP/NAT/Tailscale |

## 5. 次のステップ

- **HGW への外線登録**: [HGW/フレッツ設定](hgw-flets.md)
- **ネットワーク設定（DHCP/NAT）**: [ネットワーク設定](network.md)
- **AI プロバイダ登録**: [プロバイダ追加](providers.md)
- **更新・ロールバック**: [ops/deployment.md](ops/deployment.md)
- **LAN 外からアクセスする（リモート公開）**:
  - Cloudflare Tunnel 経由: [cloudflare.md](cloudflare.md)
  - Tailscale 経由: [tailscale.md](tailscale.md)

## millicallctl コマンド早見表

```bash
millicallctl up          # 起動
millicallctl down        # 停止
millicallctl restart     # 再起動
millicallctl update      # 更新（compose 再取得 + イメージ pull + up -d、DBマイグレーションは自動）
millicallctl self-update # millicallctl 自身を最新へ更新
millicallctl logs core   # core ログ追尾
millicallctl logs freeswitch  # freeswitch ログ追尾
millicallctl ps          # コンテナ状態確認
millicallctl backup      # data/ と .env をバックアップ
millicallctl version     # バージョン確認
```

**更新の基本**: `millicallctl backup` →  `millicallctl update` → `millicallctl ps` で確認。
`update` は compose も再取得するため新サービス（netd 等）も取り込まれ、DB マイグレーションは
core 起動時に自動実行されます。更新・ロールバック・移行注意（ポート 80 化等）の詳細手順は
[ops/deployment.md](ops/deployment.md) を参照してください。
