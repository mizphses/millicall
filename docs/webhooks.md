# Webhook

着信・通話終了・FAX 受信を外部システム（CRM・チャット通知・業務システムなど）へ HTTP POST で通知します。
管理画面の **Webhook**（`/webhooks`）で登録・テスト送信・配信ログの確認ができます（管理者のみ）。

## イベント

| イベント | 発火タイミング |
|---|---|
| `call.inbound` | 外線トランクからの着信チャネル作成時（`CHANNEL_CREATE`、トランクの `external_<name>` プロファイルの inbound レグ） |
| `call.ended` | 通話終了時（`CHANNEL_HANGUP_COMPLETE`、通話履歴と同じ値）。bridge の B レグは通知しません。内線同士の通話も含みます |
| `fax.received` | FAX 受信処理の完了時（成功・失敗とも。`success` で判別） |
| `webhook.test` | 管理画面の「テスト」ボタン（購読設定に関係なく 1 回だけ送信・再試行なし） |

## リクエスト

- メソッド: `POST`、`Content-Type: application/json`
- ヘッダ
  - `X-Millicall-Event`: イベント名
  - `X-Millicall-Delivery`: 配信 ID（uuid4）。**再試行でも同じ値**なので受信側の重複排除に使えます
  - `X-Millicall-Signature`: `sha256=<HMAC-SHA256(シークレット, 生のリクエストボディ) の hex>`（シークレット設定時のみ）
- タイムアウト 10 秒。2xx 以外・接続失敗は **最大 3 回**（1 秒後、5 秒後に再試行）。
- リダイレクト（3xx）は追いません（失敗扱い）。レスポンス本文は読み捨てます。
- 時刻はすべて UTC（ISO 8601、`Z` 付き）。

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

```json
{"event":"fax.received","id":"...","occurred_at":"...",
 "fax":{"id":12,"call_uuid":"...","from":"0312345678","to":"0445895784","remote_station_id":"+81 3 1234 5678",
        "pages":3,"success":true,"result_text":"OK","received_at":"2026-11-14T01:05:00Z",
        "pdf_url":"https://pbx.example.com/api/faxes/12/pdf"}}
```

`pdf_url` は `MILLICALL_MCP_ISSUER_URL`（既定 `http://localhost` 以外を設定している場合）、なければ
`http://<MILLICALL_SIP_DOMAIN>` を基準に組み立てます。ダウンロードには管理者ログイン（Cookie）が必要です。

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

## セキュリティ

- URL は `http://` / `https://` のみ。`user:pass@` を含む URL は拒否します。
- 送信先は DNS 解決結果を検証して接続先 IP に固定します（DNS リバインディング対策）。LAN（RFC1918）の
  受信サーバーは許可しますが、次は遮断します:
  - loopback（`127.0.0.0/8`・`::1`）・`0.0.0.0/8`・リンクローカル（クラウドメタデータ `169.254.169.254` 等）・マルチキャスト・予約済み
  - **millicall を動かしているホスト自身のアドレス**（ホスト名の解決結果・`MILLICALL_SIP_BIND_IP`・IP 指定の `MILLICALL_SIP_DOMAIN`）。
    core API・docker socket-proxy・ESL などローカル専用サービスへの踏み台を防ぐためです。同一ホスト上の受信サーバーには送れません
  - docker socket-proxy（`MILLICALL_DOCKER_PROXY_URL`）と ESL（`MILLICALL_ESL_HOST:PORT`）の host:port
- シークレットは master_key 由来の鍵で暗号化して保存し、API・画面・監査ログには出しません（`has_secret` のみ）。
- 作成・更新・削除・テスト送信は監査ログに残ります。
- 配信ログは Webhook ごとに直近 200 件を保持します。

## 実装メモ

- 送信は asyncio タスクで行い、ESL イベント処理をブロックしません。
- core 停止時に送信中のタスクは打ち切られます（永続キューは持ちません）。
