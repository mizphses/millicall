"""Shared UTC range validation for paginated history endpoints."""

from datetime import UTC, datetime

from fastapi import HTTPException


def validate_period(
    since: datetime | None, until: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    """The database stores UTC timestamps; date filters use an exclusive end."""
    def utc(value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    since, until = utc(since), utc(until)
    if since is not None and until is not None and since >= until:
        raise HTTPException(status_code=422, detail="終了日時は開始日時より後にしてください")
    return since, until
