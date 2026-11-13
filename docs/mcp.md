# MCP 利用（Model Context Protocol）

millicall は MCP サーバーを内蔵しており、claude.ai のカスタムコネクタや任意の MCP クライアントから PBX を操作できます。Streamable HTTP + OAuth 2.1 で認証します。

詳細な実機確認手順は RUNBOOK-phase4a-mcp.md（リポジトリ runbooks/ 参照） を参照してください。

## 前提条件

- プロバイダ（LLM / TTS / STT）が登録済みで、AI エージェントが 1 件以上 `enabled` であること（`converse` ツールに必要）
- **issuer URL は HTTPS 必須**（`localhost` / `127.0.0.1` のみ `http` 許可）。外部クライアント（claude.ai）から接続するには外部公開 HTTPS URL が必要です（[Cloudflare Tunnel](cloudflare.md) 参照）

## 環境変数の設定

`~/millicall/.env` に以下を追記します。

```bash
# MCP 有効化（デフォルト true なので通常は省略可）
MILLICALL_MCP_ENABLED=true

# OAuth 2.1 issuer URL（HTTPS 必須）
MILLICALL_MCP_ISSUER_URL=https://millicall.example.com

# DNS リバインド攻撃対策の許可ホスト名（カンマ区切り）
# 公開ホスト名を必ず含めること。漏れると /mcp が全拒否される
MILLICALL_MCP_ALLOWED_HOSTS=millicall.example.com,localhost,127.0.0.1

# converse ツールの既定 AI エージェント ID（省略時は enabled な最小 id を使用）
# MILLICALL_MCP_DEFAULT_AGENT_ID=1
```

設定後 `millicallctl update` で再起動します。

## 疎通確認

```bash
BASE=https://millicall.example.com

# OAuth メタデータ確認（200 が返ること）
curl -s $BASE/.well-known/oauth-authorization-server | python3 -m json.tool

# /mcp は Bearer なしで 401 になること
curl -s -o /dev/null -w "%{http_code}" $BASE/mcp
# → 401
```

## claude.ai カスタムコネクタの登録

### 1. コネクタを追加

1. claude.ai → 設定 → インテグレーション（MCP）
2. 「コネクタを追加」
3. MCP サーバー URL に `https://millicall.example.com/mcp` を入力して保存

### 2. OAuth 認可フロー

コネクタ追加後に自動的に OAuth フローが始まります。

| ステップ | 内容 |
|---|---|
| **DCR** | claude.ai が `/register` を呼んでクライアントを自動登録 |
| **認可リダイレクト** | `/authorize` → `/mcp-login` のログイン画面（HTML フォーム）が開く |
| **ログイン** | millicall の `admin` または `user` ロールのアカウントを入力 |
| **PKCE 完了** | 認可コードが `redirect_uri` へ戻り、claude.ai が `/token` でアクセストークンを取得 |

### 3. ツール一覧の確認

認可が完了すると claude.ai でツール一覧が表示されます。

> OAuth のクライアント登録・認可コード・トークンは core の DB（`data/` 内の SQLite）に保存されます。`docker compose restart` や `millicallctl update` で core が再起動しても**再認証は不要**です（この方式を導入した更新を適用した直後の 1 回だけ、以前のインメモリのトークンが無効になるため再認証が必要です）。

### OAuth 状態の保存と上限

| 項目 | 値 |
|---|---|
| 保存先 | `mcp_oauth_clients` / `mcp_oauth_codes` / `mcp_oauth_tokens`（DB） |
| 秘密値の保存形式 | 認可コード・アクセス/リフレッシュトークン・client_secret は **SHA-256 ハッシュのみ**。DB やバックアップから有効な値は復元できない |
| 認可コード | 10 分・一回限り |
| アクセストークン | 24 時間 |
| リフレッシュトークン | 30 日・使用ごとにローテーション（旧トークンは即無効、同時に 2 回使っても成功は 1 回だけ） |
| クライアント登録（DCR）上限 | 全体で 1,000 件。満杯時は一度も認可されていない登録を古い順に追い出し、それも無ければ登録を拒否（400） |
| 未認可の登録の保持 | 24 時間 |
| 認可済み登録の保持 | 最後の認可・トークン発行から 90 日（以降は再登録が必要） |
| 登録メタデータ | 16 KiB まで |
| ユーザーあたりのトークン | リフレッシュ 50 件 / アクセス 200 件。超過時は古い認可から失効 |

期限切れの行は参照時と登録・発行時に自動削除されます。ユーザーの無効化・削除・パスワード変更・ログアウト全デバイス（session_epoch 更新）では、発行済みトークンが即時に失効します。

