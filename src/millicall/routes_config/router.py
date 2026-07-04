from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.deps import get_change_listener, get_session, require_admin
from millicall.models import Extension, Route
from millicall.routes_config.enums import RouteTargetType
from millicall.routes_config.schemas import RouteCreate, RouteRead, RouteUpdate
from millicall.telephony.hooks import ExtensionChangeListener

router = APIRouter(prefix="/api/routes", tags=["routes"], dependencies=[Depends(require_admin)])


async def _validate_target(session: AsyncSession, target_type: RouteTargetType, value: str) -> None:
    if target_type == RouteTargetType.EXTENSION:
        ext = await session.scalar(select(Extension).where(Extension.number == value))
        if ext is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"extension {value} does not exist",
            )


@router.post("", response_model=RouteRead, status_code=status.HTTP_201_CREATED)
async def create_route(
    body: RouteCreate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> Route:
    await _validate_target(session, body.target_type, body.target_value)
    existing = await session.scalar(select(Route).where(Route.match_number == body.match_number))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="match_number exists")
    route = Route(
        match_number=body.match_number,
        target_type=body.target_type.value,
        target_value=body.target_value,
        enabled=body.enabled,
    )
    session.add(route)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="match_number exists"
        ) from None
    await session.refresh(route)
    await listener.notify(session)
    return route


@router.get("", response_model=list[RouteRead])
async def list_routes(session: AsyncSession = Depends(get_session)) -> list[Route]:
    result = await session.scalars(select(Route).order_by(Route.match_number))
    return list(result)


@router.get("/{route_id}", response_model=RouteRead)
async def get_route(route_id: int, session: AsyncSession = Depends(get_session)) -> Route:
    route = await session.get(Route, route_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return route


@router.patch("/{route_id}", response_model=RouteRead)
async def update_route(
    route_id: int,
    body: RouteUpdate,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> Route:
    route = await session.get(Route, route_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    try:
        new_type = body.target_type or RouteTargetType(route.target_type)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"invalid stored target_type: {route.target_type}",
        ) from None
    new_value = body.target_value if body.target_value is not None else route.target_value
    if body.target_type is not None or body.target_value is not None:
        await _validate_target(session, new_type, new_value)
    route.target_type = new_type.value
    route.target_value = new_value
    if body.enabled is not None:
        route.enabled = body.enabled
    await session.commit()
    await session.refresh(route)
    await listener.notify(session)
    return route


@router.delete("/{route_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_route(
    route_id: int,
    session: AsyncSession = Depends(get_session),
    listener: ExtensionChangeListener = Depends(get_change_listener),
) -> None:
    route = await session.get(Route, route_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(route)
    await session.commit()
    await listener.notify(session)
