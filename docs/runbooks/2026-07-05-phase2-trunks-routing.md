# RUNBOOK: Phase 2 HGW トランク登録 + 外線発着信 + CDR 確認手順

対象: Ubuntu 24.04 ホスト（ホストネットワーク）。  
Tasks 1–10（trunks/routes/contacts/cdr CRUD・外線ゲートウェイ・常駐CDRリスナー・POST /api/calls）が適用済みのイメージ・コードで行うこと。

---

## 1. 前提

### 環境要件

- Ubuntu 24.04 LTS、Docker Engine 24+ / docker compose v2。
- サーバはひかり電話HGWと **同一LAN（同一セグメント）** に有線接続されていること。
- HGW管理画面で「内線設定」を1件作成し、内線番号・SIPパスワード・HGWのLAN側IPを控えていること。
- 契約電話番号（発信者番号表示用）を控えていること。

### FreeSWITCH イメージ（digest 固定）

`docker-compose.yml` の freeswitch サービスは **digest 固定** イメージを使用する（`latest` タグは浮動的で再現性なし）:

```
safarov/freeswitch@sha256:b31c743f4c911a19687c61e3214968f2a24f93f9d3d667cc26284192e158ffc6
```

FreeSWITCH 1.10.12 / 2024-08-02。spike/RUNBOOK.md と同一 digest。

### bind mount の確認（Phase 2 必須）

core が生成する設定ファイルを FreeSWITCH に供給するため、以下の bind mount がすべて `docker-compose.yml` に存在することを確認する:

```yaml
volumes:
  - ./data/freeswitch/directory/default.xml:/etc/freeswitch/directory/default.xml
  - ./data/freeswitch/directory/default:/etc/freeswitch/directory/default
  - ./data/freeswitch/dialplan/default.xml:/etc/freeswitch/dialplan/default.xml
  - ./data/freeswitch/sip_profiles/internal.xml:/etc/freeswitch/sip_profiles/internal.xml
  - ./data/freeswitch/autoload_configs/event_socket.conf.xml:/etc/freeswitch/autoload_configs/event_socket.conf.xml
  # Phase 2 追加（外線プロファイル・着信ルート）
  - ./data/freeswitch/sip_profiles/external.xml:/etc/freeswitch/sip_profiles/external.xml
  - ./data/freeswitch/dialplan/public.xml:/etc/freeswitch/dialplan/public.xml
```

Phase 2 の外線トランク・着信ルーティングは `external.xml` と `public.xml` を使う。これらの bind mount が欠けていると FreeSWITCH はバニラ設定で動き、トランク登録も着信ルーティングも機能しない。不足している場合は `docker-compose.yml` に追記してから `docker compose up -d` をやり直すこと。

### ポート整理

| プロファイル | bind ポート | 環境変数 |
|---|---|---|
| internal（内線SIP） | 5060 | `MILLICALL_SIP_PORT`（既定 5060） |
| external（外線SIP） | 5080 | `MILLICALL_EXTERNAL_SIP_PORT`（既定 5080） |
| ESL（FreeSWITCH制御） | 8021 | 固定（`event_socket.conf.xml`） |
| core HTTP API | 80 | 既定（`MILLICALL_HTTP_PORT` で変更可） |

---

## 2. 起動

```bash
cd millicall-pbx-new
cp .env.example .env && chmod 600 .env
# .env を実値で編集（最低限 MILLICALL_SIP_DOMAIN を LAN IP に）
docker compose up -d --build
```

core が healthy になると freeswitch が起動する。初期管理者パスワードを取得:

```bash
docker compose logs core | grep "初期管理者を作成しました"
# 例: username=admin password=XXXX...
```

ヘルスチェック:

```bash
curl -f http://127.0.0.1/healthz
# {"status":"ok"} が返れば DB マイグレーション・設定生成が完了
```

以降の操作は Cookie ファイル `cookie.txt` を使う:

```bash
BASE=http://127.0.0.1
curl -c cookie.txt -X POST "$BASE/api/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<初期パスワード>"}'
```

---

## 3. HGW トランク登録

### 3-1. トランク作成 API

```bash
curl -b cookie.txt -X POST "$BASE/api/trunks" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "hgw",
    "display_name": "ひかり電話",
    "host": "<HGWのLAN側IP>",
    "username": "<HGW内線番号>",
    "password": "<HGW SIPパスワード>",
    "did_number": "<契約DID番号>",
    "caller_id": "<発信者番号表示用>"
  }'
```

