# Phase 4a Task 4 レポート: converse オーケストレーション（一時エージェント + media 再利用 + transcript/summary）

- 日付: 2026-07-07 / ブランチ: `feat/phase4a-mcp`
- ステータス: **完了**（`uv run pytest -q` = 303 passed / 288 既存 + 15 新規、`uv run ruff check .` = All checks passed）
- 配置: `mcp_server/`（Task 1/3 の確定に従う）。TDD（superpowers）で RED→GREEN。実装は自分自身（再委譲なし）、pytest 多重起動なし。

---

## 追加/変更ファイル

- `src/millicall/mcp_server/ephemeral.py` — 新規。`EphemeralAgentSpec`（DB 非保存の一時エージェント。ConversationSession が読む属性を duck-type で満たす）+ `EphemeralAgentStore`（call_uuid → spec + 解決済み provider + transcript バッファ）。
- `src/millicall/media/service.py` — `HangupRegistry`（AnswerRegistry 同型、CHANNEL_HANGUP_COMPLETE 完了 Future）+ `build_conversation_session_from_spec()`（DB `AiAgent.get` を通さず spec + 注入 provider から ConversationSession を組む。on_turn は transcript 収集 + call_messages 永続化を並行）を追加。
- `src/millicall/media/audio_fork.py` — `MediaEventRouter` に `hangup_registry` を任意注入し CHANNEL_HANGUP_COMPLETE で `resolve`（従来の registry.pop も維持）。`resolve_ws_agent()`（`?agent=` を数値 id / 非数値マーカーへ分岐）+ `_build_ephemeral_session()` を追加し、`audio_fork_ws` が `?agent=ephemeral` のとき EphemeralAgentStore を call_uuid で引く。
- `src/millicall/mcp_server/outbound.py` — `build_converse_system_prompt()`（旧 verbatim を `[DONE]`→`[END_CALL]` 置換、purpose/key_points/your_name 差し込み）、`build_summarizer(llm)`（stream_chat 連結で 1〜2 文要約）、`ConverseResult`、`OutboundCallService.converse()` を追加。
- `src/millicall/main.py` — lifespan で `app.state.hangup_registry` / `app.state.ephemeral_store` を生成し、`MediaEventRouter` へ `hangup_registry` を注入。
- `tests/test_mcp_task4.py` — 新規 15 テスト（fake ESL/LLM/TTS/STT・fake CallControl・in-memory SQLite・injectable timeout。実 FS / 実時間 sleep なし）。

---

## converse の内部フロー

1. `_resolve_target`（dial と同経路）で番号→dest 解決（外線 `sofia/gateway/<trunk>/<番号>` / 内線 `user/<ext>@<domain>`、186/184 は呼び出し側前置＝裁定#6）。ValueError は §6 エラー JSON へ。
2. `build_converse_system_prompt` で system_prompt を合成し `EphemeralAgentSpec` を生成。既定 MCP エージェント（`ResolvedProviders`＝裁定#1、`resolve_default_providers` 由来）の provider 構成を流用し、transcript バッファ付きで `EphemeralAgentStore.register(call_uuid, ...)`。
3. `bgapi "originate {origination_uuid=<uuid>,millicall_ai_agent=ephemeral,verbose_events=true[,origination_caller_id_number=<cid>]}<dest> &park"` で発信 → **着信 AI と同一の audio_stream 自動起動経路に合流**（`MediaEventRouter._maybe_start_audio_stream` が `variable_millicall_ai_agent=ephemeral` を検知して `uuid_audio_stream ... ?agent=ephemeral` 発行）。
4. `AnswerRegistry.wait(answer_timeout)`。未応答は §6 エラー（`"30秒以内に応答がありませんでした"`, transcript=[]）。
5. 会話は `audio_fork_ws`（`?agent=ephemeral`）→ `_build_ephemeral_session` → `build_conversation_session_from_spec` が駆動。ConversationSession は既存どおり greet / on_utterance ループ / `[END_CALL]` 検知→stop_playback+hangup。on_turn が store エントリの transcript に (role,text,latency) を積み call_messages に永続化。
6. `HangupRegistry.wait(timeout)` で終話を待つ（timeout = `max_conversation_seconds` 明示 or `max_turns*30s` 概算）。取り逃し時は `uuid_kill <uuid>` フォールバック終話。
7. transcript を `assistant→ai / user→human / system→system` へマップ、turn 連番付与、`summarizer` で要約。§6 `ConverseResult` を返す。finally で answer/hangup registry・ephemeral_store を pop。

