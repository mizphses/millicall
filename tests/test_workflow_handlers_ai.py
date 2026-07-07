"""Task 7: AI 会話系ノードハンドラ TDD
(ai_conversation / intent_detection / collect_info).

テスト方針:
  - ChannelContext は bare インスタンス（ESL 接続なし）
  - primitives は AsyncMock で差し替え
  - LLM プロバイダはフェイク非同期ジェネレータを持つ FakeLLM で差し替え
  - agent_resolver / provider_resolver を直接注入してテスト可能にする
  - 各ハンドラが HANDLERS に登録されていることも確認する
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from millicall.workflows.context import ChannelContext

# ハンドラのインポート（副作用で HANDLERS に登録される）
from millicall.workflows.handlers.ai import (
    handle_ai_conversation,
    handle_collect_info,
    handle_intent_detection,
)

# --------------------------------------------------------------------------- #
# ヘルパ・フェイク
# --------------------------------------------------------------------------- #


def make_ctx(variables: dict | None = None) -> ChannelContext:
    ctx = ChannelContext(uuid="test-uuid")
    if variables:
        for k, v in variables.items():
            ctx.set_var(k, v)
    return ctx


def make_fake_primitives(
    listen_returns: list[str] | None = None,
    say_and_listen_returns: list[tuple[str, str]] | None = None,
) -> MagicMock:
    """フェイク primitives を返す。

    listen_returns: listen() が順に返す文字列のリスト。
    say_and_listen_returns: say_and_listen() が順に返す (tts_text, stt_text) のリスト。
    """
    p = MagicMock()
    p.say = AsyncMock(return_value=0.5)

    if listen_returns is not None:
        p.listen = AsyncMock(side_effect=listen_returns)
    else:
        p.listen = AsyncMock(return_value="")

    if say_and_listen_returns is not None:
        p.say_and_listen = AsyncMock(side_effect=say_and_listen_returns)
    else:
        p.say_and_listen = AsyncMock(return_value=("", ""))

    return p


class FakeLLM:
    """stream_chat が canned チャンクを yield するフェイク LLM プロバイダ。

    responses: [("chunk1", "chunk2"), ...] — 呼び出しごとに次のリストを使う。
    """

    def __init__(self, responses: list[list[str]]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    def stream_chat(self, messages):
        """非同期ジェネレータとして canned チャンクを返す。"""
        chunks = (
            self._responses[self._call_index]
            if self._call_index < len(self._responses)
            else []
        )
        self._call_index += 1
        return self._async_gen(chunks)

    @staticmethod
    async def _async_gen(chunks):
        for chunk in chunks:
            yield chunk


# ---- ノードファクトリ ---- #


def make_collect_info_node(
    fields: dict[str, str] | None = None,
    agent_id: int = 1,
    tts_provider_id: int | None = None,
    confirmation: bool = True,
):
    from millicall.workflows.nodes import CollectInfoConfig, CollectInfoNode

    return CollectInfoNode(
        id="ci1",
        type="collect_info",
        config=CollectInfoConfig(
            fields=fields or {"name": "お名前を教えてください"},
            agent_id=agent_id,
            tts_provider_id=tts_provider_id,
            confirmation=confirmation,
        ),
    )


def make_intent_detection_node(
    intents: dict[str, str] | None = None,
    llm_provider_id: int = 10,
    fallback_intent: str = "other",
):
    from millicall.workflows.nodes import IntentDetectionConfig, IntentDetectionNode

    return IntentDetectionNode(
        id="id1",
        type="intent_detection",
        config=IntentDetectionConfig(
            intents=intents or {"support": "技術サポート", "sales": "営業・購入"},
            llm_provider_id=llm_provider_id,
            fallback_intent=fallback_intent,
        ),
    )


def make_ai_conversation_node(
    agent_id: int | None = 1,
    system_prompt_override: str | None = None,
    greeting_override: str = "",
    max_turns: int = 3,
    extraction_mode: str = "auto",
    extract_variables: dict[str, str] | None = None,
):
    from millicall.workflows.nodes import AiConversationConfig, AiConversationNode

    return AiConversationNode(
        id="ac1",
        type="ai_conversation",
        config=AiConversationConfig(
            agent_id=agent_id,
            system_prompt_override=system_prompt_override,
            greeting_override=greeting_override,
            max_turns=max_turns,
            extraction_mode=extraction_mode,
            extract_variables=extract_variables or {},
        ),
    )


def make_fake_agent(
    system_prompt: str = "あなたは親切なアシスタントです",
    greeting: str = "こんにちは！",
    llm_provider_id: int = 10,
    tts_provider_id: int = 20,
    stt_provider_id: int = 30,
    max_history: int = 20,
) -> MagicMock:
    agent = MagicMock()
    agent.system_prompt = system_prompt
    agent.greeting = greeting
    agent.llm_provider_id = llm_provider_id
    agent.tts_provider_id = tts_provider_id
    agent.stt_provider_id = stt_provider_id
    agent.max_history = max_history
    return agent


# --------------------------------------------------------------------------- #
# collect_info
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_collect_info_two_fields_stored() -> None:
    """2 つのフィールドを順番に質問し、両方の変数が格納される。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(
        say_and_listen_returns=[
            ("お名前を教えてください", "山田太郎"),  # name の質問 + 答え
            ("山田太郎 でよろしいですか？", "はい"),  # name の確認
            ("ご連絡先を教えてください", "090-1234-5678"),  # phone の質問 + 答え
            ("090-1234-5678 でよろしいですか？", "はい"),  # phone の確認
        ]
    )
    node = make_collect_info_node(
        fields={"name": "お名前を教えてください", "phone": "ご連絡先を教えてください"},
        confirmation=True,
    )

    result = await handle_collect_info(node, ctx)

    assert result is None
    assert ctx.get_var("name") == "山田太郎"
    assert ctx.get_var("phone") == "090-1234-5678"


