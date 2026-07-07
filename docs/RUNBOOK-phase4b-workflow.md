# Phase 4b: ワークフロー機能 実機検証 RUNBOOK

着信フローを GUI（xyflow エディタ）または API で定義し、FreeSWITCH の parked チャネル上で
ステートマシンとして実行する機能の運用・検証手順。IVR（DTMF メニュー／音声メール）、
AI 会話、条件分岐、API 連携、メール通知などをノードの組み合わせで構成する。

## 0. 前提

- Phase 3（音声 AI パイプライン）・Phase 4a（MCP）がデプロイ済みで、プロバイダ（LLM/TTS/STT）が
  登録済みであること。ワークフローの音声系ノードは Phase 3 と同じ TTS/STT を使う。
- 管理 GUI（Phase 7）が稼働していること。ワークフローエディタは `/workflows`。
- 実機構成: `mizphses@192.168.1.3`、HGW 192.168.1.1（NTT 東、内線 30、契約番号 0445895782、
  テスト用携帯 08056187372）。**電話を伴うテストは必ず事前にユーザーへ確認する。**

## 1. 有効化と起動

ワークフロー機能自体はコア機能で、追加の有効化フラグは不要。メール通知ノードを使う場合のみ
SMTP を設定する。

### 1-1. SMTP 設定（email_notify ノードを使う場合）

env（`MILLICALL_SMTP_*`）:

```bash
# 空 (smtp_host="") ならメール送信は無効。email_notify ノードは "error" 分岐へ落ちる。
MILLICALL_SMTP_HOST=smtp.example.com
MILLICALL_SMTP_PORT=587
MILLICALL_SMTP_USERNAME=notify@example.com
MILLICALL_SMTP_PASSWORD=********
MILLICALL_SMTP_FROM=notify@example.com   # 省略時は username を使う
MILLICALL_SMTP_STARTTLS=true
MILLICALL_SMTP_TIMEOUT=15
```

宛先・件名はワークフロー変数を差し込めるが、送信側で CR/LF を含む値はヘッダインジェクション
として拒否される（`SmtpEmailSender` が `EmailMessage` API で組み立て、事前検証する）。

### 1-2. 再ビルドと起動

```bash
cd ~/millicall-prod
docker compose pull && docker compose up -d
# フロント（xyflow 同梱）を含む core イメージを使う
```

## 2. ワークフローの作成

### 2-1. GUI エディタ（推奨）

1. `/workflows` を開き「新規作成」→ 名前・着信番号（`number`）を入力。
   - `number` は UNIQUE。保存時に同番号の Route（`target_type=workflow`）が自動生成される。
2. 「編集」でエディタを開く。左のパレットからノードをドラッグして配置。
   - カテゴリ: common / ivr / ai_workflow / special。
3. ノードを選択し右パネルでコンフィグを編集（フォームは各ノード種別の config_schema から自動生成）。
4. ノードの出力ハンドル（下部）から次ノードの入力ハンドル（上部）へドラッグしてエッジを張る。
   - `dtmf_input` / `intent_detection` はコンフィグ次第でハンドルが動的に変わる。
5. 「保存」。定義が不正なら 422 でバリデーションエラーが表示される（保存されない）。
   到達不能ノード等は warnings として表示（保存はされる）。
6. 「AI 生成」に自然文プロンプトを入れると `/generate` が定義の叩き台を作る。

### 2-2. API（自動化・確認用）

```bash
# ノード種別カタログ（パレット・フォーム駆動用）
curl -s http://192.168.1.3:8000/api/workflows/node-types | jq '.[].type'

# ワークフロー作成（definition は {nodes, edges}）
curl -s -X POST http://192.168.1.3:8000/api/workflows \
  -H 'Content-Type: application/json' -b cookie.txt \
  -d '{"name":"受付IVR","number":"30","definition":{"nodes":[...],"edges":[...]}}'
# 不正な definition -> 422 / number 重複 -> 409
```

## 3. ノード種別リファレンス（19 種）

