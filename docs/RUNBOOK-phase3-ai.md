# Phase 3: 音声AIパイプライン 実機検証 RUNBOOK

着信を AI が低遅延（目標: 発話終了→AI音声開始 約1秒）で応対することを実機で確認する。  
前提: Phase 0 の HGW 検証が GO（外線着信が public dialplan に届く）であること。

## 0. 前提

- Ubuntu 24.04 + Docker Compose v2。HGW（192.168.1.1）と同一 LAN。
- FreeSWITCH イメージは `docker/freeswitch/Dockerfile` でソースビルドされた
  `mod_audio_stream` 同梱版（compose build 版）。CI/CD イメージとは別物。
- OpenAI 互換 LLM（例: GPT-4o-mini）の API キーと、Whisper 用 OpenAI キーを用意。
- ローカル TTS のみで完結させる場合は VOICEVOX profile または OpenJTalk を使う。
- HGW は SDP answer に RFC 3264 厳格。外線からの DTMF はインバンドで届く（2833 不可）。
- デフォルト非通知回線。AI 発信テスト時は発信元番号を通知したい場合に `186` プレフィクスを使う。

## 1. 起動

```bash
cd /path/to/millicall-pbx-new

# VOICEVOX を使う場合（任意 profile）
docker compose --profile voicevox up -d --build

# VOICEVOX を使わない場合（OpenJTalk / 外部 TTS のみ）
docker compose up -d --build

# 状態確認
docker compose ps    # core / freeswitch が healthy/up になるまで待つ
```

初期管理者パスワードは core ログに一度だけ表示される:

```bash
docker compose logs core | grep "初期管理者"
```

## 2. モジュール確認

```bash
docker compose exec freeswitch fs_cli -x "load mod_audio_stream"
```

`+OK Reloading XML` ではなく `Adding API Function 'uuid_audio_stream'` が含まれることを確認する  
（既にロード済みなら `Module already loaded` でも可）。

`false` / エラーが出る場合: イメージが正しい Dockerfile でビルドされているか確認し、  
`docker compose build freeswitch --no-cache` で再ビルドする。

## 3. プロバイダ登録（GUI 未実装のため API で）

まずログインしてセッション Cookie を取得する:

```bash
BASE=http://127.0.0.1:8000
curl -c cj.txt -X POST $BASE/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"<初期パスワード>"}'
```

### 3-1. LLM（OpenAI 互換）

```bash
curl -b cj.txt -X POST $BASE/api/providers \
  -H 'Content-Type: application/json' -d '{
  "name": "openai",
  "type": "llm",
  "kind": "openai_compatible",
  "config": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
  "api_key": "sk-..."
}'
```

### 3-2. TTS — VOICEVOX（ローカル）

```bash
curl -b cj.txt -X POST $BASE/api/providers \
  -H 'Content-Type: application/json' -d '{
  "name": "voicevox",
  "type": "tts",
  "kind": "voicevox",
  "config": {"engine_url": "http://127.0.0.1:50021", "speaker": 3}
}'
```

OpenJTalk で代替する場合（VOICEVOX 不要、`config` は空で可）:

```bash
curl -b cj.txt -X POST $BASE/api/providers \
  -H 'Content-Type: application/json' -d '{
  "name": "openjtalk",
  "type": "tts",
  "kind": "openjtalk",
  "config": {}
}'
```

### 3-3. STT（Whisper）

```bash
curl -b cj.txt -X POST $BASE/api/providers \
  -H 'Content-Type: application/json' -d '{
  "name": "whisper",
  "type": "stt",
  "kind": "whisper",
  "config": {"language": "ja"},
  "api_key": "sk-..."
}'
```

### 3-4. 接続テスト（`"ok": true` を確認）

```bash
# 登録順に id=1,2,3 が付与される（GET /api/providers で確認）
curl -b cj.txt -X POST $BASE/api/providers/1/test
curl -b cj.txt -X POST $BASE/api/providers/2/test
curl -b cj.txt -X POST $BASE/api/providers/3/test
```

> api_key はレスポンスにマスク表示（末尾4文字のみ）される。平文は保持されない。

