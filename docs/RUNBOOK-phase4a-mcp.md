# Phase 4a: MCP エージェント機能 実機検証 RUNBOOK

claude.ai カスタムコネクタ（および任意の MCP クライアント）から Millicall PBX を操作できる
MCP サーバー（15 ツール + `guide://outbound-calling` リソース）を実機で検証する。  
前提: Phase 3 実機（192.168.1.3）の着信 AI 応対が GO であること。

---

## 0. 前提

- Ubuntu 24.04 + Docker Compose v2。HGW（192.168.1.1）と同一 LAN。
- Phase 3 で必要なプロバイダ（LLM / TTS / STT）・AI エージェント・着信ルートは登録済み。
- `MILLICALL_MCP_ENABLED=true`（デフォルト有効）と以下の追加 env が揃っていること（次節参照）。
- **issuer URL は HTTPS 必須**（MCP SDK の制約。`localhost` / `127.0.0.1` のみ `http` 許可）。  
  公衆网から claude.ai カスタムコネクタを繋ぐには、**外部公開 HTTPS エンドポイント**が必要。  
  LAN 内 PC から試す場合は Cloudflare Tunnel 等で `https://<ホスト名>` を用意してから行う。
- `MILLICALL_MCP_ALLOWED_HOSTS` に本番ホスト名を必ず含めること（漏れると `/mcp` が全拒否）。
- この回線は**デフォルト非通知**。発信者番号を相手に通知したい場合は電話番号先頭に `186` を  
  前置する（例: `08056187372` → `18608056187372`）。

---

## 1. 有効化と起動

### 1-1. env の設定

`.env`（または `docker-compose.override.yml`）に以下を追記する:

```bash
# MCP 有効化（デフォルト true なので新規環境では省略可）
MILLICALL_MCP_ENABLED=true

# OAuth issuer URL — HTTPS 必須（localhost 以外）
# Cloudflare Tunnel 等で払い出した公開 URL を使う
MILLICALL_MCP_ISSUER_URL=https://<あなたのホスト名>

# DNS リバインド対策の許可 Host 名（カンマ区切り）
MILLICALL_MCP_ALLOWED_HOSTS=<あなたのホスト名>,localhost,127.0.0.1

# converse の既定エージェント ID（省略時は enabled な ai_agents の最小 id）
# MILLICALL_MCP_DEFAULT_AGENT_ID=1
```

### 1-2. 再ビルドと起動

```bash
cd /path/to/millicall-pbx-new

docker compose up -d --build
docker compose ps    # core / freeswitch が healthy/up になるまで待つ
```

### 1-3. エンドポイント疎通確認

```bash
BASE=https://<あなたのホスト名>

# OAuth メタデータが 200 で返ること
curl -s $BASE/.well-known/oauth-authorization-server | python3 -m json.tool
# issuer / authorization_endpoint / token_endpoint などが確認できる

# /mcp は Bearer なしで 401 になること
curl -s -o /dev/null -w "%{http_code}" $BASE/mcp
# -> 401
```

---

## 2. claude.ai カスタムコネクタ登録と OAuth 認可

### 2-1. カスタムコネクタの追加

1. claude.ai の「設定 > インテグレーション（MCP）」を開く。
2. 「コネクタを追加」→ MCP サーバー URL に **`https://<あなたのホスト名>/mcp`** を入力して保存。

### 2-2. OAuth 認可フロー

コネクタ追加後に OAuth フローが自動で始まる:

1. **DCR（Dynamic Client Registration）**: claude.ai が `/register` を呼び、クライアントを自動登録する。
2. **認可リダイレクト**: `/authorize` → `/mcp-login` のログイン画面（HTML フォーム）が開く。
3. **ログイン**: ユーザー名 / パスワードに Millicall の **admin または user ロール**のアカウントを入力。  
   （`mcp` ロール新設は Phase 6 送り。現状は `admin` / `user` で可。）
4. **PKCE 完了**: 認可コードが `redirect_uri` へ戻り、claude.ai が `/token` を叩いてアクセストークンを取得。

### 2-3. ツール一覧の確認

認可が通ると claude.ai 側でツール一覧が見えるようになる:

```
確認: 15 ツール（dial / say_and_listen / say / listen / hangup / converse /
      send_dtmf / transfer / get_call_status / list_active_calls /
      list_contacts / add_contact / delete_contact / list_extensions / list_trunks）
      + guide://outbound-calling リソース
      + ping（疎通確認ダミー）が表示されること
```

