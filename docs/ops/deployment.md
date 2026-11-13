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
# 1) 念のためバックアップ（data/ と .env を暗号化して backups/ へ。パスフレーズを聞かれる）
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
- **MCP / SAML の認証状態が DB 保存になった**（#86）: この版への更新直後の 1 回だけ、
  claude.ai などの MCP コネクタで「再認証」が必要（旧版のトークンはメモリ上にしか無いため）。
  以降は core の再起動・更新で再認証は不要。
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

> **SIP 認証情報の暗号化（マイグレーション 0027）より前のバージョンへ戻す場合**: 旧バージョンの
> core は暗号化列を読めず、0027 も知らないため起動できない。pin する前に、新しいイメージのまま
> DB を 0027 の 1 つ前へ戻す（鍵で復号して平文列に戻る）。または 0027 適用前のバックアップを
> `millicallctl restore` で戻す。
>
> ```bash
> cd ~/millicall
> docker compose stop core
> docker compose run --rm --no-deps core \
>   uv run --no-dev alembic -x secrets_file=/app/data/secrets.json downgrade -1   # 0027 が head のとき
> # その後 MILLICALL_VERSION を旧タグへ変更して millicallctl update
> ```

## 4. バックアップ / リストア

`data/` には DB に加えて `secrets.json`（master_key）と、平文の SIP パスワードを含む
FreeSWITCH 設定が入る。そのためバックアップは**必ずパスフレーズで暗号化**する。
暗号化には `openssl enc -aes-256-cbc -pbkdf2 -iter 600000 -md sha256` を使う。
無暗号のバックアップは作らない。

`data/` の DB には MCP OAuth の登録とトークンのハッシュ、SAML の消費済み ID も含まれる。
ハッシュだけなのでバックアップから有効なトークンは取り出せない。古いバックアップに戻すと、
それ以降に認可した MCP コネクタは再認証が必要になる。

```bash
# 対話入力（確認のため 2 回。12 文字以上）
millicallctl backup      # ~/millicall/backups/millicall-<UTC>.tar.gz.enc (data/ と .env)

# cron 等の非対話実行: パスフレーズファイル（1 行目を使う。0600 で置く）か環境変数で渡す
# （どちらも未設定なら失敗し、無暗号では続行しない）
MILLICALL_BACKUP_PASSPHRASE_FILE=/root/.millicall-backup-pass millicallctl backup
MILLICALL_BACKUP_PASSPHRASE='...' millicallctl backup
```

- パスフレーズは `openssl` に `-pass file:` または `-pass env:`（別名の変数を openssl の起動にだけ渡す）で
  渡す。コマンドライン（`ps`）には出ない。`MILLICALL_BACKUP_PASSPHRASE` は読み込んだ直後に unset する。
- 成果物は 0600、`backups/` は 0700。
- **パスフレーズを失うと復元できない。** バックアップとは別の場所（パスワードマネージャ等）に保管する。
- バックアップには master_key も含まれる。パスフレーズが漏れれば SIP 認証情報も読めるため、
  漏えいの恐れがあれば内線パスワードを再生成する（下記）。

整合性を最優先する場合は停止してから取得:

```bash
millicallctl down && millicallctl backup && millicallctl up
```

リストア（停止中に実行する。稼働中のコンテナがあると拒否する）:

```bash
cd ~/millicall
millicallctl down
millicallctl restore backups/millicall-<UTC>.tar.gz.enc   # パスフレーズを聞かれる
millicallctl up
millicallctl logs core   # 「SIP 認証情報を復号できません」が出ていないこと
curl -fsS http://127.0.0.1/healthz   # {"status":"ok"}（degraded なら一部の認証情報を復号できていない）
```

- 既存の `data/` と `.env` は `data.pre-restore-<UTC>`（0700）/ `.env.pre-restore-<UTC>`（0600）に退避する。
  旧データの鍵と認証情報を含むので、動作を確認したら削除する（削除コマンドを出力する）。
- 以前の無暗号バックアップ（`millicall-<UTC>.tar.gz`）もそのまま `restore` できる。
  ファイル先頭のバイト列で形式を判別する。
- `data/` と `.env` 以外のパスや `..` を含むアーカイブ、通常ファイルとディレクトリ以外（リンク・デバイス・
  FIFO 等）を含むアーカイブは拒否する。展開時は所有者・権限を復元しない（`--no-same-owner --no-same-permissions`）。

### 鍵（secrets.json）の保管