## 4. AI エージェント作成

```bash
curl -b cj.txt -X POST $BASE/api/ai-agents \
  -H 'Content-Type: application/json' -d '{
  "name": "受付",
  "system_prompt": "あなたは丁寧な受付です。応答は簡潔に、最大2文で。相手が終話を示したら短い挨拶の後に [END_CALL] を付ける。",
  "greeting": "お電話ありがとうございます。ご用件をどうぞ。",
  "llm_provider_id": 1,
  "tts_provider_id": 2,
  "stt_provider_id": 3,
  "silence_end_ms": 600
}'
```

フィールド一覧（デフォルト値）:

| フィールド | 説明 | デフォルト |
|---|---|---|
| `name` | エージェント名（一意） | 必須 |
| `system_prompt` | LLM システムプロンプト | `""` |
| `greeting` | 応答直後に再生する挨拶 | `""` |
| `llm_provider_id` | LLM プロバイダ ID | 必須 |
| `tts_provider_id` | TTS プロバイダ ID | 必須 |
| `stt_provider_id` | STT プロバイダ ID | 必須 |
| `max_history` | 保持ターン数（1–50） | `10` |
| `silence_end_ms` | 無音検出で utterance 確定する ms（200–3000） | `600` |
| `enabled` | 有効/無効 | `true` |

作成されたエージェントの id（例: `1`）を次のステップで使う:

```bash
curl -b cj.txt $BASE/api/ai-agents
```

## 5. 着信ルート登録

HGW から届く `destination_number` を確認してから登録する  
（DID ではなく HGW の内線番号が届く場合あり。Phase 0 の fs_cli ログで確認）:

```bash
curl -b cj.txt -X POST $BASE/api/routes \
  -H 'Content-Type: application/json' -d '{
  "match_number": "<着信番号>",
  "target_type": "ai_agent",
  "target_value": "1"
}'
```

> `target_value` は AI エージェント id（整数）の文字列表現。  
> ルート登録時に `public.xml` が自動再生成され、FreeSWITCH に `reloadxml` が発行される。  
> 手動での `reloadxml` や `cat public.xml` での確認は不要だが、  
> core ログに `WARNING reloadxml skipped` が出た場合は ESL 疎通を確認する。

生成された dialplan は下記の形式（`set→answer→park`）になる:

```xml
<extension name="inbound_ai_<番号>">
  <condition field="destination_number" expression="^<番号>$">
    <action application="set"    data="millicall_ai_agent=1"/>
    <action application="answer"/>
    <action application="park"/>
  </condition>
</extension>
```

着信時、core は `CHANNEL_ANSWER` イベントの `variable_millicall_ai_agent` を読み取り、  
`uuid_audio_stream <uuid> start ws://127.0.0.1:8000/media/audio-fork/<uuid>?agent=1 mono 8k`  
を ESL bgapi で自動発行する（手動設定は不要）。

## 6. 要実機確認項目（実装時に E2E テストでカバーできなかった点）

以下は実機通話前に意識して観察・記録する:

| 確認項目 | 観察方法 | OK の条件 |
|---|---|---|
| **(a) `?agent=` クエリが WS ハンドシェイクに通るか** | core ログの `audio_fork` WS 接続ログを確認 | `call_uuid` と `agent_id` が正しく解析される。`agent=0` で 404/close になる場合は FS が query string を削除している — FS 側のソースで `mod_audio_stream` の接続 URL 組み立てを確認する |
| **(b) PLAYBACK_STOP の Unique-ID とレジストリキーの一致** | core ログの `PLAYBACK_STOP` 受信行を確認 | イベントの `Unique-ID` がセッションレジストリの `call_uuid` と一致し、再生完了が正しく通知される |
| **(c) レイテンシ実測** | 下記ログ確認コマンド参照 | `latency_ms` が目標 1000ms 以下（ローカル TTS 使用時） |
| **(d) バージイン動作とグレース期間** | 再生中に割り込み発話を行う | 再生が即停止し傾聴へ切り替わる。再生開始直後 300ms はバージイン無効（グレース） |

## 7. 発着信テストと遅延計測

