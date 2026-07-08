import asyncio
import re

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_change_listener, get_esl_factory, get_session, require_admin
from millicall.models import Trunk
from millicall.telephony.esl import ESLError
from millicall.telephony.hooks import ExtensionChangeListener
from millicall.trunks.schemas import TrunkCreate, TrunkRead, TrunkUpdate

router = APIRouter(prefix="/api/trunks", tags=["trunks"], dependencies=[Depends(require_admin)])

# sofia status gateway の出力から State 行を抜き出す（例: "State   \tREGED"）
_STATE_RE = re.compile(r"^\s*State\s+(\S+)", re.MULTILINE)

# ESL 疎通の最大待ち時間（秒）。listener の esl_timeout と同水準。
_STATUS_TIMEOUT = 5.0


class TrunkStatusResult(BaseModel):
    """GET /api/trunks/{id}/status のレスポンス。

    state は sofia のゲートウェイ状態そのまま:
      REGED(登録済み) / TRYING / FAIL_WAIT / UNREGED / NOREG(register=false) など。
      ゲートウェイ未ロード時は NOT_LOADED、FS へ到達できない場合は UNKNOWN。
    """

    registered: bool
    state: str


@router.post("", response_model=TrunkRead, status_code=status.HTTP_201_CREATED)
async def create_trunk(
    body: TrunkCreate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> TrunkRead:
    existing = await session.scalar(select(Trunk).where(Trunk.name == body.name))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Trunk name already exists"
        )
    trunk = Trunk(
        name=body.name,
        display_name=body.display_name,
        host=body.host,
        username=body.username,
        password=body.password,
        did_number=body.did_number,
        caller_id=body.caller_id,
        enabled=body.enabled,
    )
    session.add(trunk)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Trunk name already exists"
        ) from None
    await session.refresh(trunk)
    # sync_gateway によりゲートウェイが即ロードされ、REGISTER が直ちに試行される
    await listener.notify(session, sync_gateway=trunk.name)
    return TrunkRead.from_orm_trunk(trunk)


@router.get("", response_model=list[TrunkRead])
async def list_trunks(session: AsyncSession = Depends(get_session)) -> list[TrunkRead]:
    result = await session.scalars(select(Trunk).order_by(Trunk.name))
    return [TrunkRead.from_orm_trunk(t) for t in result]


@router.get("/{trunk_id}", response_model=TrunkRead)
async def get_trunk(trunk_id: int, session: AsyncSession = Depends(get_session)) -> TrunkRead:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return TrunkRead.from_orm_trunk(trunk)


@router.get("/{trunk_id}/status", response_model=TrunkStatusResult)
async def trunk_status(
    trunk_id: int,
    session: AsyncSession = Depends(get_session),
    esl_factory=Depends(get_esl_factory),
) -> TrunkStatusResult:
    """トランクの sofia ゲートウェイ登録状態を返す。

    FreeSWITCH へ到達できない場合も 200 で state=UNKNOWN を返し、
    GUI が常に状態をレンダリングできるようにする。
    """
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    client = esl_factory()

    async def _query() -> str:
        await client.connect()
        return await client.api(f"sofia status gateway {trunk.name}")

    try:
        out = await asyncio.wait_for(_query(), timeout=_STATUS_TIMEOUT)
    except (TimeoutError, OSError, ESLError):
        return TrunkStatusResult(registered=False, state="UNKNOWN")
    finally:
        await client.close()

    m = _STATE_RE.search(out)
    state = m.group(1) if m else "NOT_LOADED"
    return TrunkStatusResult(registered=state == "REGED", state=state)


@router.patch("/{trunk_id}", response_model=TrunkRead)
async def update_trunk(
    trunk_id: int,
    body: TrunkUpdate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> TrunkRead:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for fld in (
        "display_name",
        "host",
        "username",
        "password",
        "did_number",
        "caller_id",
        "enabled",
    ):
        val = getattr(body, fld)
        if val is not None:
            setattr(trunk, fld, val)
    await session.commit()
    await session.refresh(trunk)
    # killgw + rescan で設定変更(パスワード等)を反映し REGISTER をやり直す
    await listener.notify(session, sync_gateway=trunk.name)
    return TrunkRead.from_orm_trunk(trunk)


@router.delete("/{trunk_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_trunk(
    trunk_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    trunk = await session.get(Trunk, trunk_id)
    if trunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    gateway_name = trunk.name
    await session.delete(trunk)
    await session.commit()
    # killgw で FS 上のゲートウェイも破棄する(XML からは消えているので rescan で復活しない)
    await listener.notify(session, sync_gateway=gateway_name)
