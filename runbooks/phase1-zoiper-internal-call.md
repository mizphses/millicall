# RUNBOOK: Phase 1 内線相互通話の手動検証（Zoiper 2台）

対象: Ubuntu 24.04 ホスト（設計 §12.4 で固定）。GUI は未実装のため内線登録は API 経由で行う。

## 前提

- Docker / Docker Compose v2 導入済み。
- ホストの LAN IP を確認: `ip -4 addr show | grep inet`（例: `192.168.1.10`）。
- 同一 LAN に Zoiper5（無料版）を入れた端末を2つ用意（PC + スマホ等）。

## 1. 起動

```bash
cd millicall-pbx-new
cp .env.example .env
# .env の MILLICALL_SIP_DOMAIN をホストの LAN IP に書き換える（例: 192.168.1.10）
# LAN内 HTTP 検証のため MILLICALL_COOKIE_SECURE=false のまま
docker compose up -d --build
```

core が healthy になると freeswitch が起動する。初期管理者パスワードを取得:

```bash
docker compose logs core | grep "初期管理者を作成しました"
# 例: username=admin password=XXXXXXXXXXXXXXXXXXXXXXXX
```

## 1-b. ヘルスチェック（スモークテスト）

core が完全に起動しマイグレーション・設定生成が完了したことを確認してから次のステップに進む:

```bash
curl -f http://127.0.0.1/healthz
# 期待レスポンス: {"status":"ok"}
```

このレスポンスが返れば、DB マイグレーションと FreeSwitch 設定生成が正常終了していることが確認できる。
返らない（接続エラー・非 2xx）場合は `docker compose logs core` でエラーを確認すること。

## 2. ログインして内線を2件作成（API 経由）

```bash
BASE=http://127.0.0.1
# ログイン（Cookie を保存）
curl -c cookie.txt -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"（上で取得したパスワード）"}'

# 内線 1001 / 1002 を作成（SIP パスワードは自動生成されレスポンスに含まれる）
curl -b cookie.txt -X POST "$BASE/api/extensions" \
  -H 'Content-Type: application/json' -d '{"number":"1001","display_name":"Phone A"}'
curl -b cookie.txt -X POST "$BASE/api/extensions" \
  -H 'Content-Type: application/json' -d '{"number":"1002","display_name":"Phone B"}'

# 一覧で number と sip_password を確認
curl -b cookie.txt "$BASE/api/extensions"
```

各内線の `sip_password` を控える。CRUD 実行により core が FreeSWITCH 設定を再生成し `reloadxml` を発行する（`docker compose logs core` に reloadxml ログ、失敗時は warning）。

## 3. FreeSWITCH 側の反映確認

```bash
# sofia 状態（internal profile が RUNNING、ポート 5060）
docker compose exec freeswitch fs_cli -x "sofia status"
# ユーザーが読み込まれているか
docker compose exec freeswitch fs_cli -x "list_users"
```

`1001` / `1002` が表示されれば OK。表示されない場合は
`docker compose exec freeswitch fs_cli -x "reloadxml"` を手動実行。

## 4. Zoiper 2台を登録

各 Zoiper で手動アカウント設定（SIP UDP）:

| 項目 | 端末A | 端末B |
|---|---|---|
| Username | 1001 | 1002 |
| Password | （1001 の sip_password） | （1002 の sip_password） |
| Domain / Host | ホスト LAN IP（例 192.168.1.10） | 同左 |
| Transport | UDP | UDP |

両方が「Registered（緑）」になることを確認。ならない場合は §7 参照。

## 5. 相互通話

- 端末A から `1002` に発信 → 端末B が着信 → 応答 → 双方向音声を確認。
- 端末B から `1001` に発信 → 同様に確認。
- 通話中に core ログでイベントを確認:
  ```bash
  docker compose logs -f core | grep "ESL event"
  ```
  ※ Phase 1 の core は起動時に常時イベント購読はしないため、reloadxml 経路以外の
    CHANNEL_CREATE/ANSWER/HANGUP ログは Phase 2（通話ログ ESL 直記録）で本格化する。
    本 RUNBOOK では通話成立（音声疎通）を合否基準とする。

## 6. 合否基準

- [ ] 内線2件が API のみで作成できた（GUI 不要）。
- [ ] Zoiper 2台が登録できた。
- [ ] 双方向で発着信・音声疎通ができた。

## 7. トラブルシュート

- **登録できない:** ホスト firewall で UDP 5060 と RTP(16384-32768) を許可。
  `sudo ufw allow 5060/udp && sudo ufw allow 16384:32768/udp`。
- **登録できない（認証失敗）:** `list_users` の番号と Zoiper の Username 一致、
  sip_password のコピペ誤りを確認。`docker compose exec freeswitch fs_cli -x "sofia loglevel all 9"` でログ確認。
- **片方向音声/無音:** `MILLICALL_SIP_DOMAIN` がホスト LAN IP になっているか、
  両サービスが host network で起動しているか（`docker compose ps`）を確認。
  マルチ NIC 環境（docker0 / tailscale0 等が混在する場合）は FreeSwitch が意図しない
  インターフェースに bind することがある。`.env` に `MILLICALL_SIP_BIND_IP=<LAN側IP>`
  （例: `MILLICALL_SIP_BIND_IP=192.168.1.10`）を設定してコンテナを再起動し、
  生成された `data/freeswitch/sip_profiles/internal.xml` の sip-ip / rtp-ip に
  その IP が反映されているかを確認すること:
  ```bash
  grep -E 'sip-ip|rtp-ip' data/freeswitch/sip_profiles/internal.xml
  ```
- **設定が反映されない:** `./data/freeswitch/` に XML が生成されているか確認し、
  無ければ core を再起動（`docker compose restart core`）。event_socket.conf.xml は
  FreeSWITCH 起動時のみ読むため、変更後は `docker compose restart freeswitch`。

## 8. 後片付け

```bash
docker compose down
# 完全初期化する場合（DB・secrets・生成設定を削除）
rm -rf data/
```