外部の携帯から `<着信番号>` へ発信し、以下を確認する:

1. greeting が応答直後に再生される。
2. こちらが話し、無音になると約1秒で AI 応答音声が返る。
3. AI 応答の途中でこちらが話し出すと、再生が即停止して傾聴に切り替わる（バージイン）。
4. 「ありがとう、切ります」等の発話で AI が短い挨拶をして自動切断する（`[END_CALL]` トリガー）。

### レイテンシログ確認

```bash
docker compose logs core | grep "AI latency"
# 例: AI latency: utterance_end -> first playback = 780 ms (uuid=...)
```

目標: ローカル TTS（VOICEVOX/OpenJTalk）使用時で 1000ms 以下。  
外部 LLM との往復が律速になる場合はシステムプロンプトで最初の文を短く指定する。

### 会話ターン記録の確認

```bash
docker compose exec core sh -c \
  "python -c \"import sqlite3; print(sqlite3.connect('/app/data/millicall.db').execute(
    'select role, latency_ms, substr(text,1,20) from call_messages order by id desc limit 6'
  ).fetchall())\""
```

`role=assistant` の行に `latency_ms` が記録される（`role=user` は NULL）。

## 8. トラブルシュート

| 症状 | 確認 | 対処 |
|---|---|---|
| WS が繋がらない | `docker compose logs core \| grep audio-fork` | `MILLICALL_MEDIA_WS_BASE_URL` が `ws://127.0.0.1:8000` で core に到達できるか確認（host ネットワーク前提） |
| `?agent=` が届かない / `agent_id=0` になる | core の `audio_fork` ログで `agent_id` を確認 | FS の `mod_audio_stream` が query string を保持しているか確認。保持しない場合はパス側に agent を埋め込む方式を検討 |
| 音声が再生されない | `ls data/freeswitch/tts/` に wav が生成されるか確認 | TTS 共有ボリューム（`./data/freeswitch/tts:/app/data/freeswitch/tts`）が正しくマウントされ、`MILLICALL_TTS_CACHE_DIR=/app/data/freeswitch/tts` が一致しているか確認 |
| PLAYBACK_STOP が来ない / 応答が止まる | `docker compose logs core \| grep PLAYBACK_STOP` | ESL イベント購読が PLAYBACK_STOP を含むか確認。`aleg` 指定の uuid が一致しているか確認 |
| 遅延が大きい（>2秒） | `AI latency` ログ値と TTS プロバイダ種別 | VOICEVOX/OpenJTalk（ローカル）へ切替。LLM の最初の文が長すぎないかシステムプロンプトで短文化する |
| バージインが効かない | `docker compose logs core \| grep speech_start` | `silence_end_ms` を短くするか、VAD `mode` を下げる（PATCH `/api/ai-agents/<id>` で更新） |
| エコーによる誤バージイン | 同上 | VAD `mode` を上げる / グレース期間内の誤検知は無視される（デフォルト 300ms） |
| STT が空 / 幻聴 | Whisper の応答テキスト | 幻聴フィルタで空になっている可能性あり。実発話で試す。ノイズが多い場合は Whisper `language` 設定を確認 |
| reloadxml スキップ | `docker compose logs core \| grep reloadxml` | ESL 接続（デフォルト `127.0.0.1:8021`）が到達可能か確認。freeswitch コンテナが起動しているか確認 |

## 9. 後片付け

```bash
docker compose --profile voicevox down    # VOICEVOX を使った場合
# または
docker compose down                       # VOICEVOX なしの場合
```

## TODO: prod compose での有効化

`deploy/docker-compose.prod.yml` には現時点で TTS ボリューム（`./data/freeswitch/tts:/app/data/freeswitch/tts`）および `MILLICALL_TTS_CACHE_DIR` の設定が含まれていない。本番環境で AI 応対を有効化するには、`deploy/docker-compose.prod.yml` の `core` サービスに `environment: [MILLICALL_TTS_CACHE_DIR=/app/data/freeswitch/tts]` を、`freeswitch` サービスに TTS ボリュームマウントを追加する必要がある。この対応は Phase 4 以降の本番有効化タスクで実施する。