`data/secrets.json` の `master_key` は、SIP 認証情報・AI プロバイダ API キー・TOTP シークレット・
SMTP/電話機管理者パスワード・Tailscale auth key の暗号化に使う。暗号化バックアップに含まれるが、
バックアップとは別に、パスワードマネージャ等へ `secrets.json` の写しを保管しておくと鍵を失ったときに
戻せる。DB だけを別環境へ移す場合も、同じ `secrets.json` を一緒に置く。

### SIP 認証情報の再生成と復旧

core コンテナ内で実行する。どちらも既定は dry-run で対象の一覧だけを表示し、`--yes` を付けると実行する。
新しいパスワードは表示しない。管理画面の内線「接続情報」（監査記録あり）で確認する。

```bash
cd ~/millicall
# 内線 SIP パスワードの再生成（全内線、または --number で指定）
docker compose exec core uv run --no-dev python -m millicall.tools.rotate_sip_passwords [--number 1001] --yes
# master_key を失った場合の復旧（復号できない行だけを対象にする）
docker compose exec core uv run --no-dev python -m millicall.tools.recover_sip --yes
millicallctl restart     # 起動時に FreeSWITCH 設定を再生成する
```

`secrets.json` が無く、DB に暗号化済みの値がある場合、core は起動しない（新しい鍵を黙って作らない）。
ログに `secrets ファイルがありません` が出る。まず `secrets.json` をバックアップや写しから戻す。
戻せない場合に限り、core を起動せずに `recover_sip --yes` を実行する。新しい鍵が作られるので、その後に起動する。

```bash
cd ~/millicall
docker compose run --rm --no-deps core uv run --no-dev python -m millicall.tools.recover_sip --yes
millicallctl up
```

`recover_sip` は次を行う。

- **内線**: SIP パスワードを再生成する。各端末で設定の取り直しが必要。プロビジョニング端末は
  再起動または resync で cfg を再取得させ、ソフトフォンは新しいパスワードを手で設定する。
- **トランク**: パスワードを空（再入力待ち）にして無効化する。管理画面でパスワードを再入力してから
  有効化する（パスワード未設定のまま有効化しようとすると 422 になる）。

master_key を失った場合は、master_key で暗号化していた他の設定も読めなくなる。AI プロバイダ
API キー、TOTP（再登録が必要）、SMTP/電話機管理者パスワード、Tailscale auth key を再設定する。
まずは `secrets.json` をバックアップや写しから戻せないか確認する。戻せれば再起動だけで復旧する。

### alembic を直接実行する場合

通常は core 起動時に自動でマイグレーションする。手動で `alembic` を実行する場合、0027（SIP 認証情報の
暗号化）とその downgrade には master_key が要る。鍵そのものではなく `secrets.json` のパスを渡す。

```bash
uv run --no-dev alembic -x secrets_file=/app/data/secrets.json upgrade head
```

## 5. 対応プロセス構成

core は **1 つの data_dir につき 1 プロセス**で動かす（同梱の Dockerfile は `uvicorn` を
`--workers` なしで起動する）。`--workers 2` 以上や、同じ `data/` を共有する 2 つ目の core
コンテナは非対応。core は起動時に `data/core.lock` を排他ロックし、2 つ目のプロセスは
「別の millicall core プロセスが同じ data_dir で稼働中です」と出して起動しない。
認証状態（MCP OAuth・SAML）は DB で共有できるが、ESL イベント購読・音声 WebSocket・
MCP セッションはプロセス内の状態のため。ロックは data_dir 単位なので、`MILLICALL_DATABASE_URL` で
別々の data_dir から同じ DB を共有する構成は検出できない（これも非対応）。詳細は [セキュリティモデル](../security-model.md) の
「認証状態の保存と対応プロセス構成」を参照。

## 6. トラブルシュート

- `millicallctl ps` / `millicallctl logs core` / `millicallctl logs freeswitch`
- healthcheck: `curl http://127.0.0.1/healthz`（ポート 80）
- freeswitch が起動しない: `~/millicall/data/freeswitch/*.xml` を core が生成済みか確認
  （core が healthy になるまで freeswitch は起動しない = `depends_on: service_healthy`）。
- arm64 ホスト: 全イメージがマルチアーチ対応。Docker が manifest list からホストのアーキ（aarch64/arm64）に合ったイメージを自動 pull する。古い単一アーキイメージを掴んでいる場合は `millicallctl update` で取り直す。
