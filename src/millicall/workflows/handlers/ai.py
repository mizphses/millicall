"""AI 会話系ノードハンドラ — ai_conversation / intent_detection / collect_info
(Phase 4b Task 7).

各ハンドラは :func:`~millicall.workflows.executor.register_handler` を使って
グローバルレジストリに登録される。このモジュールをインポートするだけで登録が完了する。

設計原則:
  * **シンプルなターンベースプリミティブを使用**: WS 駆動の ``ConversationSession``
    (mod_audio_stream と結合) は使わず、``ctx.primitives.say()`` / ``ctx.primitives.listen()``
    / ``ctx.primitives.say_and_listen()`` と ``LLMProvider.stream_chat()`` を直接使う。
    これにより各ハンドラは自己完結的であり、フェイクを使ったユニットテストが容易になる。
  * **ai_conversation**: エージェント（またはシステムプロンプト上書き）を元に LLM と
    ターンベースで会話する。``[END_CALL]`` マーカーで hangup。会話後に変数抽出を実行。
  * **intent_detection**: ユーザ発話を LLM で分類し、一致した意図キーを出力ハンドルとして返す。
  * **collect_info**: 複数フィールドを順次質問・確認し、変数に格納する。
  * ctx リソース（primitives / provider_resolver / agent_resolver）が None の場合は
    graceful skip（unit テストで実 ESL / DB 不要）。

ctx への要求:
  * ``ctx.primitives``:
    - say(text: str) → Awaitable[float]           TTS 再生
    - listen(max_seconds: int) → Awaitable[str]   STT 認識
    - say_and_listen(text: str, max_seconds: int)
        → Awaitable[tuple[str, str]]               TTS 再生後すぐに STT 認識
  * ``ctx.resolve_agent(agent_id: int)`` → AiAgent | None
  * ``ctx.resolve_provider(provider_id: int)`` → LLMProvider | None
  * ``ctx.render(text)``、``ctx.set_var(name, value)``、``ctx.get_var(name)``
  * ``ctx.hangup()``

[END_CALL] 規約:
  ``_END_MARKER = "[END_CALL]"`` — LLM 応答にこの文字列が含まれていれば通話終了を意図する。
  ai_conversation はマーカーを除去した残りのテキストを再生してから hangup する。

空 listen ルール（ai_conversation のみ）:
  listen() が空文字を返した場合（無音・STT 失敗）は 1 ターン分許容し、
  2 回連続で空だった場合はループを終了する。
  単発の空発話で会話を終わらせないためのルール。

確認ループのネガティブ検出ヒューリスティック（collect_info）:
  以下のいずれかを含む場合を「否定」とみなす（大文字小文字は無視）:
    日本語: "いいえ" "ちがう" "違う" "異なる" "ちがい"
    英語  : "no" "nope" "wrong" "incorrect" "different"
  これらを含まない場合は「肯定」とみなし、収集した値を確定する。
  確認は最大 1 回のリトライ（再質問 1 回まで）。

extraction_mode の解釈（ai_conversation）:
  * "auto"  : 会話履歴全体を LLM に提示し、変数→説明 マップに従って JSON で抽出させる。
              JSON パースに失敗した場合は各変数を "" に設定する。
  * "direct": LLM による抽出呼び出しを行わず、最後のユーザ発話をそのまま先頭変数に格納する。
              ``extract_variables`` が複数ある場合でも 1 番目の変数だけが設定される。
              単一入力フィールドの直接保存に特化したモード（抽出 LLM コスト削減）。
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from millicall.ai.llm.base import ChatMessage
from millicall.workflows.executor import register_handler

if TYPE_CHECKING:
    from millicall.workflows.context import ChannelContext

logger = logging.getLogger("millicall.workflows.handlers.ai")

# ai_conversation / conversation.py と同一マーカー
_END_MARKER = "[END_CALL]"

# 確認否定とみなすキーワード（小文字で比較）
_NEGATIVE_KEYWORDS = (
    "いいえ",
    "ちがう",
    "違う",
    "異なる",
    "ちがい",
    "no",
    "nope",
    "wrong",
    "incorrect",
    "different",
)


def _is_negative(reply: str) -> bool:
    """ユーザ返答が否定かどうかを判定する（確認ループ用）。

    ``_NEGATIVE_KEYWORDS`` のいずれかが返答に含まれていれば否定とみなす。
    大文字・小文字は区別しない（ASCII のみ）。
    """
    lower = reply.lower()
    return any(kw in lower for kw in _NEGATIVE_KEYWORDS)


async def _collect_llm_response(llm, messages: list[ChatMessage]) -> str:
    """LLM の stream_chat を消費し、完全な応答文字列を返す。"""
    chunks = []
    async for chunk in llm.stream_chat(messages):
        chunks.append(chunk)
    return "".join(chunks)


# --------------------------------------------------------------------------- #
# collect_info ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("collect_info")
async def handle_collect_info(node: object, ctx: ChannelContext) -> None:
    """情報収集ノード。

    config.fields（変数名→質問文 の辞書）を順番に質問し、回答を変数に格納する。
    config.confirmation が True の場合は回答を読み上げて確認を求める。
    否定返答があれば同じ質問をもう 1 回だけ再実施する（最大 1 リトライ）。

    ctx.primitives が None の場合は各変数を空文字に設定して graceful に終了する。
    戻り値は None（単一出力ノード → "out" 既定遷移）。
    """
    config = node.config  # type: ignore[attr-defined]

    if ctx.primitives is None:
        # primitives なし（unit テスト / エンジンコア）→ 全変数を空文字に設定
        for var_name in config.fields:
            ctx.set_var(var_name, "")
        return None

    for var_name, question in config.fields.items():
        rendered_question = ctx.render(question)

        # 初回質問
        _, answer = await ctx.primitives.say_and_listen(rendered_question)

        if config.confirmation and answer:
            # 確認: 回答を読み上げて yes/no を聞く
            confirm_prompt = f"{answer} でよろしいですか？"
            _, confirm_reply = await ctx.primitives.say_and_listen(confirm_prompt)

            if _is_negative(confirm_reply):
                # 否定 → 1 回だけ再質問し、その答えを使う
                _, answer = await ctx.primitives.say_and_listen(rendered_question)

        ctx.set_var(var_name, answer)

    return None


# --------------------------------------------------------------------------- #
# intent_detection ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("intent_detection")
async def handle_intent_detection(node: object, ctx: ChannelContext) -> str:
    """意図検出ノード。

    ユーザ発話を LLM で分類し、一致した意図キー文字列を返す。
    戻り値は executor が出力ハンドルとして使用する（intent_detection の output_handles は
    ``intents`` の各キー + ``fallback_intent`` のリスト）。

    フォールバック条件:
      - ctx.primitives が None → 発話取得不可 → fallback_intent を返す
      - LLM プロバイダが解決できない（None） → fallback_intent
      - 発話が空 → fallback_intent
      - LLM 応答が有効な意図キーでない → fallback_intent

    分類プロセス:
      1. ``listen()`` でユーザ発話を取得する
      2. システムメッセージに「次の意図キーのうち最も適切な 1 つだけ答えよ」と指示
      3. ユーザメッセージに発話を渡す
      4. 応答を strip して意図キーと照合（大文字小文字を無視）
    """
    config = node.config  # type: ignore[attr-defined]
    fallback = config.fallback_intent

    # 発話取得
    if ctx.primitives is None:
        utterance = ""
    else:
        utterance = await ctx.primitives.listen()

    if not utterance:
        return fallback

    # LLM 解決
    llm = await ctx.resolve_provider(config.llm_provider_id)
    if llm is None:
        logger.warning(
            "intent_detection: LLM プロバイダ %d を解決できません。fallback_intent を使用します",
            config.llm_provider_id,
        )
        return fallback

    # 意図一覧を文字列化してシステムプロンプトへ
    intent_descriptions = "\n".join(f"- {key}: {desc}" for key, desc in config.intents.items())
    system_msg = (
        "あなたはユーザの発話を分類するアシスタントです。\n"
        "以下の意図キーの中から最も適切な 1 つを選び、そのキー文字列のみを返答してください。"
        " 余計な文字や説明を加えてはいけません。\n\n"
        f"意図キー一覧:\n{intent_descriptions}"
    )

    messages = [
        ChatMessage("system", system_msg),
        ChatMessage("user", utterance),
    ]

    raw = await _collect_llm_response(llm, messages)
    reply = raw.strip()

    # 大文字小文字を無視して意図キーと照合
    intent_keys = list(config.intents.keys())
    reply_lower = reply.lower()
    for key in intent_keys:
        if key.lower() == reply_lower:
            return key

    logger.info(
        "intent_detection: LLM 応答 %r が有効な意図キーに一致しません。fallback_intent=%r を使用",
        reply,
        fallback,
    )
    return fallback


# --------------------------------------------------------------------------- #
# ai_conversation ハンドラ
# --------------------------------------------------------------------------- #


@register_handler("ai_conversation")
async def handle_ai_conversation(node: object, ctx: ChannelContext) -> None:
    """AI 会話ノード。

    エージェント（またはシステムプロンプト上書き）を元に LLM と
    ターンベースで会話する。

    動作フロー:
      1. エージェント / システムプロンプト / 挨拶文を決定する
      2. LLM プロバイダを解決する
         - LLM が None または ctx.primitives が None の場合:
           挨拶文を読み上げ（primitives がある場合）て graceful に終了する
           （Task 9 でランナーファクトリが適切なリソースを注入する）
      3. 挨拶文がある場合は say() で再生する
      4. max_turns 回のターンループ:
         a. listen() でユーザ発話を取得
         b. 空発話が 2 回連続の場合はループ終了（空発話ルール）
         c. LLM に messages を渡してレスポンスを取得
         d. [END_CALL] が含まれていれば残りのテキストを再生して hangup
         e. そうでなければ応答を再生し、履歴に追加
      5. 会話後に extract_variables が設定されていれば変数抽出を実施

    extraction_mode の詳細は本モジュールの docstring を参照。
    戻り値は None（単一出力ノード → "out" 既定遷移）。
    """
    config = node.config  # type: ignore[attr-defined]

    # ----- 1. エージェント / システムプロンプト / 挨拶文の決定 -----
    agent = None
    if config.agent_id is not None:
        agent = await ctx.resolve_agent(config.agent_id)

    # システムプロンプト: override が空白でなければ優先
    if config.system_prompt_override and config.system_prompt_override.strip():
        system_prompt = config.system_prompt_override
    elif agent is not None:
        system_prompt = agent.system_prompt
    else:
        system_prompt = ""

    # 挨拶文: greeting_override が空でなければ優先
    if config.greeting_override:
        greeting = config.greeting_override
    elif agent is not None:
        greeting = agent.greeting
    else:
        greeting = ""

    # ----- 2. LLM プロバイダ解決 -----
    llm = None
    if agent is not None:
        llm = await ctx.resolve_provider(agent.llm_provider_id)

    # LLM または primitives が利用不可 → graceful skip（Task 9 で接続される）
    if llm is None or ctx.primitives is None:
        if greeting and ctx.primitives is not None:
            await ctx.primitives.say(ctx.render(greeting))
        if llm is None:
            logger.warning(
                "ai_conversation: LLM プロバイダを解決できません。"
                "挨拶のみ再生して終了します（Task 9 実装後に完全動作）"
            )
        return None

    # ----- 3. 挨拶再生 -----
    if greeting:
        await ctx.primitives.say(ctx.render(greeting))

    # ----- 4. ターンループ -----
    history: list[ChatMessage] = []
    empty_count = 0  # 連続空発話カウンタ
    max_history = agent.max_history if agent is not None else 0

    for _ in range(config.max_turns):
        # ユーザ発話取得
        user_text = await ctx.primitives.listen()

        if not user_text:
            empty_count += 1
            if empty_count >= 2:
                # 2 回連続空発話 → ループ終了（無限無音ループ回避）
                logger.info("ai_conversation: 2 回連続の空発話を検出。ループを終了します")
                break
            # 1 回目の空発話は許容して次のターンへ
            continue
        else:
            empty_count = 0

        history.append(ChatMessage("user", user_text))

        # LLM へメッセージを送信
        messages = [ChatMessage("system", system_prompt), *history]
        assistant_text = await _collect_llm_response(llm, messages)

        if _END_MARKER in assistant_text:
            # 終話マーカーが含まれている → マーカーを除去して残りを再生し、hangup
            clean_text = assistant_text.replace(_END_MARKER, "").strip()
            if clean_text:
                await ctx.primitives.say(clean_text)
            await ctx.hangup()
            break

        # 通常応答: 再生して履歴に追加
        await ctx.primitives.say(assistant_text)
        history.append(ChatMessage("assistant", assistant_text))

        # 履歴トリミング（agent.max_history が 0 より大きい場合のみ）
        if max_history > 0 and len(history) > max_history:
            history = history[-max_history:]

    # ----- 5. 変数抽出 -----
    if config.extract_variables and llm is not None:
        await _extract_variables(config, history, llm, ctx)

    return None


async def _extract_variables(
    config: object,
    history: list[ChatMessage],
    llm: object,
    ctx: ChannelContext,
) -> None:
    """会話履歴から変数を抽出し、ctx に格納する（ai_conversation 内部ヘルパ）。

    extraction_mode == "direct":
        LLM を使わず、最後のユーザ発話をそのまま先頭の抽出変数に格納する。
        単一入力フィールドの直接保存ユースケースに特化（LLM コスト削減）。
        複数変数が指定されていても 1 番目のみ設定される（他は変更しない）。

    extraction_mode == "auto":
        会話履歴全体を LLM に提示し、変数→説明マップに従って
        minified JSON で回答させる。JSON パースに失敗した場合は各変数を "" に設定する。
    """
    extract_vars: dict[str, str] = config.extract_variables  # type: ignore[attr-defined]
    mode: str = config.extraction_mode  # type: ignore[attr-defined]

    if mode == "direct":
        # 最後のユーザ発話を先頭変数に格納
        last_user_text = ""
        for msg in reversed(history):
            if msg.role == "user":
                last_user_text = msg.content
                break
        first_var = next(iter(extract_vars))
        ctx.set_var(first_var, last_user_text)
        return

    # mode == "auto": LLM による抽出
    var_descriptions = "\n".join(f'"{name}": {desc}' for name, desc in extract_vars.items())
    transcript = "\n".join(f"{msg.role}: {msg.content}" for msg in history)
    extract_system = (
        "以下の会話から必要な情報を抽出してください。\n"
        "次のキーを持つ minified JSON オブジェクトのみで回答してください（他の文字を含めない）。\n\n"
        f"抽出する変数（キー: 説明）:\n{var_descriptions}"
    )
    extract_messages = [
        ChatMessage("system", extract_system),
        ChatMessage("user", transcript),
    ]

    try:
        raw = await _collect_llm_response(llm, extract_messages)
        # JSON 部分だけ取り出す（前後の余計なテキストを除去）
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError("JSON オブジェクトが見つかりません")
        parsed = json.loads(raw[start:end])
        for var_name in extract_vars:
            value = parsed.get(var_name, "")
            ctx.set_var(var_name, str(value) if value is not None else "")
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "ai_conversation: 変数抽出の JSON パースに失敗しました。全変数を空に設定します: %s",
            exc,
        )
        for var_name in extract_vars:
            ctx.set_var(var_name, "")
