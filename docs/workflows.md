# ワークフロー作成

ワークフロー機能を使うと、着信をノードベースのフローで処理できます。IVR メニュー、AI 会話、時間帯分岐、メール通知など 19 種のノードを組み合わせます。エディタは `/workflows` の xyflow ベースの GUI で操作します。

詳細な実機確認手順は RUNBOOK-phase4b-workflow.md（リポジトリ runbooks/ 参照） を参照してください。

## 前提

- プロバイダ（LLM / TTS / STT）が `/providers` に登録済みであること（AI 系ノードに必要）
- AI エージェントが `/ai-agents` に作成済みであること（`ai_conversation` 等のノードに必要）

## ワークフローの作成手順

### 1. 新規作成

1. 管理 GUI `/workflows` を開く
2. 「新規作成」ボタンをクリック
3. **名前**と**着信番号**（`number`）を入力して保存
   - 着信番号は一意です。保存時に同番号の Route（`target_type=workflow`）が自動生成されます

### 2. エディタを開く

ワークフロー一覧で「編集」をクリックすると xyflow エディタが開きます。

### 3. ノードの配置

- 左のパレットからノードをドラッグ＆ドロップして配置
- ノードを選択し右パネルでパラメータを設定（フォームは各ノードの schema から自動生成）
- ノードの**下部（出力ハンドル）**から次ノードの**上部（入力ハンドル）**へドラッグしてエッジを繋ぐ

### 4. 保存

「保存」ボタンで保存します。定義が不正な場合は 422 でエラーが表示され保存されません。到達不能ノード等は警告として表示されます（保存は可能）。

### 5. AI 自動生成（オプション）

「AI 生成」に自然言語でフローを記述すると、`/api/workflows/generate` が叩き台の定義を生成します。生成後にエディタで調整してください。

---

## ノード種別リファレンス（19 種）

### common カテゴリ（汎用）

| ノード | 用途 | 出力ハンドル |
|---|---|---|
| `start` | 開始ノード。`ring_count` で応答前の呼出回数を設定 | `out` |
| `end` | 正常終了 | （終端） |
| `hangup` | 通話切断 | （終端） |
| `play_audio` | 音声/TTS テキストを再生 | `out` |
| `transfer` | ブラインド転送（内線番号や外線番号へ） | （終端） |
| `condition` | 変数の条件分岐 | `true` / `false` |
| `set_variable` | ワークフロー変数を設定 | `out` |
| `goto` | 別ノードへジャンプ（循環検出あり、最大 500 ステップ） | （終端） |

### ivr カテゴリ（IVR）

| ノード | 用途 | 出力ハンドル |
|---|---|---|
| `dtmf_input` | DTMF を収集（桁数・終端キー・タイムアウト設定可） | `0`〜`9` / `done` / `timeout` |
| `menu` | 単一キーメニュー（リトライ回数設定可） | `0`〜`9` / `timeout` |
| `time_condition` | 営業時間・曜日判定 | `match` / `no_match` |
| `voicemail` | 応答メッセージ再生 → 録音（録音パスが `voicemail_path` 変数に入る） | （終端） |

### ai_workflow カテゴリ（AI）

| ノード | 用途 | 出力ハンドル |
|---|---|---|
| `ai_conversation` | AI エージェントによる多ターン会話。変数抽出も可 | `out` |
| `intent_detection` | 発話を指定した意図（intent）に分類 | 各 intent / `fallback` |
| `collect_info` | 「質問→聞き取り→確認」の繰り返しで情報収集 | `out` |
| `api_call` | 外部 API を呼び出し（SSRF ガード付き。private/loopback アドレスは拒否） | `success` / `error` |
| `email_notify` | メール通知送信（SMTP 設定必要） | `success` / `error` |
| `human_escalation` | アナウンス再生後に有人オペレーターへ転送 | （終端） |

### special カテゴリ

| ノード | 用途 | 出力ハンドル |
|---|---|---|
| `call_workflow` | 別のワークフローを呼び出す（サブフロー） | `out` |

---

## フロー設計例

### 最小フロー（録音メッセージを再生して切断）

```
[start] → [play_audio: "ただいま電話に出られません"] → [hangup]
```

### IVR メニュー

```
[start] → [play_audio: "営業は1、サポートは2を押してください"]
    → [menu]
        ├─ 1 → [transfer: 内線 100（営業）]
        ├─ 2 → [transfer: 内線 200（サポート）]
        └─ timeout → [play_audio: "お時間をいただき..."] → [hangup]
```

### 時間帯分岐 + AI 応対

```
[start] → [time_condition: 平日 9:00-18:00]
    ├─ match → [ai_conversation: エージェント「受付」]
    └─ no_match → [voicemail: "営業時間外です..."]
```

---

## メール通知ノードの SMTP 設定

`email_notify` ノードを使う場合は `.env` に SMTP 設定が必要です。

```bash
MILLICALL_SMTP_HOST=smtp.example.com
MILLICALL_SMTP_PORT=587
MILLICALL_SMTP_USERNAME=notify@example.com
MILLICALL_SMTP_PASSWORD=xxxxxxxx
MILLICALL_SMTP_FROM=notify@example.com
MILLICALL_SMTP_STARTTLS=true
MILLICALL_SMTP_TIMEOUT=15
```

`MILLICALL_SMTP_HOST` が空の場合、`email_notify` ノードは常に `error` 分岐に落ちます。

---

## API 操作（参考）

```bash
# ノード種別カタログを取得
curl -b cookie.txt http://192.168.1.10/api/workflows/node-types | jq '.[].type'

# ワークフロー一覧
curl -b cookie.txt http://192.168.1.10/api/workflows

# ワークフロー作成
curl -X POST -b cookie.txt http://192.168.1.10/api/workflows \
  -H 'Content-Type: application/json' \
  -d '{"name":"受付IVR","number":"30","definition":{"nodes":[...],"edges":[...]}}'

# ワークフロー削除（Route も自動削除）
curl -X DELETE -b cookie.txt http://192.168.1.10/api/workflows/<id>
```

---

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| 着信するが無音・即切断 | Route が `target_type=workflow` で正しく生成されているか確認。ワークフローが `enabled` か確認 |
| menu/dtmf ノードが必ず timeout | HGW の DTMF はインバンドのみ。`docker compose logs freeswitch` で DTMF イベントを確認（[HGW/フレッツ設定](hgw-flets.md) 参照） |
| email_notify が常に error | SMTP 設定を確認。宛先・件名に改行文字が含まれていないか確認 |
| api_call が常に error | SSRF ガードで内部アドレスが拒否されている可能性。外部到達可能な URL か確認 |
| フロー途中で停止 | 実行ステップ上限は 500。`goto` ループや過大なフローを見直す |

詳細: RUNBOOK-phase4b-workflow.md（リポジトリ runbooks/ 参照）