@pytest.mark.asyncio
async def test_collect_info_no_confirmation_skips_confirm() -> None:
    """confirmation=False の場合は確認をスキップする。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(
        say_and_listen_returns=[
            ("お名前を教えてください", "鈴木一郎"),
        ]
    )
    node = make_collect_info_node(
        fields={"name": "お名前を教えてください"},
        confirmation=False,
    )

    result = await handle_collect_info(node, ctx)

    assert result is None
    assert ctx.get_var("name") == "鈴木一郎"
    # say_and_listen は 1 回だけ（確認なし）
    assert ctx.primitives.say_and_listen.call_count == 1


@pytest.mark.asyncio
async def test_collect_info_confirmation_negative_reasks() -> None:
    """確認で否定返答 → 再質問 → 再回答が格納される。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(
        say_and_listen_returns=[
            ("お名前を教えてください", "田中"),       # 初回質問
            ("田中 でよろしいですか？", "いいえ"),     # 確認 → 否定
            ("お名前を教えてください", "田中一郎"),    # 再質問
        ]
    )
    node = make_collect_info_node(
        fields={"name": "お名前を教えてください"},
        confirmation=True,
    )

    result = await handle_collect_info(node, ctx)

    assert result is None
    # 再質問後の値が格納されている
    assert ctx.get_var("name") == "田中一郎"
    assert ctx.primitives.say_and_listen.call_count == 3


@pytest.mark.asyncio
async def test_collect_info_confirmation_english_no_reasks() -> None:
    """英語の 'no' で否定を検出して再質問する。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(
        say_and_listen_returns=[
            ("問い合わせ内容を教えてください", "product inquiry"),
            ("product inquiry でよろしいですか？", "no, wrong"),
            ("問い合わせ内容を教えてください", "billing question"),
        ]
    )
    node = make_collect_info_node(
        fields={"inquiry": "問い合わせ内容を教えてください"},
        confirmation=True,
    )

    await handle_collect_info(node, ctx)

    assert ctx.get_var("inquiry") == "billing question"


@pytest.mark.asyncio
async def test_collect_info_no_primitives_sets_empty_vars() -> None:
    """ctx.primitives が None の場合は全変数を空文字で設定して graceful に終了する。"""
    ctx = make_ctx()
    # primitives = None（デフォルト）
    node = make_collect_info_node(
        fields={"name": "お名前は？", "age": "年齢は？"},
        confirmation=True,
    )

    result = await handle_collect_info(node, ctx)

    assert result is None
    assert ctx.get_var("name") == ""
    assert ctx.get_var("age") == ""


@pytest.mark.asyncio
async def test_collect_info_empty_answer_no_confirmation() -> None:
    """答えが空（STT 失敗）の場合は確認をスキップして空文字を格納する。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(
        say_and_listen_returns=[
            ("お名前を教えてください", ""),  # 空回答
        ]
    )
    node = make_collect_info_node(
        fields={"name": "お名前を教えてください"},
        confirmation=True,
    )

    result = await handle_collect_info(node, ctx)

    assert result is None
    assert ctx.get_var("name") == ""
    # 空回答なので確認ループは実行されていない（say_and_listen は 1 回のみ）
    assert ctx.primitives.say_and_listen.call_count == 1


