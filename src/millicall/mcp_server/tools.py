"""MCP ツール層（契約 §1–§15）— Task 6。

Task 2–5 の下回りサービスを `@mcp.tool()` として登録し、`guide://outbound-calling`
リソースを公開する。全ツールは契約どおり `json.dumps(..., ensure_ascii=False)` した
**文字列**を返す（構造化コンテンツではない）。docstring・引数名・デフォルト値・返り値
JSON のキーは旧サーバー互換（verbatim）。

依存は `get_app_state(mcp)` 経由で `app.state`（DI コンテナ）から取得する。
`voice` 引数は裁定#2 により受理して無視する（互換維持・TODO(phase4b)）。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from sqlalchemy import select

from millicall.mcp_server.directory import Directory
from millicall.mcp_server.guide import OUTBOUND_CALLING_GUIDE
from millicall.mcp_server.live_calls import LiveCallView
from millicall.mcp_server.outbound import (
    DialTimeout,
    OutboundCallService,
    build_summarizer,
    resolve_default_providers,
)
from millicall.mcp_server.primitives import CallPrimitives
from millicall.mcp_server.server import get_app_state
from millicall.media.call_control import EslCallControl
from millicall.models import Trunk
from millicall.telephony.esl import ESLError

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

# 発信者番号通知の既定ボイス（互換のためシグネチャに残すが未使用＝裁定#2）。
_DEFAULT_VOICE = "ja-JP-Chirp3-HD-Aoede"


def _dumps(obj: object) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _err(message: str) -> str:
    return _dumps({"error": message})


async def _fetch_enabled_trunks(sessionmaker) -> list:
    """enabled なトランクを name 昇順で返す（外線トランク自動選択の決定論化）。"""
    async with sessionmaker() as session:
        rows = await session.scalars(
            select(Trunk).where(Trunk.enabled.is_(True)).order_by(Trunk.name)
        )
        return list(rows)


def _build_outbound(state) -> OutboundCallService:
    """app.state から OutboundCallService（dial/converse 共用）を組む。"""
    sessionmaker = state.sessionmaker
    return OutboundCallService(
        esl=state.esl_command,
        answer_registry=state.answer_registry,
        sip_domain=state.settings.sip_domain,
        fetch_enabled_trunks=lambda: _fetch_enabled_trunks(sessionmaker),
        hangup_registry=state.hangup_registry,
        ephemeral_store=state.ephemeral_store,
        lock=state.esl_command_lock,
        reconnect=state.esl_reconnect,
    )


def register_tools(mcp: FastMCP) -> None:
    """契約 §1–§15 の 15 ツール + guide リソースを FastMCP に登録する。

    `build_mcp` から呼ばれる。ダミー `ping` は骨格テスト互換のため残置（Task 1）。
    """

    # -- §6 converse（中核） --------------------------------------------------
    @mcp.tool()
    async def converse(
        phone_number: str,
        purpose: str,
        key_points: str = "",
        your_name: str = "",
        max_turns: int = 10,
        caller_id: str = "",
        trunk: str = "",
        voice: str = _DEFAULT_VOICE,
    ) -> str:
        """電話を発信し、目的に沿って自律的に会話を行います。会話の目的と要点を指定するだけで、発信→会話→切電まで自動で行います。

        Args:
            phone_number: 発信先番号（外線は0始まり、内線は内線番号）。186/184 は先頭に前置。
            purpose: 会話の目的を具体的に書く（例: "ラーメンを1杯注文する"）。
            key_points: 伝えるべき情報を改行区切りで書く。
            your_name: 名乗る名前（任意）。
            max_turns: 会話の最大ターン数。
            caller_id: 発信者番号（省略時トランク既定）。
            trunk: 使用するトランク（省略時自動選択）。
            voice: 現行版では未使用（互換のため受理して無視）。TODO(phase4b)。
        """
        state = get_app_state(mcp)
        try:
            providers = await resolve_default_providers(
                state.sessionmaker, state.secrets, state.settings.mcp_default_agent_id
            )
        except ValueError as exc:
            return _dumps({"error": f"発信エラー: {exc}", "transcript": []})

        svc = _build_outbound(state)
        result = await svc.converse(
            phone_number,
            purpose,
            key_points=key_points,
            your_name=your_name,
            max_turns=max_turns,
            caller_id=caller_id,
            trunk=trunk,
            providers=providers,
            summarizer=build_summarizer(providers.llm),
        )
        if result.status == "error":
            return _dumps({"error": result.error, "transcript": []})
        return _dumps(
            {
                "status": result.status,
                "phone_number": result.phone_number,
                "purpose": result.purpose,
                "turns": result.turns,
                "summary": result.summary,
                "transcript": result.transcript,
            }
        )

    # -- §1 dial --------------------------------------------------------------
    @mcp.tool()
    async def dial(phone_number: str, caller_id: str = "", trunk: str = "") -> str:
        """電話を発信し、相手が応答するまで待ちます。応答したらchannel_idを返します。

        Args:
            phone_number: 発信先番号（外線は0始まり、内線は内線番号 例 "800"）。
            caller_id: 発信者番号（省略時トランク既定）。
            trunk: 使用するトランク（省略時自動選択）。
        """
        state = get_app_state(mcp)
        svc = _build_outbound(state)
        try:
            result = await svc.dial(phone_number, caller_id, trunk)
        except DialTimeout as exc:
            return _dumps(
                {"error": "30秒以内に応答がありませんでした", "channel_id": exc.call_uuid}
            )
        except ValueError as exc:
            return _err(str(exc))
        except Exception as exc:  # noqa: BLE001 — 発信失敗を互換 JSON に集約
            return _err(f"発信エラー: {exc}")
        return _dumps(
            {
                "channel_id": result.call_uuid,
                "state": result.state,
                "message": (
                    f"{phone_number} が応答しました。say_and_listenで会話を始めてください。"
                ),
            }
        )

    # -- 手動音声プリミティブ（say/listen/say_and_listen） --------------------
    async def _build_primitives(state, channel_id: str) -> CallPrimitives:
        """default エージェントの tts/stt で CallPrimitives を組む（parked channel 上）。"""
        providers = await resolve_default_providers(
            state.sessionmaker, state.secrets, state.settings.mcp_default_agent_id
        )
        call_control = EslCallControl(
            state.esl_command,
            channel_id,
            lock=state.esl_command_lock,
            reconnect=state.esl_reconnect,
        )
        return CallPrimitives(
            esl=state.esl_command,
            call_uuid=channel_id,
            call_control=call_control,
            tts=providers.tts,
            stt=providers.stt,
            tts_dir=state.settings.tts_cache_dir,
            lock=state.esl_command_lock,
            reconnect=state.esl_reconnect,
        )

    # -- §2 say_and_listen ----------------------------------------------------
    @mcp.tool()
    async def say_and_listen(
        channel_id: str,
        text: str,
        max_listen_seconds: int = 15,
        voice: str = _DEFAULT_VOICE,
    ) -> str:
        """相手にテキストを話しかけ、その後相手の返答を聞き取ります。会話の1ターン（こちらが話す→相手が話す）を1回のツール呼び出しで行います。

        Args:
            channel_id: dial が返した channel_id。
            text: 相手に話す内容。
            max_listen_seconds: 返答を待つ最大秒数。
            voice: 現行版では未使用（互換のため受理して無視）。TODO(phase4b)。
        """
        state = get_app_state(mcp)
        try:
            prim = await _build_primitives(state, channel_id)
            _said, heard = await prim.say_and_listen(text, max_listen_seconds)
        except Exception as exc:  # noqa: BLE001
            return _err(f"会話エラー: {exc}")
        message = heard if heard else "（相手の発話が検出されませんでした）"
        return _dumps({"you_said": text[:100], "they_said": heard, "message": message})

    # -- §3 say ---------------------------------------------------------------
    @mcp.tool()
    async def say(channel_id: str, text: str, voice: str = _DEFAULT_VOICE) -> str:
        """相手にテキストを話します（返答は聞きません）。通話の最後の挨拶やお礼など、返答を待たない場面で使います。

        Args:
            channel_id: dial が返した channel_id。
            text: 相手に話す内容。
            voice: 現行版では未使用（互換のため受理して無視）。TODO(phase4b)。
        """
        state = get_app_state(mcp)
        try:
            prim = await _build_primitives(state, channel_id)
            duration = await prim.say(text)
        except Exception as exc:  # noqa: BLE001
            return _err(f"TTS再生に失敗: {exc}")
        return _dumps(
            {
                "status": "ok",
                "message": f"「{text[:50]}」を再生しました",
                "duration_sec": duration,
            }
        )

    # -- §4 listen ------------------------------------------------------------
    @mcp.tool()
    async def listen(channel_id: str, max_seconds: int = 15) -> str:
        """相手の発話だけを聞き取ります（こちらは何も話しません）。

        Args:
            channel_id: dial が返した channel_id。
            max_seconds: 聞き取る最大秒数。
        """
        state = get_app_state(mcp)
        try:
            prim = await _build_primitives(state, channel_id)
            heard = await prim.listen(max_seconds)
        except Exception as exc:  # noqa: BLE001
            return _err(f"録音/STTに失敗: {exc}")
        message = heard if heard else "（相手の発話が検出されませんでした）"
        return _dumps({"text": heard, "message": message})

    # -- §5 hangup ------------------------------------------------------------
    @mcp.tool()
    async def hangup(channel_id: str) -> str:
        """通話を終了します。

        Args:
            channel_id: dial が返した channel_id。
        """
        state = get_app_state(mcp)
        entry = state.session_registry.get(channel_id)
        if entry is not None:
            # 管理中の AI セッション: 登録済み CallControl で終話。
            _, call_control = entry
            try:
                await call_control.hangup()
            except Exception as exc:  # noqa: BLE001
                return _err(f"通話終了に失敗: {exc}")
            return _dumps({"status": "ok", "message": "通話を終了しました"})

        # 管理外のチャネル（dial 済み parked ch 等）は uuid_kill を試み、
        # 無効 uuid / 接続断は「既に終了」に冪等化する（裁定#3: show channels 不使用）。
        try:
            cc = EslCallControl(
                state.esl_command,
                channel_id,
                lock=state.esl_command_lock,
                reconnect=state.esl_reconnect,
            )
            await cc.hangup()
        except (ValueError, ESLError):
            return _dumps({"status": "ok", "message": "通話は既に終了しています"})
        except Exception as exc:  # noqa: BLE001
            return _err(f"通話終了に失敗: {exc}")
        return _dumps({"status": "ok", "message": "通話を終了しました"})

    # -- §7 send_dtmf ---------------------------------------------------------
    @mcp.tool()
    async def send_dtmf(channel_id: str, digits: str) -> str:
        """DTMFトーンを送信します。

        Args:
            channel_id: dial が返した channel_id。
            digits: 送信する DTMF 桁（0-9*#ABCDw）。
        """
        state = get_app_state(mcp)
        try:
            cc = EslCallControl(
                state.esl_command,
                channel_id,
                lock=state.esl_command_lock,
                reconnect=state.esl_reconnect,
            )
            await cc.send_dtmf(digits)
        except Exception as exc:  # noqa: BLE001
            return _err(f"DTMF送信に失敗: {exc}")
        return _dumps({"status": "ok", "message": f"DTMF '{digits}' を送信しました"})

    # -- §8 transfer ----------------------------------------------------------
    @mcp.tool()
    async def transfer(channel_id: str, destination: str) -> str:
        """通話を内線に転送します。

        Args:
            channel_id: dial が返した channel_id。
            destination: 転送先内線番号。
        """
        state = get_app_state(mcp)
        try:
            cc = EslCallControl(
                state.esl_command,
                channel_id,
                lock=state.esl_command_lock,
                reconnect=state.esl_reconnect,
            )
            await cc.transfer(destination)
        except Exception as exc:  # noqa: BLE001
            return _err(f"転送に失敗: {exc}")
        return _dumps({"status": "ok", "message": f"内線 {destination} に転送しました"})

    # -- §9 get_call_status ---------------------------------------------------
    @mcp.tool()
    async def get_call_status(channel_id: str) -> str:
        """通話の現在の状態を取得します。

        Args:
            channel_id: dial が返した channel_id。
        """
        state = get_app_state(mcp)
        try:
            view = LiveCallView(state.session_registry, state.sessionmaker)
            result = await view.get_status(channel_id)
        except Exception as exc:  # noqa: BLE001
            return _err(f"ステータス取得に失敗: {exc}")
        if result is None:
            return _err("チャネルが見つかりません（通話が終了している可能性があります）")
        return _dumps(result)

    # -- §10 list_active_calls ------------------------------------------------
    @mcp.tool()
    async def list_active_calls() -> str:
        """現在アクティブな通話の一覧を取得します。"""
        state = get_app_state(mcp)
        try:
            view = LiveCallView(state.session_registry, state.sessionmaker)
            calls = await view.list_active()
        except Exception as exc:  # noqa: BLE001
            return _err(f"通話一覧の取得に失敗: {exc}")
        return _dumps({"count": len(calls), "calls": calls})

    # -- §11 list_contacts ----------------------------------------------------
    @mcp.tool()
    async def list_contacts(query: str = "") -> str:
        """電話帳を検索します。queryが空の場合は全件返します。

        Args:
            query: 検索キーワード（名前、電話番号、会社名で部分一致検索）。
        """
        state = get_app_state(mcp)
        directory = Directory(state.sessionmaker)
        return _dumps(await directory.list_contacts(query))

    # -- §12 add_contact ------------------------------------------------------
    @mcp.tool()
    async def add_contact(
        name: str,
        phone_number: str,
        company: str = "",
        department: str = "",
        notes: str = "",
    ) -> str:
        """電話帳に連絡先を追加します。

        Args:
            name: 名前。
            phone_number: 電話番号。
            company: 会社名。
            department: 部署名。
            notes: メモ。
        """
        state = get_app_state(mcp)
        directory = Directory(state.sessionmaker)
        try:
            return _dumps(
                await directory.add_contact(
                    name=name,
                    phone_number=phone_number,
                    company=company,
                    department=department,
                    notes=notes,
                )
            )
        except Exception as exc:  # noqa: BLE001
            return _err(f"連絡先の追加に失敗: {exc}")

    # -- §13 delete_contact ---------------------------------------------------
    @mcp.tool()
    async def delete_contact(contact_id: int) -> str:
        """電話帳から連絡先を削除します。

        Args:
            contact_id: 削除する連絡先のID。
        """
        state = get_app_state(mcp)
        directory = Directory(state.sessionmaker)
        try:
            return _dumps(await directory.delete_contact(contact_id))
        except Exception as exc:  # noqa: BLE001
            return _err(f"連絡先の削除に失敗: {exc}")

    # -- §14 list_extensions --------------------------------------------------
    @mcp.tool()
    async def list_extensions() -> str:
        """内線番号の一覧を取得します。"""
        state = get_app_state(mcp)
        directory = Directory(state.sessionmaker)
        return _dumps(await directory.list_extensions())

    # -- §15 list_trunks ------------------------------------------------------
    @mcp.tool()
    async def list_trunks() -> str:
        """外線トランクの一覧と発信プレフィックスを取得します。"""
        state = get_app_state(mcp)
        directory = Directory(state.sessionmaker)
        return _dumps(await directory.list_trunks())

    # -- guide リソース -------------------------------------------------------
    @mcp.resource("guide://outbound-calling")
    async def outbound_calling_guide() -> str:
        """外線発信のガイド"""
        return OUTBOUND_CALLING_GUIDE