成功時のレスポンス例:
```json
{"id":1,"name":"hgw","display_name":"ひかり電話","host":"192.168.1.1",
 "username":"101","did_number":"0312345678","caller_id":"0312345678",
 "enabled":true,"has_password":true}
```

### 3-2. 生成ファイル確認

core が `data/freeswitch/sip_profiles/external.xml` を再生成し、FreeSWITCH に `reloadxml` を発行する。ホスト側のファイルを確認:

```bash
grep -c 'gateway name' data/freeswitch/sip_profiles/external.xml
# 1 以上が返れば gateway ブロックが生成されている
grep 'gateway name="hgw"' data/freeswitch/sip_profiles/external.xml
```

手動での reload が必要な場合:

```bash
docker compose exec freeswitch fs_cli -x 'reloadxml'
docker compose exec freeswitch fs_cli -x 'sofia profile external rescan'
```

> **注意**: `sofia profile external restart` は既存通話を切断する。通話中でなければ `rescan` よりも `restart` の方がゲートウェイ再登録まで確実。

---

## 4. REGISTER 確認

```bash
docker compose exec freeswitch fs_cli -x "sofia status gateway hgw"
```

**成功時の期待出力（抜粋）:**
```
Name       hgw
Profile    external
State      REGED
Status     UP
```

登録を5分間観察（フラップしないこと）:

```bash
watch -n 15 'docker compose exec -T freeswitch fs_cli -x "sofia status gateway hgw" | grep -E "State|Status|FailedCalls"'
```

### SIPトレースを見る（切り分け用）

```bash
docker compose exec freeswitch fs_cli -x "sofia profile external siptrace on"
docker compose logs -f freeswitch   # 生SIPを観察（REGISTER / 401チャレンジ / 200OK）
```

`State DOWN` だが通話が通る場合は HGW が OPTIONS(ping) に応答しない個体。`external.xml.j2` の `ping` を `0` に変更してコアを再起動し再検証する（spike RUNBOOK §5 同旨）。

---

## 5. 外線発信

### 5-1. オンデマンド発信 API

あらかじめ内線 1001 が存在していることを確認（[Phase 1 RUNBOOK](./phase1-zoiper-internal-call.md) 参照）:

```bash
curl -b cookie.txt -X POST "$BASE/api/calls" \
  -H 'Content-Type: application/json' \
  -d '{"from_extension":"1001","to":"0XXXXXXXXXX"}'
# 成功: {"call_uuid":"..."}
```

> **注意**: `from_extension` は内線番号（2〜6桁の数字）、`to` は発信先（0始まりの電話番号）。

### 5-2. ダイヤルプラン確認（fs_cli）

```bash
docker compose exec freeswitch fs_cli
# 対話プロンプトで:
/log 7
# 発信を行い、ダイヤルプランが outbound_external にヒットしているか確認
# "dialplan: XML Parsing <outbound_external>" のようなログが出ること
```

### 5-3. 発信者番号の確認

相手方（携帯等）で表示される番号が `trunk.caller_id` と一致すること。

### 5-4. 国際発信ブロック確認

`to` に `010...`（国際番号）または `00X...`（国内事業者選択番号を含む）を指定すると即切断される:

```bash
curl -b cookie.txt -X POST "$BASE/api/calls" \
  -H 'Content-Type: application/json' \
  -d '{"from_extension":"1001","to":"01012345678"}'
# 発信試行後、CDR に hangup_cause=CALL_REJECTED が記録されること
```

**重要**: `00X` プレフィックスは国内の事業者選択（0033-... など）も含むためデフォルトでブロックされる。特定プレフィックスを許可するには `.env` に設定:

```env
MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW=010,001
```

カンマ区切り、2〜8桁の数字のみ。設定後は `docker compose up -d` で再起動が必要（settings はプロセス起動時のみ読み込まれる）。

---

## 6. 着信ルーティング

### 6-1. 実際の destination_number を確認してからルートを登録する（重要）

ひかり電話HGWからの着信INVITEにおいて、FreeSWITCHが受け取る `destination_number` は
**通常「HGWに登録した内線番号 = trunk.username」** である（例: `"101"`）。
公衆DID番号（`0312345678` など）ではない場合がほとんど。

**まずテスト着信を行い、実際の値を観察すること:**