**テスト差し替え点**: `run_conversation` を注入すると 5. の WS 駆動をフェイクできる（transcript を積み、hangup_registry を解決）。実運用では None（WS ハンドラが担い、converse は hangup を待つだけ）。

---

## Task 6 が呼ぶ IF

```python
from millicall.mcp_server.outbound import (
    OutboundCallService, ConverseResult, build_summarizer, resolve_default_providers,
)

svc = OutboundCallService(
    esl=app.state.esl_command,
    answer_registry=app.state.answer_registry,
    sip_domain=settings.sip_domain,
    fetch_enabled_trunks=<enabled trunks を name 昇順で返す>,
    hangup_registry=app.state.hangup_registry,
    ephemeral_store=app.state.ephemeral_store,
    # run_conversation は渡さない（実運用は WS ハンドラが会話駆動）
)
providers = await resolve_default_providers(sessionmaker, secrets, settings.mcp_default_agent_id)
result: ConverseResult = await svc.converse(
    phone_number, purpose, key_points="", your_name="", max_turns=10,
    caller_id="", trunk="",
    providers=providers,                      # 既定エージェントの provider 構成（一時注入）
    summarizer=build_summarizer(providers.llm),
    answer_timeout=30.0,
)
# §6 JSON へ: {"status": result.status, "phone_number", "purpose", "turns", "summary",
#              "transcript": result.transcript}  ／ error 時 {"error": result.error, "transcript": []}
```
- `ConverseResult`: `status`("completed"|"error"), `phone_number`, `purpose`, `turns`(human 発話数), `summary`, `transcript`(list[{turn,speaker,text}]), `error`。
- **voice 引数は Task 6 のツール層で受理して無視**（裁定#2）。本サービスは voice を持たない。

---

## plan からの逸脱・設計判断

- **transcript の所在**: plan は「on_turn で converse オーケストレータが蓄積」。実際は WS ハンドラ（別コルーチン）が ConversationSession を駆動するため、transcript は `EphemeralAgentStore` のエントリに持たせ、converse は終話後にそこから読む。converse オーケストレータと会話駆動が別タスクに分かれる v2 の実態に合わせた結線（plan の「call_messages 永続化と並行」は維持）。
- **HangupRegistry** は plan 記載どおり `media/service.py` に AnswerRegistry と同型で新設。`MediaEventRouter.CHANNEL_HANGUP_COMPLETE` で resolve。
- **`build_conversation_session_from_spec` に `call_control` 注入口**を追加（既定は `EslCallControl` を内製）。実 CallControl は PLAYBACK_STOP イベントで再生完了するためユニットで会話を駆動できず、テストが fake CallControl を挿せるようにした DI。実運用パスは従来どおり EslCallControl。
- **summary は LLM の stream_chat 連結**（LLM base は stream_chat のみ）。`build_summarizer(llm)` を Task 6 が converse に渡す。transcript 空なら summary は空文字。
- **上限時間** = `max_conversation_seconds` 明示 or `max_turns*30s` 概算。plan の「max_turns 相応の上限時間 + フォールバック終話」を実装（uuid_kill）。
- **EphemeralAgentStore.get** は `put(spec)`（spec のみ）経由なら spec を、`register(spec, llm/tts/stt)` 経由なら内部エントリを返す（テスト互換 + 実運用の provider 同梱）。WS ハンドラは `get_entry()` を使う。
- 裁定準拠: #1 既定エージェント provider 流用、#2 voice 無視（ツール層）、#6 186/184 前置。
- **これらは下回りサービス。`@mcp.tool()` 登録・§6 JSON 文字列整形（json.dumps ensure_ascii=False）は Task 6。**
