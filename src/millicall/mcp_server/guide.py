"""`guide://outbound-calling` リソース本文（旧実装 verbatim）。

旧 `mcp_server.py:1155-1195` の Markdown を踏襲する。番号形式（内線そのまま /
外線 0 始まり / 184・186 プレフィックス）と dial → say_and_listen → hangup の
3 ステップ、会話例、ツール使い分けを記述。
"""

OUTBOUND_CALLING_GUIDE = """# Millicall PBX 電話会話ガイド

## 基本の会話フロー（3ステップ）
1. `dial` で発信（応答まで自動で待ちます）
2. `say_and_listen` で会話のやりとり（何ターンでも繰り返し可能）
3. `hangup` で通話終了

## 電話番号の形式
- 内線: 番号をそのまま（例: "800", "4001"）
- 外線: 0 + 番号（例: "09012345678"）
- 非通知発信: 184 + 0 + 番号
- 番号通知発信: 186 + 0 + 番号

## 会話例：ラーメンの注文
```
# 1. 発信（応答を待つ）
result = dial("09012345678")
channel_id = result["channel_id"]

# 2. 会話（say_and_listen = こちらが話す→相手の返答を聞く）
r1 = say_and_listen(channel_id, "こんにちは、ラーメンを1杯お願いしたいのですが")
# r1["they_said"] = "はい、味はどうしますか？"

r2 = say_and_listen(channel_id, "味噌ラーメンをお願いします")
# r2["they_said"] = "かしこまりました。20分ほどでお届けします"

# 3. 最後の挨拶（返答不要なのでsayだけ）→ 切る
say(channel_id, "ありがとうございます。お願いします")
hangup(channel_id)
```

## ツール使い分け
- `say_and_listen`: 通常の会話（話す→聞く）
- `say`: 最後の一言（お礼・挨拶など返答不要）
- `listen`: 追加で相手の話を聞きたい時
- `dial`: 発信（応答まで待つ）
- `hangup`: 切電
"""