| カテゴリ | type | 用途 | 主な出力ハンドル |
|---|---|---|---|
| common | start | 開始（ring_count で応答前コール数） | out |
| common | end / hangup | 終了 / 切断 | （終端） |
| common | play_audio | 音声/TTS 再生 | out |
| common | transfer | 転送（blind） | （終端） |
| common | condition | 変数の条件分岐 | true / false |
| common | set_variable | 変数設定 | out |
| common | goto | 別ノードへジャンプ | （終端／循環検出あり） |
| ivr | dtmf_input | DTMF 収集（桁数/終端キー/タイムアウト） | 0..9 / done / timeout |
| ivr | menu | 単一キーメニュー（リトライ） | 0..9 / timeout |
| ivr | time_condition | 営業時間判定 | match / no_match |
| ivr | voicemail | 応答メッセージ→録音（voicemail_path 変数） | （終端） |
| ai_workflow | ai_conversation | AI 多ターン会話＋変数抽出 | out |
| ai_workflow | intent_detection | 発話を意図に分類 | 各 intent / fallback |
| ai_workflow | collect_info | 質問→聞き取り→確認で情報収集 | out |
| ai_workflow | api_call | 外部 API 呼び出し（SSRF ガード） | success / error |
| ai_workflow | email_notify | メール通知 | success / error |
| ai_workflow | human_escalation | アナウンス→有人転送 | （終端） |
| special | call_workflow | 別ワークフロー呼び出し | out |

## 4. 着信テスト（電話あり・ユーザー協力必須）

事前にユーザーへ確認のうえ実施。テスト携帯 08056187372 から契約番号へ発信。

### 4-1. 最小フロー（start → play_audio → hangup）

1. `number` にワークフローの着信番号を設定して保存（Route 自動生成）。
2. 携帯から発信 → 応答され TTS が再生され、切断されること。
3. `docker compose logs -f freeswitch core` で `INBOUND <番号> -> workflow <id>` ログ、
   `variable_millicall_workflow` が CHANNEL_ANSWER に載っていること（`verbose_events=true` 必須）。

### 4-2. DTMF メニュー（menu / dtmf_input）

1. menu ノードでプロンプト→キー入力で分岐するフローを組む。
2. 発信して音声ガイダンス後にキー入力 → 対応する分岐へ進むこと。

### 4-3. AI 会話（ai_conversation）

1. エージェントを指定した ai_conversation ノードを含むフロー。
2. 発信して複数ターン会話が成立し、`[END_CALL]` で自然に切断されること。

## 5. 要実機確認項目（統合テストで fake だった経路 / Phase 0 知見との相互作用）

Task 9 実装時に洗い出した、実機でのみ検証できる項目。**未確認**。

1. **`variable_millicall_workflow` の到達**: Phase 3 で判明したとおり FS はデフォルトで
   `variable_*` を CHANNEL_ANSWER に含めない。dialplan の workflow 分岐に `verbose_events=true`
   を入れてあるが、実機で当該変数が MediaEventRouter に届くことの最終確認。
2. **DTMF イベントの発火**: Phase 0 知見で HGW 対策として `rfc2833-pt=0` + `dtmf-type=none`
   （DTMF はインバンド検出前提）としている。この設定下で FS が `DTMF` イベントを発火し
   `DtmfCollector.feed` に届くか要確認。届かない場合は menu/dtmf_input が timeout に落ちる。
3. **ring_count（ring_ready + sleep）のタイミング**: `sleep data="ring_count*6000"`（1 コール≒6 秒想定）
   は理論値。実キャリアのリングサイクルとのズレ、`ring_ready`→`sleep`→`answer` が着信側に自然な
   呼び出し音として聞こえるか要確認。
4. **park ライフサイクルと HGW session-timer**: ワークフロー実行中はチャネルが park を維持する前提。
   長時間 park と HGW の `Session-Expires: 300`（Phase 0 で判明）との相互作用は未検証。長時間フローで
   セッションが切れる場合は session-timer リフレッシュの手当てが要る。

## 6. トラブルシュート

- **着信するが無音・即切断**: Route が生成されているか（`target_type=workflow`, `target_value=<id>`）、
  workflow が enabled か、definition が valid か（warnings/errors）を確認。
- **menu/dtmf が必ず timeout**: 上記 §5-2（DTMF イベント発火）。`docker compose logs` で `DTMF`
  イベントが来ているか確認。
- **email_notify が常に error**: `MILLICALL_SMTP_HOST` 未設定、または宛先/件名に不正文字（CR/LF）、
  宛先が非メール形式。
- **api_call が常に error**: SSRF ガードで内部アドレス（private/loopback/link-local/CGNAT 等）が
  拒否されている可能性。外部到達可能なホストか確認。リダイレクトは辿らない（follow_redirects=False）。
- **フロー途中で停止（step limit）**: 実行ステップ上限は 500。goto ループや過大なフローは打ち切られる。

## 7. 後片付け

```bash
# ワークフロー削除（Route も自動削除される）
curl -s -X DELETE http://192.168.1.3:8000/api/workflows/<id> -b cookie.txt   # 204

# 一時的に無効化（Route も disabled になる）
curl -s -X PUT http://192.168.1.3:8000/api/workflows/<id> -b cookie.txt \
  -H 'Content-Type: application/json' -d '{"enabled": false, ...}'
```
