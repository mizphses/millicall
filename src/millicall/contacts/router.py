from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from millicall.contacts.schemas import ContactCreate, ContactRead, ContactUpdate
from millicall.deps import get_session, require_admin
from millicall.models import Contact

router = APIRouter(prefix="/api/contacts", tags=["contacts"], dependencies=[Depends(require_admin)])


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
async def create_contact(
    body: ContactCreate, session: AsyncSession = Depends(get_session)
) -> Contact:
    contact = Contact(
        name=body.name,
        phone_number=body.phone_number,
        company=body.company,
        department=body.department,
        notes=body.notes,
    )
    session.add(contact)
    await session.commit()
    await session.refresh(contact)
    return contact


@router.get("", response_model=list[ContactRead])
async def list_contacts(session: AsyncSession = Depends(get_session)) -> list[Contact]:
    result = await session.scalars(select(Contact).order_by(Contact.name))
    return list(result)


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(contact_id: int, session: AsyncSession = Depends(get_session)) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return contact


@router.patch("/{contact_id}", response_model=ContactRead)
async def update_contact(
    contact_id: int, body: ContactUpdate, session: AsyncSession = Depends(get_session)
) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for fld in ("name", "phone_number", "company", "department", "notes"):
        val = getattr(body, fld)
        if val is not None:
            setattr(contact, fld, val)
    await session.commit()
    await session.refresh(contact)
    return contact


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_contact(contact_id: int, session: AsyncSession = Depends(get_session)) -> None:
    contact = await session.get(Contact, contact_id)
    if contact is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    await session.delete(contact)
    await session.commit()