> **注意**: OAuth トークンはインメモリ保持のため、**プロセス再起動（`docker compose restart`）で全失効**する。  
> 再起動後は claude.ai のコネクタ設定から「再認証」を行うこと（旧実装と同等の動作）。

---

## 3. ツール疎通（電話なし）

電話を使わない読み取り系のツールで基本疎通を確認する。

### 3-1. 内線一覧

claude.ai のチャット欄で以下を入力（または MCP クライアントから直接 `call_tool`）:

```
list_extensions ツールを呼んでください
```

期待返り値（JSON 文字列）:

```json
{"count": 2, "extensions": [{"id": 1, "number": "100", "display_name": "テスト内線", "enabled": true, "type": "phone"}, ...]}
```

> `type` は v2 Extension モデルに該当カラムが無いため、互換のため固定値 `"phone"` が返る。

### 3-2. トランク一覧

```
list_trunks ツールを呼んでください
```

期待返り値:

```json
{"count": 1, "trunks": [{"id": 1, "name": "hikari", "display_name": "光電話", "did_number": "...", "caller_id": "...", "outbound_prefixes": [], "enabled": true}]}
```

> `outbound_prefixes` は互換キー維持のため常に `[]`（v2 Trunk モデルにカラム無し）。  
> `password` / `sip_password` はレスポンスに含まれない（秘密衛生）。

### 3-3. 電話帳 CRUD

```
add_contact で名前「テスト太郎」電話番号「09000000001」を追加してください
→ list_contacts で「テスト」で検索してください
→ 追加した連絡先を delete_contact で削除してください
```

期待:

- `add_contact` → `{"status": "ok", "message": "連絡先「テスト太郎」を追加しました", "contact": {...}}`
- `list_contacts` → `{"count": 1, "contacts": [{"id": N, "name": "テスト太郎", ...}]}`
- `delete_contact` → `{"status": "ok", "message": "連絡先 (ID: N) を削除しました"}`

---

## 4. 発信テスト（電話あり・ユーザー協力必須）

> **！ 注意 ！** このセクションは実際に外線へ発信します。  
> **必ず事前にユーザー（着信側）に確認を取ってから**実施してください。  
> テスト番号は `08056187372`（例）を使用。番号通知が必要な場合は `18608056187372` と入力してください。

### 4-1. dial → say → hangup（手動制御）

```
dial で 08056187372 に電話をかけてください
（相手が応答したら channel_id を教えてください）

say で「ミリコールのテストです。聞こえますか？」を話してください（channel_id を使う）

hangup で通話を終了してください
```

確認項目:

| 項目 | 期待 |
|------|------|
| `dial` 応答 | `{"channel_id": "...", "state": "Up", "message": "08056187372 が応答しました。say_and_listenで会話を始めてください。"}` |
| `say` 応答 | `{"status": "ok", "message": "「ミリコールのテスト…」を再生しました", "duration_sec": N.N}` |
| 相手の受話 | AI の発話が聞こえる |
| `hangup` | `{"status": "ok", "message": "通話を終了しました"}` |

### 4-2. say_and_listen（1 ターン会話）

```
dial で 08056187372 にかけ、応答後に
say_and_listen で「今日のご予定はいかがですか？」と話しかけてください
その後 hangup してください
```

確認項目:

- `say_and_listen` → `{"you_said": "今日のご予定は...", "they_said": "<相手の発言>", "message": "..."}` が返る。
- `they_said` に相手の発話が文字起こしされていること（STT 動作確認）。

### 4-3. 番号通知の確認

```
186 プレフィックス付き: dial で 18608056187372 に電話をかけてください
186 なし:             dial で 08056187372 に電話をかけてください
```

- **186 あり**: 相手のスマートフォンに発信者番号（トランク `caller_id`）が表示される。
- **186 なし**: 相手の画面に「非通知」または番号なしと表示される（回線がデフォルト非通知のため）。

> `caller_id` 引数を指定した場合は `origination_caller_id_number` に反映される。  
> 未指定のときはトランクの `caller_id` 設定値が継承される。

---

## 5. converse（自律会話）

`converse` は発信→AI 自律会話→切電→transcript/summary 返却を全自動で行う中核ツール。

### 5-1. 基本的な converse

> **！ 注意 ！** 実際に外線へ発信します。事前にユーザーに確認を取ってください。

```
converse で 08056187372 に電話をかけてください。
purpose: 「ラーメンを1杯注文する」
key_points: 「醤油ラーメン、大盛り、持ち帰り」
your_name: 「鈴木」
```

