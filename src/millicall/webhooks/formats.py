"""チャットサービス向け Webhook 本文の組み立て（issue #130）。

Webhook の ``format`` が ``discord`` / ``slack`` / ``teams`` のとき、millicall JSON の代わりに
各サービスの Incoming Webhook がそのまま受け付ける本文を作る。いずれも純関数で、送信や
署名は dispatcher 側が行う（チャット形式は HMAC 署名ヘッダを付けない）。

1. :func:`describe_event` でイベントを形式に依存しない :class:`ChatMessage`（タイトル・
   1 行要約・項目・リンク）にする。値が None / 空の項目は落とす。
2. :func:`build_chat_message` が形式ごとの JSON（dict）へ変換する。文字列は各サービスの
   長さ制限に収まるよう切り詰める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

FORMAT_GENERIC = "generic"
FORMAT_DISCORD = "discord"
FORMAT_SLACK = "slack"
FORMAT_TEAMS = "teams"
WEBHOOK_FORMATS: tuple[str, ...] = (FORMAT_GENERIC, FORMAT_DISCORD, FORMAT_SLACK, FORMAT_TEAMS)
# 署名なしでチャット向け本文を送る形式。
CHAT_FORMATS: tuple[str, ...] = (FORMAT_DISCORD, FORMAT_SLACK, FORMAT_TEAMS)

# Discord の制限（https://discord.com/developers/docs/resources/message#embed-object-embed-limits）
DISCORD_CONTENT_MAX = 2000
DISCORD_TITLE_MAX = 256
DISCORD_DESCRIPTION_MAX = 4096
DISCORD_FIELD_NAME_MAX = 256
DISCORD_FIELD_VALUE_MAX = 1024
DISCORD_FIELDS_MAX = 25
# Slack の制限（Block Kit）
SLACK_TEXT_MAX = 3000
SLACK_HEADER_MAX = 150
SLACK_FIELD_MAX = 2000
SLACK_FIELDS_PER_SECTION = 10
# Teams（Adaptive Card）には明確な文字数制限がないが、通知として常識的な長さに抑える。
TEAMS_TEXT_MAX = 3000
TEAMS_FACT_VALUE_MAX = 1024

# 埋め込みの色（Discord の embed color）。
COLOR_INBOUND = 0x3B82F6
COLOR_ENDED = 0x6B7280
COLOR_FAX_OK = 0x22C55E
COLOR_FAX_NG = 0xEF4444
COLOR_TEST = 0x8B5CF6
COLOR_OTHER = 0x6B7280

_DIRECTION_LABELS = {"inbound": "着信", "outbound": "発信"}


@dataclass
class ChatField:
    name: str
    value: str
    inline: bool = True


@dataclass
class ChatLink:
    label: str
    url: str


@dataclass
class ChatMessage:
    """形式に依存しない通知内容。"""

    title: str
    # 通知のプレビューや fallback に使う 1 行要約。
    summary: str
    description: str | None = None
    fields: list[ChatField] = field(default_factory=list)
    link: ChatLink | None = None
    color: int = COLOR_OTHER


def truncate(text: str, limit: int) -> str:
    """limit 文字以内に切り詰める（超えた場合は末尾を「…」にする）。"""
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _add(fields: list[ChatField], name: str, value: Any, *, inline: bool = True) -> None:
    text = _text(value)
    if text is not None:
        fields.append(ChatField(name, text, inline))


def _party(number: Any, name: Any) -> str | None:
    """「番号（名前）」。名前が空・番号と同じなら番号だけ。"""
    num = _text(number)
    label = _text(name)
    if num is None:
        return label
    if label is None or label == num:
        return num
    return f"{num}（{label}）"


def format_duration(seconds: Any) -> str | None:
    if seconds is None:
        return None
    try:
        total = max(int(seconds), 0)
    except (TypeError, ValueError):
        return None
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}時間{minutes}分{secs}秒"
    if minutes:
        return f"{minutes}分{secs}秒"
    return f"{secs}秒"


def _dict(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    return value if isinstance(value, dict) else {}


def describe_event(event_name: str, data: dict[str, Any]) -> ChatMessage:
    """Webhook イベントを人が読める日本語の通知内容にする。"""
    if event_name == "call.inbound":
        call = _dict(data, "call")
        caller = _party(call.get("from"), call.get("caller_name")) or "番号非通知"
        fields: list[ChatField] = []
        _add(fields, "発信元", caller)
        _add(fields, "着信先", call.get("to"))
        _add(fields, "トランク", call.get("trunk"))
        to = _text(call.get("to"))
        summary = f"📞 着信: {caller}" + (f" → {to}" if to else "")
        return ChatMessage("📞 着信", summary, fields=fields, color=COLOR_INBOUND)

    if event_name == "call.ended":
        call = _dict(data, "call")
        caller = _party(call.get("from"), call.get("caller_name"))
        direction = _text(call.get("direction"))
        fields = []
        _add(fields, "種別", _DIRECTION_LABELS.get(direction or "", direction))
        _add(fields, "発信元", caller)
        _add(fields, "着信先", call.get("to"))
        _add(fields, "トランク", call.get("trunk"))
        answered = call.get("answered_at") is not None
        talk = format_duration(call.get("billsec_seconds")) if answered else "未応答"
        _add(fields, "通話時間", talk)
        _add(fields, "切断理由", call.get("hangup_cause"))
        summary = "☎️ 通話終了"
        if caller:
            summary += f": {caller}"
        if talk:
            summary += f"（{talk}）"
        return ChatMessage("☎️ 通話終了", summary, fields=fields, color=COLOR_ENDED)

    if event_name == "fax.received":
        fax = _dict(data, "fax")
        success = bool(fax.get("success"))
        pages = fax.get("pages")
        fields = []
        _add(fields, "発信元", fax.get("from"))
        _add(fields, "着信先", fax.get("to"))
        _add(fields, "送信元局 ID", fax.get("remote_station_id"))
        _add(fields, "ページ数", f"{pages} ページ" if pages is not None else None)
        result = "成功" if success else "失敗"
        detail = _text(fax.get("result_text"))
        if not success and detail:
            result += f"（{detail}）"
        _add(fields, "結果", result)
        pdf_url = _text(fax.get("pdf_url"))
        sender = _text(fax.get("from"))
        summary = "📠 FAX 受信" + (f": {sender}" if sender else "")
        summary += (
            f"（{pages} ページ・{'成功' if success else '失敗'}）" if pages is not None else ""
        )
        return ChatMessage(
            "📠 FAX 受信",
            summary,
            fields=fields,
            link=ChatLink("PDF を開く", pdf_url) if pdf_url else None,
            color=COLOR_FAX_OK if success else COLOR_FAX_NG,
        )

    if event_name == "webhook.test":
        test = _dict(data, "test")
        fields = []
        _add(fields, "Webhook ID", test.get("webhook_id"))
        return ChatMessage(
            "🔔 Webhook テスト",
            "🔔 millicall Webhook のテスト送信です",
            description="millicall からのテスト送信です。このメッセージが表示されれば設定は完了しています。",
            fields=fields,
            color=COLOR_TEST,
        )

    return ChatMessage(event_name, f"millicall: {event_name}")


# --------------------------------------------------------------------------- 形式ごとの本文


def _discord(msg: ChatMessage, event_name: str, occurred_at: str) -> dict[str, Any]:
    embed: dict[str, Any] = {
        "title": truncate(msg.title, DISCORD_TITLE_MAX),
        "color": msg.color,
        "timestamp": occurred_at,
        "footer": {"text": f"millicall · {event_name}"},
    }
    if msg.description:
        embed["description"] = truncate(msg.description, DISCORD_DESCRIPTION_MAX)
    fields = [
        {
            "name": truncate(f.name, DISCORD_FIELD_NAME_MAX),
            "value": truncate(f.value, DISCORD_FIELD_VALUE_MAX),
            "inline": f.inline,
        }
        for f in msg.fields
    ]
    if msg.link is not None:
        # タイトルをリンクにし、項目にもリンクを置く（項目の上限を超える長い URL は置かない）。
        embed["url"] = msg.link.url
        value = f"[{msg.link.label}]({msg.link.url})"
        if len(value) <= DISCORD_FIELD_VALUE_MAX:
            fields.append({"name": "PDF", "value": value, "inline": False})
    if fields:
        embed["fields"] = fields[:DISCORD_FIELDS_MAX]
    return {
        "content": truncate(msg.summary, DISCORD_CONTENT_MAX),
        "embeds": [embed],
        # 発信者名などに含まれる @everyone 等でメンションしない。
        "allowed_mentions": {"parse": []},
    }


def slack_escape(text: str) -> str:
    """Slack mrkdwn の制御文字（& < >）をエスケープする（<!channel> 等の注入防止）。"""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_date(occurred_at: str) -> str:
    try:
        epoch = int(datetime.fromisoformat(occurred_at.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return slack_escape(occurred_at)
    return f"<!date^{epoch}^{{date_num}} {{time_secs}}|{slack_escape(occurred_at)}>"


def _slack(msg: ChatMessage, event_name: str, occurred_at: str) -> dict[str, Any]:
    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": truncate(msg.title, SLACK_HEADER_MAX),
                "emoji": True,
            },
        }
    ]
    if msg.description:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": truncate(slack_escape(msg.description), SLACK_TEXT_MAX),
                },
            }
        )
    items = [
        {
            "type": "mrkdwn",
            "text": truncate(f"*{slack_escape(f.name)}*\n{slack_escape(f.value)}", SLACK_FIELD_MAX),
        }
        for f in msg.fields
    ]
    for i in range(0, len(items), SLACK_FIELDS_PER_SECTION):
        blocks.append({"type": "section", "fields": items[i : i + SLACK_FIELDS_PER_SECTION]})
    if msg.link is not None:
        link = f"<{msg.link.url}|{slack_escape(msg.link.label)}>"
        if len(link) <= SLACK_TEXT_MAX:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": link}})
    blocks.append(
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"millicall · {slack_escape(event_name)} · {_slack_date(occurred_at)}",
                }
            ],
        }
    )
    return {"text": truncate(slack_escape(msg.summary), SLACK_TEXT_MAX), "blocks": blocks}


def _teams(msg: ChatMessage, event_name: str, occurred_at: str) -> dict[str, Any]:
    body: list[dict[str, Any]] = [
        {
            "type": "TextBlock",
            "text": truncate(msg.title, TEAMS_TEXT_MAX),
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        }
    ]
    if msg.description:
        body.append(
            {"type": "TextBlock", "text": truncate(msg.description, TEAMS_TEXT_MAX), "wrap": True}
        )
    if msg.fields:
        body.append(
            {
                "type": "FactSet",
                "facts": [
                    {"title": f.name, "value": truncate(f.value, TEAMS_FACT_VALUE_MAX)}
                    for f in msg.fields
                ],
            }
        )
    body.append(
        {
            "type": "TextBlock",
            "text": f"millicall · {event_name} · {occurred_at}",
            "size": "Small",
            "isSubtle": True,
            "wrap": True,
        }
    )
    content: dict[str, Any] = {
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "type": "AdaptiveCard",
        "version": "1.4",
        "body": body,
    }
    if msg.link is not None:
        content["actions"] = [
            {"type": "Action.OpenUrl", "title": msg.link.label, "url": msg.link.url}
        ]
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": content,
            }
        ],
    }


_BUILDERS = {FORMAT_DISCORD: _discord, FORMAT_SLACK: _slack, FORMAT_TEAMS: _teams}


def build_chat_message(
    fmt: str, event_name: str, data: dict[str, Any], occurred_at: str
) -> dict[str, Any]:
    """チャット形式（discord / slack / teams）の本文 dict を返す。

    occurred_at は ISO 8601（UTC、``Z`` 付き）。generic など未知の形式は ValueError。
    """
    builder = _BUILDERS.get(fmt)
    if builder is None:
        raise ValueError(f"チャット形式ではありません: {fmt}")
    return builder(describe_event(event_name, data), event_name, occurred_at)
