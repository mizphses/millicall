"""WorkflowRunner のユニットテスト (Phase 4b Task 9).

すべてフェイク/インメモリオブジェクトを使用 — ソケット・ネットワーク・ESL は使わない。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from millicall.media.dtmf import DtmfCollector
from millicall.media.service import SessionRegistry
from millicall.models import AiAgent, Provider, Workflow
from millicall.workflows.runner import WorkflowRunner

# --------------------------------------------------------------------------- #
# フェイクインフラ
# --------------------------------------------------------------------------- #


class _FakeEsl:
    def __init__(self):
        self.commands: list[str] = []

    async def bgapi(self, cmd: str) -> str:
        self.commands.append(cmd)
        return "+OK"


class _FakeSession:
    def __init__(self, rows: dict):
        self._rows = rows

    async def get(self, model, pk):
        return self._rows.get((model, pk))

    async def scalar(self, stmt):
        # プロバイダが存在しない場合は None を返す
        return None


class _FakeSessionCtx:
    def __init__(self, session: _FakeSession):
        self._session = session

    async def __aenter__(self) -> _FakeSession:
        return self._session

    async def __aexit__(self, *a) -> None:
        pass


def _fake_sm(rows: dict):
    return lambda: _FakeSessionCtx(_FakeSession(rows))


class _FakeSecrets:
    master_key: str = "a" * 32


class _FakeSettings:
    tts_cache_dir = Path("/tmp/test-tts")
    smtp_host = ""
    smtp_port = 587
    smtp_username = ""
    smtp_password = ""
    smtp_from = ""
    smtp_starttls = False
    smtp_timeout = 15


def _make_runner(rows: dict, esl: _FakeEsl | None = None) -> WorkflowRunner:
    if esl is None:
        esl = _FakeEsl()
    registry = SessionRegistry()
    collector = DtmfCollector()
    runner = WorkflowRunner(
        sessionmaker=_fake_sm(rows),
        secrets=_FakeSecrets(),
        esl=esl,
        esl_lock=asyncio.Lock(),
        esl_reconnect=None,
        session_registry=registry,
        settings=_FakeSettings(),
        dtmf_collector=collector,
    )
    # テストから registry / collector を参照できるよう返す
    runner._session_registry = registry
    runner._dtmf_collector = collector
    return runner


def _simple_workflow(*, enabled: bool = True, definition: dict | None = None):
    """有効な start のみのワークフローを返すフェイク Workflow (MagicMock)。"""
    if definition is None:
        # start ノードのみ → 正常終了（out エッジなし）
        definition = {"nodes": [{"id": "s1", "type": "start"}], "edges": []}
    wf = MagicMock(spec=Workflow)
    wf.id = 1
    wf.enabled = enabled
    wf.default_tts_provider_id = None
    wf.definition_json = json.dumps(definition)
    return wf


# --------------------------------------------------------------------------- #
# テスト 1: ハッピーパス
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_happy_path():
    wf = _simple_workflow()
    esl = _FakeEsl()
    rows = {(Workflow, 1): wf}
    runner = _make_runner(rows, esl)
    uuid = "test-uuid-1234"

    await runner.start(uuid, 1)

    # ハングアップが発行されている
    assert any(f"uuid_kill {uuid}" in cmd for cmd in esl.commands)
    # レジストリ後片付け済み
    assert runner._session_registry.get(uuid) is None
    # DtmfCollector 後片付け済み
    assert runner._dtmf_collector._queues.get(uuid) is None


# --------------------------------------------------------------------------- #
# テスト 2: ワークフロー未存在
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_missing_workflow():
    esl = _FakeEsl()
    rows: dict = {}  # ワークフローが DB にない
    runner = _make_runner(rows, esl)
    uuid = "test-uuid-missing"

    await runner.start(uuid, 99)

    # ハングアップが発行されている（_safe_hangup 経由）
    assert any(f"uuid_kill {uuid}" in cmd for cmd in esl.commands)
    # レジストリに登録されていない
    assert runner._session_registry.get(uuid) is None


# --------------------------------------------------------------------------- #
# テスト 3: ワークフロー無効
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_disabled_workflow():
    wf = _simple_workflow(enabled=False)
    esl = _FakeEsl()
    rows = {(Workflow, 1): wf}
    runner = _make_runner(rows, esl)
    uuid = "test-uuid-disabled"

    await runner.start(uuid, 1)

    assert any(f"uuid_kill {uuid}" in cmd for cmd in esl.commands)
    assert runner._session_registry.get(uuid) is None


# --------------------------------------------------------------------------- #
# テスト 4: ハンドラ例外でも finally が動作する
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_handler_exception():
    # ノードなし定義 → WorkflowExecutor が "must have exactly one start node" を raise
    wf = _simple_workflow(definition={"nodes": [], "edges": []})
    esl = _FakeEsl()
    rows = {(Workflow, 1): wf}
    runner = _make_runner(rows, esl)
    uuid = "test-uuid-exception"

    # 例外が外に伝播しないことを確認
    await runner.start(uuid, 1)  # should not raise

    # finally ブロックでハングアップ + 後片付けが行われている
    assert any(f"uuid_kill {uuid}" in cmd for cmd in esl.commands)
    assert runner._session_registry.get(uuid) is None
    assert runner._dtmf_collector._queues.get(uuid) is None


# --------------------------------------------------------------------------- #
# テスト 4b: ChannelContext 構築失敗でも finally が動作する (ctx=None パス)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_ctx_construction_failure():
    """ChannelContext 構築で例外が発生しても hangup・後片付けが行われる。

    register() 後 try ブロック内で ChannelContext(...) が raise した場合、
    ctx が None のまま finally へ到達する。このパスでは call_control 経由でハングアップし、
    session_registry / dtmf_collector の後片付けも行われなければならない。
    """
    from unittest.mock import patch

    wf = _simple_workflow()
    esl = _FakeEsl()
    rows = {(Workflow, 1): wf}
    runner = _make_runner(rows, esl)
    uuid = "test-uuid-ctx-fail"

    # SmtpEmailSender.from_settings を raise させて ChannelContext 構築を失敗させる
    with patch(
        "millicall.workflows.runner.SmtpEmailSender.from_settings",
        side_effect=RuntimeError("smtp init failure"),
    ):
        await runner.start(uuid, 1)  # 外に伝播しないこと

    # ctx が None のパスでも hangup が発行されている
    assert any(f"uuid_kill {uuid}" in cmd for cmd in esl.commands)
    # レジストリ後片付け済み
    assert runner._session_registry.get(uuid) is None
    # DtmfCollector 後片付け済み
    assert runner._dtmf_collector._queues.get(uuid) is None


# --------------------------------------------------------------------------- #
# テスト 5: provider_resolver が TTS を構築する
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_provider_resolver_loads_tts():
    """provider_resolver が Provider ORM 行から VoicevoxTTS を構築できる。"""
    # フェイク Provider (TTS / voicevox)
    provider = MagicMock(spec=Provider)
    provider.id = 10
    provider.type = "tts"
    provider.kind = "voicevox"
    provider.config_json = '{"engine_url": "http://localhost:50021"}'
    provider.api_key_encrypted = None
    provider.enabled = True

    class _SessionWithProvider(_FakeSession):
        async def get(self, model, pk):
            if model is Provider and pk == 10:
                return provider
            return await super().get(model, pk)

    class _SmWithProvider:
        def __call__(self):
            return _FakeSessionCtx(_SessionWithProvider({}))

    from millicall.crypto import SecretBox

    box = SecretBox("a" * 32)

    runner = WorkflowRunner(
        sessionmaker=_SmWithProvider(),
        secrets=_FakeSecrets(),
        esl=_FakeEsl(),
        esl_lock=asyncio.Lock(),
        esl_reconnect=None,
        session_registry=SessionRegistry(),
        settings=_FakeSettings(),
        dtmf_collector=DtmfCollector(),
    )

    resolver = runner._make_provider_resolver(box)
    result = await resolver(10)

    assert result is not None  # VoicevoxTTS インスタンスが返る


# --------------------------------------------------------------------------- #
# テスト 6: agent_resolver が AiAgent を返す
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_workflow_runner_agent_resolver():
    agent = MagicMock(spec=AiAgent)
    agent.id = 5
    agent.name = "test-agent"

    rows = {(AiAgent, 5): agent}
    runner = _make_runner(rows)

    resolver = runner._make_agent_resolver()
    result = await resolver(5)

    assert result is agent