**内部動作**:

1. 既定エージェント（`MILLICALL_MCP_DEFAULT_AGENT_ID` or enabled な ai_agents 最小 id）の LLM / TTS / STT provider を流用して一時エージェントスペックを組む。
2. `bgapi originate {origination_uuid=<uuid>, millicall_ai_agent=ephemeral, verbose_events=true, ...} sofia/gateway/<trunk>/<番号> &park` で発信。
3. `CHANNEL_ANSWER` → `variable_millicall_ai_agent=ephemeral` を検知 → `uuid_audio_stream start ws://.../media/audio-fork/<uuid>?agent=ephemeral` が発行される（着信 AI と同一の経路に合流）。
4. `ConversationSession` が greet → on_utterance ループ → `[END_CALL]` 検知 → stop_playback + hangup。
5. `CHANNEL_HANGUP_COMPLETE` で converse が完了 Future を受け取り、transcript をロール変換（`assistant→ai`, `user→human`）し、LLM で 1〜2 文要約を生成して返す。

**期待返り値**:

```json
{
  "status": "completed",
  "phone_number": "08056187372",
  "purpose": "ラーメンを1杯注文する",
  "turns": 3,
  "summary": "醤油ラーメン大盛り持ち帰りを注文し、完了した。",
  "transcript": [
    {"turn": 0, "speaker": "ai", "text": "もしもし、鈴木と申します。..."},
    {"turn": 1, "speaker": "human", "text": "はい、何名様ですか？"},
    {"turn": 2, "speaker": "ai", "text": "1名分です。..."}
  ]
}
```

### 5-2. 遅延計測（Phase 3 同様）

```bash
docker compose logs core | grep "AI latency"
# 例: AI latency: utterance_end -> first playback = 820 ms (uuid=...)
```

目標: ローカル TTS（VOICEVOX / OpenJTalk）使用時で 1000ms 以下。

### 5-3. バージイン確認

- AI が発話中に相手（実際にあなた）が割り込んで発話する。
- AI の再生が即停止して傾聴に切り替わることを確認（Phase 3 の実証済み動作と同一）。

---

## 6. 要実機確認項目（統合テストで fake だった経路）

以下は fake ESL / fake provider でしかテストできなかった経路。実機で必ず観察・記録する:

| 確認項目 | 観察方法 | OK の条件 |
|----------|----------|-----------|
| **(a) say / listen の実挙動** | core ログ + 相手の受話 | `uuid_record start` → 音声ファイル生成 → STT → `uuid_record stop` がリークせず完了 |
| **(b) say_and_listen の実挙動** | `they_said` フィールド | 相手の発話が文字起こしされている。無発話時は `（相手の発話が検出されませんでした）` |
| **(c) send_dtmf の実挙動** | IVR ガイダンスで確認 | `send_dtmf(channel_id, "1234#")` で IVR が反応する |
| **(d) transfer の実挙動** | 内線電話で受話確認 | `transfer(channel_id, "100")` で内線 100 が鳴る |
| **(e) get_call_status** | dial 後に呼ぶ | `{"channel_id": ..., "state": "Up", ...}` が返る（`connected_name` は null） |
| **(f) converse の originate→audio_stream 合流** | core ログの `_maybe_start_audio_stream` | `CHANNEL_ANSWER` イベントに `variable_millicall_ai_agent=ephemeral` が乗り、WS が繋がる |
| **(g) [END_CALL] 自動切電** | 実通話で「ありがとうございました」等を言う | AI が終話フレーズ後に `[END_CALL]` を出力し、再生完了後に自動切断 |
| **(h) OAuth 再認証（再起動後）** | `docker compose restart core` → claude.ai 操作 | `/mcp` が 401 を返す。コネクタから再認証し直すと復帰する |

---

## 7. トラブルシュート