---

## 利用可能なツール（converse を含む 15 ツール）

### 電話操作

| ツール | 説明 |
|---|---|
| `dial` | 指定番号へ発信（30 秒タイムアウト） |
| `hangup` | 通話を切断 |
| `transfer` | 通話を転送（例: 内線 100 へ） |
| `send_dtmf` | DTMF トーンを送信（IVR 操作等） |
| `get_call_status` | 通話の現在状態を取得 |
| `list_active_calls` | アクティブな通話一覧を取得 |

### 音声会話

| ツール | 説明 |
|---|---|
| `say` | AI 音声でメッセージを再生 |
| `listen` | 相手の発話を録音して文字起こし |
| `say_and_listen` | メッセージ再生 → 相手の返答を録音・文字起こし（1 ターン） |
| `converse` | 発信→AI 自律会話→切電→transcript/summary を全自動で返す |

### 電話帳 CRUD

| ツール | 説明 |
|---|---|
| `list_contacts` | 電話帳を検索・一覧 |
| `add_contact` | 連絡先を追加 |
| `delete_contact` | 連絡先を削除 |

### 設定確認

| ツール | 説明 |
|---|---|
| `list_extensions` | 内線一覧を取得 |
| `list_trunks` | トランク一覧を取得 |

### リソース

| リソース | 説明 |
|---|---|
| `guide://outbound-calling` | 発信ガイドリソース |

---

## converse ツールの使い方

`converse` は最も高度なツールで、AI が自律的に通話を進めます。

**claude.ai での指示例:**

```
converse で 09000000000 に電話をかけてください。
purpose: 「明日の打ち合わせの時間を確認する」
key_points: 「14時か15時で相談したい」
your_name: 「山田」
```

**返り値（例）:**

```json
{
  "status": "completed",
  "phone_number": "09000000000",
  "purpose": "明日の打ち合わせの時間を確認する",
  "turns": 4,
  "summary": "14時に打ち合わせを設定した。",
  "transcript": [
    {"turn": 0, "speaker": "ai", "text": "もしもし、山田と申します..."},
    {"turn": 1, "speaker": "human", "text": "はい、明日でしたら..."}
  ]
}
```

内部動作:

1. 既定 AI エージェント（`MILLICALL_MCP_DEFAULT_AGENT_ID` または enabled な最小 id）の LLM / TTS / STT プロバイダを使用
2. FreeSWITCH で発信 → `CHANNEL_ANSWER` → 音声フォーク開始
3. AI が挨拶 → ターン制会話 → `[END_CALL]` 検知 → 自動切断
4. transcript をロール変換し LLM で 1〜2 文の要約を生成して返す

---

## 番号通知

この HGW 回線はデフォルト非通知です。相手に発信者番号を表示したい場合は番号先頭に `186` を付けます。

```
# 非通知
dial で 09000000000 に電話をかけてください

# 番号通知
dial で 18609000000000 に電話をかけてください
```

---

## トラブルシュート

| 症状 | 確認・対処 |
|---|---|
| `/mcp` が 401（トークン失効） | ユーザーの無効化・パスワード変更・ログアウト全デバイス、または 30 日以上リフレッシュされていない。claude.ai コネクタから「再認証」を実行（core の再起動だけでは失効しない） |
| issuer HTTPS エラー | `MILLICALL_MCP_ISSUER_URL` に `https://` が付いているか確認。`localhost` 以外は HTTPS 必須 |
| `/mcp` が常に接続エラー | `MILLICALL_MCP_ALLOWED_HOSTS` に公開ホスト名が含まれているか確認 |
| OAuth 開始直後に issuer 不一致で失敗 | `/.well-known/oauth-authorization-server` の `issuer` と `/.well-known/oauth-protected-resource` の `authorization_servers` が完全一致しているか確認（mcp SDK 2.x 準拠クライアントは厳密照合する）。プロキシでのホスト/パス書き換えを避ける |
| `/mcp` が 413 | リクエストボディが 4 MiB 上限（mcp SDK 2.x 既定）を超えている |
| `converse` で「既定エージェントが見つかりません」 | `GET /api/ai-agents` で `enabled=true` のエージェントが存在するか確認 |
| `dial` で「利用可能なトランクがありません」 | `GET /api/trunks` で `enabled=true` のトランクが存在するか確認 |
| `dial` が 30 秒でタイムアウト | 正常動作（応答なし）。FreeSWITCH と ESL の接続（`127.0.0.1:8021`）を確認 |

詳細: RUNBOOK-phase4a-mcp.md（リポジトリ runbooks/ 参照）