def test_collect_info_registered_in_handlers() -> None:
    """collect_info ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "collect_info" in HANDLERS


# --------------------------------------------------------------------------- #
# intent_detection
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_intent_detection_valid_key_returned() -> None:
    """LLM が有効な意図キーを返す → そのキーが返る。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(listen_returns=["技術的な問題があります"])

    fake_llm = FakeLLM([["support"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    node = make_intent_detection_node(
        intents={"support": "技術サポート", "sales": "営業・購入"},
        llm_provider_id=10,
        fallback_intent="other",
    )

    result = await handle_intent_detection(node, ctx)

    assert result == "support"


@pytest.mark.asyncio
async def test_intent_detection_llm_returns_junk_falls_back() -> None:
    """LLM が不正な文字列を返す → fallback_intent が返る。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(listen_returns=["なんか助けてほしい"])

    fake_llm = FakeLLM([["INVALID_INTENT_XYZ"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    node = make_intent_detection_node(
        intents={"support": "技術サポート", "sales": "営業"},
        fallback_intent="other",
    )

    result = await handle_intent_detection(node, ctx)

    assert result == "other"


@pytest.mark.asyncio
async def test_intent_detection_llm_none_falls_back() -> None:
    """LLM プロバイダが解決できない（None）→ fallback_intent。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(listen_returns=["テスト発話"])
    ctx.provider_resolver = AsyncMock(return_value=None)

    node = make_intent_detection_node(fallback_intent="other")

    result = await handle_intent_detection(node, ctx)

    assert result == "other"


@pytest.mark.asyncio
async def test_intent_detection_empty_utterance_falls_back() -> None:
    """listen() が空文字を返す → fallback_intent。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(listen_returns=[""])

    fake_llm = FakeLLM([["support"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    node = make_intent_detection_node(fallback_intent="other")

    result = await handle_intent_detection(node, ctx)

    assert result == "other"
    # 空発話なので LLM は呼ばれていない
    assert fake_llm._call_index == 0  # stream_chat 未呼び出し


@pytest.mark.asyncio
async def test_intent_detection_no_primitives_falls_back() -> None:
    """ctx.primitives が None → 発話取得不可 → fallback_intent。"""
    ctx = make_ctx()
    # primitives = None

    fake_llm = FakeLLM([["support"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    node = make_intent_detection_node(fallback_intent="other")

    result = await handle_intent_detection(node, ctx)

    assert result == "other"


@pytest.mark.asyncio
async def test_intent_detection_case_insensitive_match() -> None:
    """LLM の応答は大文字小文字を無視して照合される。"""
    ctx = make_ctx()
    ctx.primitives = make_fake_primitives(listen_returns=["問合せ"])

    fake_llm = FakeLLM([["SALES"]])  # 大文字で返す
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    node = make_intent_detection_node(
        intents={"support": "サポート", "sales": "営業"},
        fallback_intent="other",
    )

    result = await handle_intent_detection(node, ctx)

    assert result == "sales"


def test_intent_detection_registered_in_handlers() -> None:
    """intent_detection ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "intent_detection" in HANDLERS


# --------------------------------------------------------------------------- #
# ai_conversation
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ai_conversation_greeting_said() -> None:
    """挨拶文が primitives.say() で再生される。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="いらっしゃいませ！")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    # 1 ターン: ユーザが "ありがとう" → LLM が "どういたしまして" → [END_CALL]
    fake_llm = FakeLLM([["どういたしまして[END_CALL]"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["ありがとう"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(agent_id=1, max_turns=5)

    result = await handle_ai_conversation(node, ctx)

    assert result is None
    # 挨拶が先頭で再生されていること
    first_say_call = ctx.primitives.say.call_args_list[0]
    assert first_say_call.args[0] == "いらっしゃいませ！"


@pytest.mark.asyncio
async def test_ai_conversation_end_call_marker_triggers_hangup() -> None:
    """LLM 応答に [END_CALL] が含まれる → hangup が呼ばれてループが終了。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    fake_llm = FakeLLM([["ご利用ありがとうございました。[END_CALL]"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["さようなら"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(agent_id=1, max_turns=5)

    await handle_ai_conversation(node, ctx)

    assert ctx.hung_up is True
    # "[END_CALL]" が除去された残りのテキストが say() で再生されていること
    say_calls = ctx.primitives.say.call_args_list
    texts_said = [c.args[0] for c in say_calls]
    assert any("ご利用ありがとうございました" in t for t in texts_said)
    assert not any("[END_CALL]" in t for t in texts_said)


@pytest.mark.asyncio
async def test_ai_conversation_max_turns_respected() -> None:
    """max_turns に達したらループが終了し、hangup されない。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    # 各ターンで通常応答（[END_CALL] なし）
    fake_llm = FakeLLM([["応答1"], ["応答2"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(
        listen_returns=["ユーザ発話1", "ユーザ発話2"]
    )
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(agent_id=1, max_turns=2)

    result = await handle_ai_conversation(node, ctx)

    assert result is None
    assert ctx.hung_up is False
    # listen は 2 回呼ばれる（max_turns=2）
    assert ctx.primitives.listen.call_count == 2


@pytest.mark.asyncio
async def test_ai_conversation_two_consecutive_empty_breaks() -> None:
    """listen() が 2 回連続で空文字を返した場合はループを終了する。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    fake_llm = FakeLLM([])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(
        listen_returns=["", ""]  # 2 回連続空
    )

    node = make_ai_conversation_node(agent_id=1, max_turns=10)

    result = await handle_ai_conversation(node, ctx)

    assert result is None
    assert ctx.hung_up is False
    # LLM は呼ばれていない
    assert fake_llm._call_index == 0


@pytest.mark.asyncio
async def test_ai_conversation_extract_variables_auto() -> None:
    """extraction_mode='auto' → LLM で変数抽出され ctx に格納される。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    # ターン応答 + 抽出応答の 2 回分
    extracted = json.dumps({"customer_name": "佐藤様", "issue": "ログインできない"})
    fake_llm = FakeLLM([
        ["[END_CALL]"],           # ターン: すぐ終話
        [extracted],              # 抽出呼び出し
    ])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["ログインできません"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(
        agent_id=1,
        max_turns=5,
        extraction_mode="auto",
        extract_variables={"customer_name": "顧客名", "issue": "問題内容"},
    )

    await handle_ai_conversation(node, ctx)

    assert ctx.get_var("customer_name") == "佐藤様"
    assert ctx.get_var("issue") == "ログインできない"


@pytest.mark.asyncio
async def test_ai_conversation_extract_variables_malformed_json() -> None:
    """抽出 LLM が不正な JSON を返した場合は全変数を空文字に設定する。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    fake_llm = FakeLLM([
        ["[END_CALL]"],       # ターン応答
        ["not json at all"],  # 不正な抽出応答
    ])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["テスト"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(
        agent_id=1,
        max_turns=5,
        extraction_mode="auto",
        extract_variables={"name": "名前", "issue": "問題"},
    )

    await handle_ai_conversation(node, ctx)

    assert ctx.get_var("name") == ""
    assert ctx.get_var("issue") == ""


@pytest.mark.asyncio
async def test_ai_conversation_extract_variables_direct() -> None:
    """extraction_mode='direct' → 最後のユーザ発話が先頭変数に格納される。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    fake_llm = FakeLLM([["[END_CALL]"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["私の問題は接続エラーです"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(
        agent_id=1,
        max_turns=5,
        extraction_mode="direct",
        extract_variables={"raw_input": "ユーザの生の発話"},
    )

    await handle_ai_conversation(node, ctx)

    # ユーザの最後の発話がそのまま格納される
    assert ctx.get_var("raw_input") == "私の問題は接続エラーです"


@pytest.mark.asyncio
async def test_ai_conversation_agent_none_override_only_graceful() -> None:
    """agent_id なし + system_prompt_override のみ（llm=None） → 挨拶再生 + graceful 終了。"""
    ctx = make_ctx()
    # agent_resolver は不要（agent_id=None）
    # provider_resolver も不要（llm_provider_id なし）
    ctx.primitives = make_fake_primitives()

    node = make_ai_conversation_node(
        agent_id=None,
        system_prompt_override="あなたはシンプルなボットです",
        greeting_override="こんにちは！（オーバーライド）",
        max_turns=3,
    )

    result = await handle_ai_conversation(node, ctx)

    assert result is None
    assert ctx.hung_up is False
    # 挨拶は再生されている
    ctx.primitives.say.assert_awaited_once_with("こんにちは！（オーバーライド）")
    # listen は呼ばれていない（llm がないため graceful skip）
    ctx.primitives.listen.assert_not_called()


@pytest.mark.asyncio
async def test_ai_conversation_greeting_override_takes_precedence() -> None:
    """greeting_override が設定されていればエージェントの greeting より優先される。"""
    ctx = make_ctx()

    fake_agent = make_fake_agent(greeting="エージェントの挨拶")
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    fake_llm = FakeLLM([["[END_CALL]"]])
    ctx.provider_resolver = AsyncMock(return_value=fake_llm)

    ctx.primitives = make_fake_primitives(listen_returns=["テスト"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(
        agent_id=1,
        greeting_override="上書き挨拶",
        max_turns=2,
    )

    await handle_ai_conversation(node, ctx)

    first_say = ctx.primitives.say.call_args_list[0].args[0]
    assert first_say == "上書き挨拶"


@pytest.mark.asyncio
async def test_ai_conversation_system_prompt_override_takes_precedence() -> None:
    """system_prompt_override が設定されていればエージェントの system_prompt より優先される。

    このテストでは LLM に渡される messages の先頭が override のシステムプロンプトであることを
    間接的に確認する（LLM のフェイクが 1 ターンで [END_CALL] を返すため）。
    """
    ctx = make_ctx()

    fake_agent = make_fake_agent(
        system_prompt="エージェントのシステムプロンプト",
        greeting="",
    )
    ctx.agent_resolver = AsyncMock(return_value=fake_agent)

    captured_messages = []

    async def _end_call_gen():
        yield "[END_CALL]"

    class CaptureLLM:
        def stream_chat(self, messages):
            captured_messages.extend(messages)
            return _end_call_gen()

    ctx.provider_resolver = AsyncMock(return_value=CaptureLLM())

    ctx.primitives = make_fake_primitives(listen_returns=["発話"])
    ctx.call_control = MagicMock()
    ctx.call_control.hangup = AsyncMock()

    node = make_ai_conversation_node(
        agent_id=1,
        system_prompt_override="上書きシステムプロンプト",
        max_turns=2,
    )

    await handle_ai_conversation(node, ctx)

    assert captured_messages[0].role == "system"
    assert captured_messages[0].content == "上書きシステムプロンプト"


def test_ai_conversation_registered_in_handlers() -> None:
    """ai_conversation ハンドラが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "ai_conversation" in HANDLERS


# --------------------------------------------------------------------------- #
# 全ハンドラ登録確認（一括）
# --------------------------------------------------------------------------- #


def test_all_three_ai_handlers_registered() -> None:
    """3 つの AI ハンドラすべてが HANDLERS に登録されている。"""
    from millicall.workflows.executor import HANDLERS

    assert "ai_conversation" in HANDLERS
    assert "intent_detection" in HANDLERS
    assert "collect_info" in HANDLERS