```bash
# テスト着信前にログ監視を開始
docker compose logs -f freeswitch &
docker compose exec freeswitch fs_cli -x "sofia profile external siptrace on"

# 携帯等からひかり電話番号へ発信（着信させる）
# ログで "INBOUND NO ROUTE: XXXX" の XXXX を確認する
docker compose logs freeswitch 2>/dev/null | grep "INBOUND NO ROUTE"
```

または fs_cli の `/log 7` でダイヤルプラン `public` を通過する INVITE の `destination_number` を直接確認する。

### 6-2. 確認した値でルートを登録

```bash
# 例: destination_number が "101"（trunk.username）だった場合
curl -b cookie.txt -X POST "$BASE/api/routes" \
  -H 'Content-Type: application/json' \
  -d '{"match_number":"101","target_type":"extension","target_value":"1001"}'

# HGW設定によってはDIDが届く場合もある（例: "0312345678"）
# curl -b cookie.txt -X POST "$BASE/api/routes" \
#   -H 'Content-Type: application/json' \
#   -d '{"match_number":"0312345678","target_type":"extension","target_value":"1001"}'
```

> **補足**: HGWによっては `extension` パラメータ（`trunk.username`）ではなくDIDが `destination_number` として届く場合がある。観測値に合わせて `match_number` を設定すること。

### 6-3. 生成ファイル確認

```bash
grep "inbound_" data/freeswitch/dialplan/public.xml
# <extension name="inbound_101"> などが生成されているか確認
```

### 6-4. 着信テスト

携帯等からひかり電話番号へ発信 → 内線 1001 が鳴ること。

確認ログ:
```bash
docker compose logs freeswitch | grep "INBOUND"
# "===== INBOUND 101 -> ext 1001 =====" が出ること
```

---

## 7. CDR 記録確認

発着信後、CDR に記録があることを確認:

```bash
curl -b cookie.txt "$BASE/api/cdr"
# 最新100件が JSON 配列で返る
```

レスポンスフィールド確認:

```json
{
  "id": 1,
  "call_uuid": "...",
  "direction": "outbound",
  "src_number": "1001",
  "dst_number": "0XXXXXXXXXX",
  "caller_id_name": "1001",
  "started_at": "2026-07-05T10:00:00",
  "answered_at": "2026-07-05T10:00:05",
  "ended_at": "2026-07-05T10:00:30",
  "duration_seconds": 30,
  "billsec_seconds": 25,
  "hangup_cause": "NORMAL_CLEARING"
}
```

direction でフィルタ:
```bash
curl -b cookie.txt "$BASE/api/cdr?direction=outbound"
curl -b cookie.txt "$BASE/api/cdr?direction=inbound"
```

### CDR が記録されない場合の切り分け

(a) 常駐リスナーの接続確認:
```bash
docker compose logs core | grep -E "ESL event listener|ESL"
# "ESL event listener connection failed" の連発がないか
```

(b) ESL パスワード一致確認:
```bash
grep 'password' data/freeswitch/autoload_configs/event_socket.conf.xml
# data/secrets.json の esl_password と一致しているか
cat data/secrets.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('esl_password','(not found)'))"
```

(c) `CHANNEL_HANGUP_COMPLETE` イベントの発火確認（fs_cli）:
```bash
docker compose exec freeswitch fs_cli
# 対話プロンプトで:
/events plain CHANNEL_HANGUP_COMPLETE
# 通話を1件終了し、イベントが流れてくることを確認
```

---

## 8. ポートコンフリクト コンティンジェンシー

### internal:5060 と external:5080 の共存未検証について

spike（Phase 0）では external プロファイル単独で 5060 を使う構成のみ実証済みであり、
**internal:5060 と external:5080 の共存は実機では未実証**（`external.xml.j2` の DISPATCH NOTE 参照）。

以下のいずれかの症状が出た場合はポートコンフリクトの疑いがある:

- REGISTER の INVITE が HGW に届かない（siptrace で発信が見えない）
- 着信 INVITE は届くが internal プロファイルで受けてしまい public コンテキストに入らない
- `sofia status` で `external` プロファイルが `RUNNING` にならない

### 切り分け手順

**Step 1: external ポートを確認**

```bash
docker compose exec freeswitch fs_cli -x "sofia status profile external"
# "sip-port: 5080" が表示されるか確認
```

**Step 2: ポートを変更して再試行**

`.env` で `MILLICALL_EXTERNAL_SIP_PORT` を別ポートに変更し再起動:

```bash
# .env
MILLICALL_EXTERNAL_SIP_PORT=5082
```