| 症状 | 確認 | 対処 |
|------|------|------|
| `/mcp` が 401（トークン失効） | `docker compose logs core` でプロセス再起動を確認 | claude.ai コネクタから「再認証」を実行（インメモリ OAuth の仕様） |
| `/mcp` が 4xx（認可前） | curl で `/.well-known/oauth-authorization-server` を確認 | Bearer なし → 認可フローを完了させていない。claude.ai で再度コネクタを認証 |
| issuer HTTPS エラー | `MILLICALL_MCP_ISSUER_URL` を確認 | `https://` プレフィックスが必要。`localhost` / `127.0.0.1` のみ `http` 許可 |
| `/mcp` が常に接続エラー | `MILLICALL_MCP_ALLOWED_HOSTS` を確認 | claude.ai から届く Host ヘッダ（公開ホスト名）が `allowed_hosts` に含まれていなければ 4xx で全拒否される。カンマ区切りで追加 |
| `converse` で「既定エージェントが見つかりません」エラー | `GET /api/ai-agents` で確認 | `enabled=true` の AI エージェントが 0 件。Phase 3 §4 でエージェントを作成するか `MILLICALL_MCP_DEFAULT_AGENT_ID` を設定 |
| dial で「利用可能なトランクがありません」エラー | `GET /api/trunks` で確認 | `enabled=true` のトランクが 0 件。Phase 0 の SIP トランク設定を確認 |
| dial が 30 秒でタイムアウト | core ログの originate 応答を確認 | `{"error": "30秒以内に応答がありませんでした", "channel_id": "..."}` が正常タイムアウト JSON。FreeSWITCH の ESL 接続（`127.0.0.1:8021`）が到達可能か確認 |
| say / listen で音声なし | `ls data/freeswitch/tts/` に wav が生成されるか確認 | TTS 共有ボリューム（`./data/freeswitch/tts:/app/data/freeswitch/tts`）と `MILLICALL_TTS_CACHE_DIR` が一致しているか確認 |
| converse の audio_stream が繋がらない | core ログ `audio_fork` + `CHANNEL_ANSWER` イベントを確認 | `variable_millicall_ai_agent=ephemeral` がイベントに乗っていない場合は originate コマンドの変数付与を確認（`verbose_events=true` 必須） |
| SPA が `/mcp` や `/.well-known` を横取りする | `_SPA_EXCLUDED_PREFIXES` を確認 | Task 1 で `mcp` と `.well-known` を除外プレフィクスに追加済み。main.py の `_SPA_EXCLUDED_PREFIXES` を確認 |
| converse がハングし返ってこない | core ログで `CHANNEL_HANGUP_COMPLETE` を確認 | max_turns 相応の上限時間でタイムアウトし、フォールバック `uuid_kill` が走る。ログに `HangupRegistry timeout` が出るか確認 |
| DTMF が届かない / IVR が反応しない | core ログで `uuid_send_dtmf` 発行を確認 | HGW が DTMF をインバンドでのみ受け付ける（Phase 3 の実機知見と同一）。SDP の `a=rtpmap` 設定を確認 |

---

## 8. 後片付け

```bash
docker compose --profile voicevox down    # VOICEVOX を使った場合
# または
docker compose down                       # VOICEVOX なしの場合
```

認証テスト用に追加した連絡先（§3-3 の「テスト太郎」等）を削除する:

```bash
BASE=http://127.0.0.1
curl -c cj.txt -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<パスワード>"}'

# 全連絡先を確認
curl -b cj.txt $BASE/api/contacts

# 対象 ID を指定して削除
curl -b cj.txt -X DELETE $BASE/api/contacts/<id>
```

---

## 付録: curl による MCP ツール呼び出し（デバッグ用）

OAuth フローを手動で行い、Bearer トークンを取得してから `/mcp` に直接 POST する手順。

```bash
BASE=https://<あなたのホスト名>

# 1. DCR: クライアント登録
CLIENT=$(curl -s -X POST $BASE/register \
  -H 'Content-Type: application/json' \
  -d '{"client_name":"debug","grant_types":["authorization_code"],"response_types":["code"],"redirect_uris":["http://localhost:9999/callback"],"token_endpoint_auth_method":"none"}')
CLIENT_ID=$(echo $CLIENT | python3 -c "import sys,json; print(json.load(sys.stdin)['client_id'])")
echo "client_id: $CLIENT_ID"

# 2. authorize URL を開いてブラウザでログイン（code_challenge は S256 が必要）
#    ブラウザで: $BASE/authorize?client_id=$CLIENT_ID&response_type=code&...
#    → /mcp-login で admin ログイン → redirect_uri に code が付く

# 3. /token で Bearer トークン取得
TOKEN=$(curl -s -X POST $BASE/token \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "client_id=$CLIENT_ID" \
  --data-urlencode "code=<コールバックの code>" \
  --data-urlencode "redirect_uri=http://localhost:9999/callback" \
  --data-urlencode "code_verifier=<PKCE verifier>")
ACCESS=$(echo $TOKEN | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 4. /mcp に Streamable HTTP で list_tools を呼ぶ
curl -s -X POST $BASE/mcp \
  -H "Authorization: Bearer $ACCESS" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```
