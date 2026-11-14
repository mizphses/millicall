# Webhook

着信・通話終了・FAX 受信を外部システム（CRM・チャット通知・業務システムなど）へ HTTP POST で通知します。
管理画面の **Webhook**（`/webhooks`）で登録・テスト送信・配信ログの確認ができます（管理者のみ）。

## イベント

| イベント | 発火タイミング |
|---|---|
| `call.inbound` | 外線トランクからの着信チャネル作成時（`CHANNEL_CREATE`、トランクの `external_<name>` プロファイルの inbound レグ） |
| `call.ended` | 通話終了時（`CHANNEL_HANGUP_COMPLETE`、通話履歴と同じ値）。**1 通話につき 1 回**、呼の代表レグ（A レグ）だけを通知します（下記「call.ended のレグ」）。内線同士の通話も含みます |
| `fax.received` | FAX 受信処理の完了時（成功・失敗とも。`success` で判別） |
| `fax.sent` | FAX 送信の成功時（[FAX 送信](fax.md#fax-送信)） |
| `fax.failed` | FAX 送信の最終失敗時（自動再試行を使い切った・再試行しない理由で失敗した・core 再起動で結果不明になった）。再試行待ちの途中経過では送りません |
| `webhook.test` | 管理画面の「テスト」ボタン（購読設定に関係なく 1 回だけ送信・再試行なし） |

## リクエスト

- メソッド: `POST`、`Content-Type: application/json`
- ヘッダ
  - `X-Millicall-Event`: イベント名
  - `X-Millicall-Delivery`: 配信 ID（uuid4）。**再試行でも同じ値**なので受信側の重複排除に使えます
  - `X-Millicall-Signature`: `sha256=<HMAC-SHA256(シークレット, 生のリクエストボディ) の hex>`（シークレット設定時のみ）
- タイムアウト 10 秒。5xx・408・429・接続失敗は **最大 3 回**（1 秒後、5 秒後に再試行）。
  それ以外の 4xx は受信側の拒否とみなして再試行しません。
- リダイレクト（3xx）は追いません（失敗扱い）。レスポンス本文は読み捨てます。
- 時刻はすべて UTC（ISO 8601、`Z` 付き）。
- **リプレイ対策**: 署名は本文全体（`occurred_at` を含む）にかかります。受信側は署名検証に加えて
  `occurred_at` が古すぎる（例: 5 分以上前）リクエストを拒否し、`X-Millicall-Delivery` で重複を排除してください。

### payload 例

```json
{"event":"call.inbound","id":"6f1c...","occurred_at":"2026-11-14T01:02:03.456789Z",
 "call":{"uuid":"a1b2...","trunk":"Flets01","from":"07012345678","caller_name":"","to":"0445895782"}}
```

`to` は HGW から届く着信先（ひかり電話 HGW では内線番号＝トランクのユーザー名のことがあります）。

```json
{"event":"call.ended","id":"...","occurred_at":"...",
 "call":{"uuid":"a1b2...","direction":"inbound","trunk":"Flets01","from":"07012345678","caller_name":"",
         "to":"0445895782","started_at":"2026-11-14T01:02:03Z","answered_at":"2026-11-14T01:02:08Z",
         "ended_at":"2026-11-14T01:03:08Z","duration_seconds":65,"billsec_seconds":60,
         "hangup_cause":"NORMAL_CLEARING"}}
```

### call.ended のレグ

FreeSWITCH では 1 通話が複数のチャネル（レグ）からなり、それぞれ `CHANNEL_HANGUP_COMPLETE` が出ます。
millicall は次の規則で **A レグ（呼の代表）だけ** を通知し、通話履歴（CDR）にも同じ規則で A レグだけを残します。

| 通話の作られ方 | A レグ（通知する） | B レグ（通知しない） |
|---|---|---|
| ダイヤルプランの bridge（外線着信 → 内線、内線 → 外線発信、内線同士） | 最初のチャネル（外線着信ならトランク側、内線発信なら発信した端末側） | bridge で作られた `outbound` のチャネル（`variable_originating_leg_uuid` が A レグを指す） |
| ワークフローの転送（transfer） | 同じチャネルがダイヤルプランを移るだけなので 1 チャネル | — |
| API / ワークフローからの発信（originate） | 最初に作られるチャネル（`originating_leg_uuid` を持たない `outbound`） | その先を bridge したチャネル |

念のため、同じ `call.uuid` の `call.ended` が重ねて届いた場合も、直近 256 件の `call.uuid` を覚えておき 1 回だけ送ります（再起動でリセット）。

`direction` は FreeSWITCH から見た A レグの向き（`inbound` / `outbound`）をそのまま入れています。
チャット形式（Discord / Slack / Teams）の「種別」は、`trunk` の有無と組み合わせて次のように表示します。

| `trunk` | `direction` | 種別 |
|---|---|---|
| あり | `inbound` | 外線着信 |
| あり | `outbound` | 外線発信（API・ワークフローからトランクへ直接発信した場合など） |
| なし | `inbound` | 内線発信（内線端末が発信。外線宛ても含む） |
| なし | `outbound` | 内線着信（API・ワークフローから内線端末を呼び出した場合など） |

```json
{"event":"fax.received","id":"...","occurred_at":"...",
 "fax":{"id":12,"call_uuid":"...","from":"0312345678","to":"0445895784","remote_station_id":"+81 3 1234 5678",
        "pages":3,"success":true,"result_text":"OK","received_at":"2026-11-14T01:05:00Z",
        "pdf_url":"https://pbx.example.com/api/faxes/12/pdf"}}
```

```json
{"event":"fax.sent","id":"...","occurred_at":"...",
 "fax":{"id":7,"direction":"outbound","to":"0312345678","trunk":"Flets02","pages":2,"success":true,
        "result_text":"OK","error":null,"remote_station_id":"+81 3 1234 5678","attempts":1,
        "finished_at":"2026-11-14T01:08:00Z",
        "pdf_url":"https://pbx.example.com/api/faxes/outbox/7/pdf?token=..."}}
```

`fax.failed` も同じ形で、`success` が `false`、`error` に失敗理由（例: `応答なし（NO_ANSWER）`）が入ります。
`pdf_url` は送信した元 PDF です。

`pdf_url` は `MILLICALL_MCP_ISSUER_URL`（既定 `http://localhost` 以外を設定している場合）、なければ
`http://<MILLICALL_SIP_DOMAIN>` を基準に組み立てます。URL には **24 時間有効・その FAX にだけ使える署名付き
トークン**（`?token=...`）が付いており、受信側はログインなしで PDF を取得できます。トークンは他の API には使えません。
URL は秘密情報として扱ってください（ログ等に残さない）。管理画面からのダウンロードは従来どおり管理者ログインで行えます。

## 署名の検証

必ず **受け取った生のボディ**（JSON を再シリアライズしない）で HMAC を計算し、定数時間比較してください。

### Python

```python
import hashlib
import hmac


def verify(secret: str, body: bytes, header: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


# FastAPI の例
from fastapi import FastAPI, HTTPException, Request

app = FastAPI()


@app.post("/millicall/webhook")
async def receive(request: Request):
    body = await request.body()
    if not verify("共有シークレット", body, request.headers.get("X-Millicall-Signature", "")):
        raise HTTPException(status_code=401)
    event = await request.json()
    return {"ok": True}
```

### Node.js

```js
import crypto from "node:crypto";
import express from "express";

function verify(secret, rawBody, header) {
  const expected = "sha256=" + crypto.createHmac("sha256", secret).update(rawBody).digest("hex");
  const a = Buffer.from(expected);
  const b = Buffer.from(header ?? "");
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

const app = express();
app.post("/millicall/webhook", express.raw({ type: "application/json" }), (req, res) => {
  if (!verify("共有シークレット", req.body, req.get("X-Millicall-Signature"))) {
    return res.sendStatus(401);
  }
  const event = JSON.parse(req.body.toString("utf8"));
  res.json({ ok: true });
});
```

## チャットサービスへ通知する

Webhook の **形式** を `Discord` / `Slack` / `Microsoft Teams` にすると、millicall JSON の代わりに各サービスの
Webhook がそのまま受け付ける本文を送ります（受信サーバーを用意せずにチャンネルへ通知できます）。
既定の形式は `millicall JSON`（API の `format: "generic"`）で、既存の Webhook はこの形式のままです。

| 形式 | API の `format` | Webhook URL の取得 | 成功とみなす応答 |
|---|---|---|---|
| Discord | `discord` | チャンネルの **設定 → 連携サービス → ウェブフック → 新しいウェブフック** で作成し、「ウェブフック URL をコピー」（`https://discord.com/api/webhooks/...`） | `204 No Content` |
| Slack | `slack` | Slack アプリを作成して **Incoming Webhooks** を有効にし、投稿先チャンネルを選んで URL を発行（`https://hooks.slack.com/services/...`） | `200` |
| Microsoft Teams | `teams` | Teams の **ワークフロー**（Power Automate）で「Webhook 要求を受信したらチャネルに投稿する」テンプレートからフローを作り、表示される URL をコピー | `200` / `202` |

- 本文はイベントごとに日本語のタイトルと項目（発信元・着信先・トランク、通話時間・切断理由、ページ数・成否など）で
  構成します。値が空の項目は省きます。各サービスの長さ制限に収まるよう長い値は切り詰めます。
- 発信者名・番号・結果メッセージなど外部から届く値は、Discord / Slack ではインラインコード、Teams では
  Markdown 記号のエスケープで表示し、リンクや装飾・メンションとして解釈させません。リンクになるのは
  millicall が生成した FAX の PDF URL だけです。
- **チャット形式は署名しません**（`X-Millicall-Signature` を付けません。`X-Millicall-Event` / `X-Millicall-Delivery` は付きます）。
  Webhook URL を知っていれば誰でもそのチャンネルに投稿できるため、URL は秘密情報として扱ってください。
  シークレットは画面に表示されず、API で指定しても使われません（`generic` に戻したときに使われます）。
- Teams は **ワークフロー（Power Automate）を推奨** します。廃止予定の旧 Incoming Webhook コネクタ（Office 365 コネクタ）の
  URL にも同じ本文（Adaptive Card 添付の `message`）がそのまま通ります。
- 再試行・配信ログ・送信先の制限（下記「セキュリティ」）は millicall JSON 形式と共通です。「テスト」ボタンも選んだ形式で送ります。
- **FAX の PDF リンクについて**: チャット通知の PDF リンクは `pdf_url` と同じ **署名付きトークン付き URL** です。
  チャンネルを閲覧できる人は誰でも、ログインなしで **24 時間** その PDF をダウンロードできます。
  閲覧できる人を限定したい場合は、チャンネルの閲覧権限で制御するか、リンクをチャットに出さない
  `millicall JSON` 形式で自前の受信サーバーに送ってください。Slack ではリンクのプレビュー展開
  （`unfurl_links` / `unfurl_media`）を無効にして送ります。

### Discord の本文例（`fax.received`）

```json
{"content":"📠 FAX 受信: `0312345678`（3 ページ・成功）",
 "embeds":[{"title":"📠 FAX 受信","color":2278750,"timestamp":"2026-11-14T01:05:00Z",
            "footer":{"text":"millicall · fax.received"},
            "url":"https://pbx.example.com/api/faxes/12/pdf?token=...",
            "fields":[{"name":"発信元","value":"`0312345678`","inline":true},
                      {"name":"着信先","value":"`0445895784`","inline":true},
                      {"name":"ページ数","value":"3 ページ","inline":true},
                      {"name":"結果","value":"成功","inline":true},
                      {"name":"PDF","value":"[PDF を開く](https://pbx.example.com/api/faxes/12/pdf?token=...)","inline":false}]}],
 "allowed_mentions":{"parse":[]}}
```

### Slack の本文例（`call.ended`）

```json
{"text":"☎️ 通話終了: `07012345678`（1分0秒）",
 "blocks":[{"type":"header","text":{"type":"plain_text","text":"☎️ 通話終了","emoji":true}},
           {"type":"section","fields":[{"type":"mrkdwn","text":"*種別*\n外線着信"},
                                       {"type":"mrkdwn","text":"*発信元*\n`07012345678`"},
                                       {"type":"mrkdwn","text":"*着信先*\n`0445895782`"},
                                       {"type":"mrkdwn","text":"*トランク*\n`Flets01`"},
                                       {"type":"mrkdwn","text":"*通話時間*\n1分0秒"},
                                       {"type":"mrkdwn","text":"*切断理由*\n`NORMAL_CLEARING`"}]},
           {"type":"context","elements":[{"type":"mrkdwn","text":"millicall · call.ended · <!date^1794618188^{date_num} {time_secs}|2026-11-14T01:03:08Z>"}]}],
 "unfurl_links":false,"unfurl_media":false}
```

### Microsoft Teams の本文例（`call.inbound`）

```json
{"type":"message",
 "attachments":[{"contentType":"application/vnd.microsoft.card.adaptive","contentUrl":null,
   "content":{"$schema":"http://adaptivecards.io/schemas/adaptive-card.json","type":"AdaptiveCard","version":"1.4",
     "body":[{"type":"TextBlock","text":"📞 着信","weight":"Bolder","size":"Medium","wrap":true},
             {"type":"FactSet","facts":[{"title":"発信元","value":"07012345678"},
                                        {"title":"着信先","value":"0445895782"},
                                        {"title":"トランク","value":"Flets01"}]},
             {"type":"TextBlock","text":"millicall · call.inbound · 2026-11-14T01:02:03Z","size":"Small","isSubtle":true,"wrap":true}]}}]}
```

`fax.received` で PDF がある場合は `content.actions` に `Action.OpenUrl`（「PDF を開く」）が付きます。

## セキュリティ

- URL は `http://` / `https://` のみ。`user:pass@` を含む URL は拒否します。
- 送信先は **送信のたびに** DNS 解決結果をすべて検証し、接続先 IP に固定します（DNS リバインディング対策）。
  core は `network_mode: host` で動くため、ローカル専用サービス（docker socket-proxy・ESL・core 自身の API）への
  踏み台にならないよう次の規則を適用します。

  | 規則 | 対象 | 許可 CIDR で解除 |
  |---|---|---|
  | 常に拒否 | loopback（`127.0.0.0/8`・`::1`）・`0.0.0.0/8`・リンクローカル（`169.254.169.254` 等のメタデータ含む）・マルチキャスト・予約済み | 不可 |
  | 常に拒否 | **millicall ホスト自身の全インターフェースのアドレス**（全ポート。LAN・docker ブリッジ `172.17.0.1` 等・`tailscale0`）。core の 80/443/HTTP ポートもこれで塞がります。列挙は 30 秒キャッシュ、列挙に失敗した場合は送信を拒否（fail-closed） | 不可 |
  | 常に拒否 | 内部サービスの接続先そのもの（`MILLICALL_ESL_HOST:PORT`・`MILLICALL_DOCKER_PROXY_URL` の host:port）と、非グローバル宛ての `2375`・`2376`・`8021`（+ 設定の ESL / docker proxy ポート） | 不可 |
  | 既定で拒否 | docker 既定レンジ `172.16.0.0/12`（子 LAN 既定の `172.20.0.0/16` を含む）・CGNAT `100.64.0.0/10`（tailnet） | **可**（解除できるのはこの規則だけ） |
  | 許可 | 上記以外の RFC1918（`192.168.0.0/16`・`10.0.0.0/8`）の LAN 受信サーバー（80/443 を含む）、グローバルアドレス | — |

  許可 CIDR は **設定 → Webhook → Webhook 許可 CIDR**（`webhook_allowed_cidrs`、改行/カンマ区切り）で指定します。
  例: 子 LAN（`172.20.0.0/16`）や tailnet（`100.64.0.0/10`）上の受信サーバーに送るときに追加します。
  LAN の `http://192.168.1.50/` は設定なしで送れます。同一ホスト上の受信サーバーには送れません。
- 検証したポートと実際の接続先ポートが異なるリクエストはトランスポートで拒否します。
- シークレットは master_key 由来の鍵で暗号化して保存し、API・画面・監査ログには出しません（`has_secret` のみ）。
- 作成・更新・削除・テスト送信は監査ログに残ります。
- 配信ログは Webhook ごとに直近 200 件を保持します。

## 実装メモ

- 送信は asyncio タスクで行い、ESL イベント処理をブロックしません。
- core 停止時に送信中のタスクは打ち切られます（永続キューは持ちません）。