```bash
docker compose up -d
# または
docker compose restart core freeswitch
```

**Step 3（最終手段）: external を 5060 専有、internal を 5062 へ移す**

HGW によっては 5060 以外の SIP ポートへの REGISTER を受け付けない場合がある。
その場合は external を 5060 に戻し、internal を別ポートに移す:

```bash
# .env
MILLICALL_EXTERNAL_SIP_PORT=5060
MILLICALL_SIP_PORT=5062
```

内線 SIP 端末（Zoiper 等）の設定ポートも 5062 に変更すること。
設定変更後は `docker compose up -d` で再起動し、両プロファイルが RUNNING になることを確認:

```bash
docker compose exec freeswitch fs_cli -x "sofia status"
```

---

## 9. 切り分け表

| 症状 | 疑い | 確認・対処 |
|---|---|---|
| gateway が REGED にならない（401 が繰り返す） | HGW の realm/ユーザー名/パスワード誤り | `sofia status gateway hgw`、siptrace で 401→200OK チャレンジシーケンスを確認。`trunk.host` が HGW の LAN IP か |
| gateway が REGED にならない（タイムアウト） | IP 誤り・ファイアウォール・別セグメント | `docker compose exec freeswitch ping -c1 <HGW_IP>`。`ufw allow 5080/udp` |
| gateway が DOWN だが通話は通る | HGW が OPTIONS(ping) に無応答 | `external.xml.j2` の `ping` を `0` にして再起動 |
| 発信・着信で片方向音声 | sip-ip/rtp-ip が誤 NIC | `MILLICALL_SIP_BIND_IP=<LAN側IP>` を `.env` に設定して再起動。`grep -E 'sip-ip\|rtp-ip' data/freeswitch/sip_profiles/external.xml` で確認 |
| 着信が鳴らない（NO ROUTE） | route の match_number と実 destination_number が不一致 | `docker compose logs freeswitch \| grep "INBOUND NO ROUTE"` で実値を確認し route を更新 |
| 外線発信が outbound_external にヒットしない | outbound トランクが未登録/disabled | `curl -b cookie.txt $BASE/api/trunks` で enabled=true のトランクがあるか確認 |
| 010/00X が通ってしまう | allowlist 誤設定 | `MILLICALL_OUTBOUND_INTERNATIONAL_ALLOW` を空にして再起動 |
| CDR が空 | 常駐リスナー未接続 / ESL パスワード不一致 | §7 切り分け手順参照。core ログで `ESL event listener connection failed` を検索 |
| internal と外線番号の衝突 | 内線番号が 0 始まり | 内線番号は `0` 始まりを避ける（outbound_external の条件 `^(0\d+)$` と衝突） |
| external プロファイルが RUNNING にならない | ポートバインド競合 | §8 コンティンジェンシー参照 |

補助コマンド:

```bash
docker compose exec freeswitch fs_cli -x "sofia status"                       # 全プロファイル確認
docker compose exec freeswitch fs_cli -x "sofia status profile external"      # sip-ip/rtp-ip 確認
docker compose exec freeswitch fs_cli -x "sofia xmlstatus gateway hgw"        # ゲートウェイ詳細
docker compose exec freeswitch fs_cli -x "show calls"                         # アクティブ通話
docker compose exec freeswitch fs_cli -x "reloadxml"                          # XML 再読込
docker compose exec freeswitch ping -c1 <HGW_IP>                              # 疎通確認
docker compose logs -f core                                                    # core ログ
docker compose logs -f freeswitch                                              # FreeSWITCH ログ
```

---

## 10. 合否基準

- [ ] `sofia status gateway hgw` が `State REGED` を5分間フラップなく維持する。
- [ ] 外線発信: POST `/api/calls` → 相手方が鳴動・応答 → 双方向音声。
- [ ] 発信者番号: 相手方に `trunk.caller_id` が表示される。
- [ ] 国際番号ブロック: `to=010...` が `CALL_REJECTED` で即切断され CDR に記録される。
- [ ] 着信ルーティング: 外部から着信 → 内線が鳴動 → 応答 → 双方向音声。
- [ ] CDR: 発着信後に `GET /api/cdr` に該当通話が記録される（direction / src_number / dst_number / duration_seconds / billsec_seconds / hangup_cause が揃っている）。

---

## 11. 後片付け

```bash
docker compose down
# 完全初期化（DB・secrets・生成設定を削除）する場合
rm -rf data/
```
