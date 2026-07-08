# プロバイダ追加（LLM / TTS / STT）

millicall の音声 AI 機能（着信 AI 応対・ワークフロー・MCP converse）は、外部プロバイダを通じて LLM・TTS・STT を利用します。プロバイダは管理 GUI `/providers` から登録します。

## プロバイダの種類

| タイプ | 用途 |
|---|---|
| **LLM** | AI エージェントの会話生成 |
| **TTS** | テキスト読み上げ（AI 音声出力） |
| **STT** | 音声認識（通話音声のテキスト化） |

## /providers ページでの登録手順

1. 管理 GUI `/providers` を開く
2. 「プロバイダを追加」ボタンをクリック
3. タイプ（LLM / TTS / STT）と種別（kind）を選択
4. 名前、設定項目、API キー（必要な場合）を入力
5. 「保存」→「接続テスト」で `ok: true` を確認

> **API キーは暗号化保存**されます。登録後のレスポンスでは末尾 4 文字のみが表示されます（平文は返されません）。

## 対応プロバイダ一覧

### LLM（大規模言語モデル）

#### OpenAI 互換（`openai_compatible`）
OpenAI の Chat Completion API またはその互換エンドポイント（Azure OpenAI、ローカル vLLM 等）を使用します。

| 設定項目 | 説明 | 例 |
|---|---|---|
| ベース URL | API エンドポイント | `https://api.openai.com/v1` |
| モデル | 使用モデル名 | `gpt-4o-mini` |
| 温度 | 生成の多様性（省略時 0.7） | `0.7` |
| 最大トークン | 応答の最大トークン数（省略時 500） | `500` |
| API キー | OpenAI API キー | `sk-...` |

#### Anthropic（`anthropic`）
Claude API（Anthropic）を使用します。

| 設定項目 | 説明 | 例 |
|---|---|---|
| モデル | Claude モデル名 | `claude-sonnet-4-20250514` |
| 最大トークン | 省略時 500 | `500` |
| API キー | Anthropic API キー | `sk-ant-...` |

#### Gemini（`gemini`）
Google Gemini 生成 API を使用します。

| 設定項目 | 説明 | 例 |
|---|---|---|
| モデル | Gemini モデル名 | `gemini-2.5-flash` |
| 温度 | 省略時 0.7 | `0.7` |
| API キー | Google AI Studio API キー | `AIza...` |

#### Vertex AI（`vertex_ai`）
Google Cloud の Vertex AI 経由で Gemini を使用します。API キーの代わりに**サービスアカウント JSON** を使用します。

| 設定項目 | 説明 | 例 |
|---|---|---|
| プロジェクト | GCP プロジェクト ID | `my-gcp-project` |
| ロケーション | リージョン | `us-central1` |
| モデル | モデル名 | `gemini-2.0-flash` |
| 温度 | 省略時 0.7 | `0.7` |
| サービスアカウント JSON | SA キー JSON ファイル（アップロード） | — |

---

### TTS（テキスト読み上げ）

#### VOICEVOX（`voicevox`）
ローカルで動作する VOICEVOX エンジンを使用します。**API キー不要**。低遅延（目標 1 秒以下）を実現できます。

VOICEVOX を有効化する場合は `docker compose --profile voicevox up -d` で起動します（開発環境）。本番の `docker-compose.prod.yml` には別途設定が必要です（[RUNBOOK-phase3-ai.md § TODO](RUNBOOK-phase3-ai.md) 参照）。

| 設定項目 | 説明 | 例 |
|---|---|---|
| エンジン URL | VOICEVOX エンジンの URL | `http://127.0.0.1:50021` |
| 話者 ID | キャラクター番号 | `1`（ずんだもん等） |

#### Open JTalk（`openjtalk`）
コンテナ内蔵の Open JTalk を使用します。**API キー不要**。追加サービスなしで動作します。

| 設定項目 | 説明 | デフォルト |
|---|---|---|
| 辞書ディレクトリ | MeCab 辞書のパス | `/var/lib/mecab/dic/open-jtalk/naist-jdic` |
| 音声モデルパス | HTS voice ファイルのパス | `/usr/share/hts-voice/.../nitech_jp_atr503_m001.htsvoice` |

> 辞書・音声モデルのパスはコンテナ内のパスです。省略するとサーバ側のデフォルトが使われます。

---

### STT（音声認識）

#### Whisper（`whisper`）
OpenAI Whisper API による音声認識です。

| 設定項目 | 説明 | 例 |
|---|---|---|
| モデル | Whisper モデル | `whisper-1` |
| 言語 | 認識言語 | `ja` |
| API キー | OpenAI API キー | `sk-...` |

#### Google STT（`google_stt`）
Google Cloud Speech-to-Text（V2）を使用します。**サービスアカウント JSON** 認証です。

| 設定項目 | 説明 | 例 |
|---|---|---|
| プロジェクト | GCP プロジェクト ID | `my-gcp-project` |
| ロケーション | リージョン | `global` |
| 言語 | 認識言語 | `ja-JP` |
| モデル | 認識モデル | `chirp_2` |
| サービスアカウント JSON | SA キー JSON ファイル（アップロード） | — |

---

## 接続テスト

プロバイダ登録後、一覧の「接続テスト」ボタンを押します。`ok: true` と `latency_ms` が返れば正常です。

API で実行する場合:

```bash
# プロバイダ ID を確認
curl -b cookie.txt http://192.168.1.10:8000/api/providers

# 接続テスト（id=1 の場合）
curl -b cookie.txt -X POST http://192.168.1.10:8000/api/providers/1/test
# 期待: {"ok": true, "detail": "...", "latency_ms": 123}
```

## AI エージェントへの割り当て

登録したプロバイダは `/ai-agents` ページで AI エージェントに割り当てます。各エージェントに LLM・TTS・STT を 1 つずつ指定します。詳細は [ワークフロー作成](workflows.md) を参照してください。

## 遅延に関する注意

- **TTS の遅延**が AI 応答の遅延の主因になることがあります
- ローカル TTS（VOICEVOX / Open JTalk）使用時の目標遅延は **1000ms 以下**（発話終了 → 最初の AI 音声再生）
- 外部 LLM との往復が律速になる場合はシステムプロンプトで最初の応答文を短くするよう指示してください

詳細: [RUNBOOK-phase3-ai.md](RUNBOOK-phase3-ai.md)
