"""発信プレフィクスによる回線選択の共通処理・transfer・MCP 発信のテスト（issue #139）。"""

from dataclasses import dataclass

import pytest

from millicall.media.call_control import EslCallControl
from millicall.media.service import AnswerRegistry
from millicall.telephony import outbound_routes
from millicall.telephony.outbound_routes import (
    dial_prefix_format_error,
    find_prefixed_trunk,
    is_external_destination,
    select_default_outbound_trunk,
    split_dial_prefix,
)


@dataclass
class _T:
    name: str
    id: int
    caller_id: str = ""
    dial_prefix: str = ""
    outbound_enabled: bool = True
    trunk_type: str = "hgw"
    enabled: bool = True


_TRUNKS = [
    _T("Flets01", 1, "0445895782"),
    _T("Flets02", 2, "0445895784", dial_prefix="92"),
    _T("brastel", 3, "05068650165", dial_prefix="*3", trunk_type="sip"),
]


@pytest.mark.parametrize(
    ("prefix", "ok"),
    [("9", True), ("92", True), ("*3", True), ("#9", True), ("12345678", True), ("18", True),
     ("1234", True), ("", False), ("0", False), ("09", False), ("184", False), ("186", False),
     ("123", False), ("1840", False), ("18612", False), ("9a", False), ("123456789", False)],
)
def test_dial_prefix_format(prefix, ok):
    assert (dial_prefix_format_error(prefix) is None) is ok


def test_split_and_external_detection():
    prefixes = ["92", "*3"]
    assert split_dial_prefix("9207089372750", prefixes) == ("92", "07089372750")
    assert split_dial_prefix("9218607089372750", prefixes) == ("92", "18607089372750")
    assert split_dial_prefix("*305012345678", prefixes) == ("*3", "05012345678")
    assert split_dial_prefix("921001", prefixes) is None
    assert is_external_destination("0312345678", [])
    assert is_external_destination("1860312345678", [])
    assert is_external_destination("920312345678", prefixes)
    assert not is_external_destination("920312345678", [])
    assert not is_external_destination("1001", prefixes)


def test_default_trunk_selection():
    assert select_default_outbound_trunk(_TRUNKS, None).name == "Flets01"
    assert select_default_outbound_trunk(_TRUNKS, 3).name == "brastel"
    # 存在しない id は名前順先頭へフォールバック
    assert select_default_outbound_trunk(_TRUNKS, 99).name == "Flets01"
    trunks = [_T("Flets01", 1, outbound_enabled=False), _T("Flets02", 2)]
    assert select_default_outbound_trunk(trunks, 1).name == "Flets02"
    assert select_default_outbound_trunk([_T("a", 1, outbound_enabled=False)], None) is None


def test_find_prefixed_trunk_ignores_outbound_disabled():
    assert find_prefixed_trunk("920312345678", _TRUNKS)[0].name == "Flets02"
    trunks = [_T("Flets02", 2, dial_prefix="92", outbound_enabled=False)]
    assert find_prefixed_trunk("920312345678", trunks) is None


# --- EslCallControl.transfer ------------------------------------------------------


class _FakeEsl:
    def __init__(self) -> None:
        self.cmds: list[str] = []

    async def bgapi(self, command: str) -> str:
        self.cmds.append(command)
        return "job"


@pytest.fixture
def prefix_loader():
    async def _load() -> list[str]:
        return ["92", "*3"]

    outbound_routes.register_dial_prefix_loader(_load)
    yield
    outbound_routes.register_dial_prefix_loader(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "dest", ["0312345678", "1860312345678", "920312345678", "*305012345678"]
)
async def test_transfer_to_external_sets_domestic(prefix_loader, dest):
    esl = _FakeEsl()
    await EslCallControl(esl, "u1").transfer(dest)
    assert esl.cmds == [
        "uuid_setvar u1 calling_permission domestic",
        f"uuid_transfer u1 {dest} XML default",
    ]


@pytest.mark.asyncio
async def test_transfer_to_extension_does_not_set_permission(prefix_loader):
    esl = _FakeEsl()
    await EslCallControl(esl, "u1").transfer("1001")
    assert esl.cmds == ["uuid_transfer u1 1001 XML default"]


@pytest.mark.asyncio
async def test_transfer_unknown_prefix_is_not_external():
    # ローダ未登録（プレフィクスなし）では 92… は外線扱いしない
    esl = _FakeEsl()
    await EslCallControl(esl, "u1").transfer("950312345678")
    assert esl.cmds == ["uuid_transfer u1 950312345678 XML default"]


@pytest.mark.asyncio
async def test_db_dial_prefix_loader(app):
    from millicall.models import Trunk

    async with app.state.sessionmaker() as session:
        session.add_all([
            Trunk(name="a", display_name="a", host="h", username="u", dial_prefix="92"),
            Trunk(name="b", display_name="b", host="h", username="u", dial_prefix="93",
                  outbound_enabled=False),
            Trunk(name="c", display_name="c", host="h", username="u", dial_prefix="94",
                  enabled=False),
            Trunk(name="d", display_name="d", host="h", username="u"),
        ])
        await session.commit()
    load = outbound_routes.make_db_dial_prefix_loader(app.state.sessionmaker)
    assert await load() == ["92"]


# --- MCP 発信（OutboundCallService._resolve_target_with_vars） ---------------------


def _svc(trunks, default_id=None, allow=None):
    from millicall.mcp_server.outbound import OutboundCallService

    async def _fetch():
        return [t for t in trunks if t.enabled]

    return OutboundCallService(
        esl=_FakeEsl(),
        answer_registry=AnswerRegistry(),
        sip_domain="millicall.local",
        fetch_enabled_trunks=_fetch,
        international_allow_prefixes=allow or [],
        default_outbound_trunk_id=default_id,
    )


@pytest.mark.asyncio
async def test_mcp_prefixed_dial_selects_trunk():
    svc = _svc(_TRUNKS)
    dest, cid, extra = await svc._resolve_target_with_vars("9207089372750", "", "")
    assert dest == "sofia/gateway/Flets02/07089372750"
    assert cid == "0445895784"
    assert extra == ["absolute_codec_string=PCMU"]
    dest, cid, extra = await svc._resolve_target_with_vars("*305012345678", "", "")
    assert dest == "sofia/gateway/brastel/05012345678"
    assert extra == []


@pytest.mark.asyncio
async def test_mcp_unprefixed_uses_default_trunk_setting():
    dest, cid, _ = await _svc(_TRUNKS)._resolve_target_with_vars("0312345678", "", "")
    assert dest == "sofia/gateway/Flets01/0312345678"
    dest, cid, _ = await _svc(_TRUNKS, default_id=3)._resolve_target_with_vars(
        "0312345678", "", ""
    )
    assert dest == "sofia/gateway/brastel/0312345678"
    assert cid == "05068650165"


@pytest.mark.asyncio
async def test_mcp_prefixed_international_is_blocked():
    svc = _svc(_TRUNKS)
    with pytest.raises(ValueError, match="international"):
        await svc._resolve_target_with_vars("92010123456789", "", "")
    # 186 を前置しても国際判定を回避できない
    with pytest.raises(ValueError, match="international"):
        await svc._resolve_target_with_vars("186010123456789", "", "")


@pytest.mark.asyncio
async def test_mcp_explicit_trunk_must_be_outbound_enabled():
    trunks = [_T("a", 1), _T("b", 2, outbound_enabled=False)]
    with pytest.raises(ValueError, match="outbound"):
        await _svc(trunks)._resolve_target_with_vars("0312345678", "", "b")
