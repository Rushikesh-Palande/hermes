"""
/api/packages — configuration package CRUD + clone (gap 5 dependency).

A *package* is an immutable-once-used bundle of detector parameters
(see ``parameters`` table). Sessions reference a package, which is
why operators starting a session via the Sessions UI need a way to
list, create, and clone packages.

Lifecycle (legacy parity, enforced by DB triggers in 0004_triggers.sql):

  * Newly created packages are unlocked. The operator can edit the
    parameter rows under them freely via /api/config.
  * Once a session that used the package closes (``ended_at IS NOT NULL``),
    the trigger ``sessions_lock_package`` flips ``is_locked = TRUE``.
    Subsequent attempts to write parameters under that package via
    /api/config will be rejected. Operators must clone the package
    first.
  * ``is_default = TRUE`` marks the package the bootstrap helper
    auto-creates on first boot. There's exactly one default at any
    time; other packages have ``is_default = FALSE``.
  * ``parent_package_id`` is set on cloned packages so downstream
    tooling can trace provenance ("this was cloned from that, which
    was cloned from the original").

In scope for this release (alpha.19):

  GET    /api/packages        — list (newest first)
  GET    /api/packages/{id}   — detail
  POST   /api/packages        — create blank package
  POST   /api/packages/{id}/clone — clone, copying all parameter rows

NOT in scope yet:

  * PATCH for renaming / updating description / archiving — useful
    but operators don't need it to start using sessions. A follow-up
    can add it without breaking anything else.
  * DELETE — packages are append-only by design; deleting one would
    orphan its event/session history. Operators archive instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hermes.api.deps import CurrentUser, DbSession
from hermes.db.models import Package, Parameter

router = APIRouter()


# ─── Shapes ────────────────────────────────────────────────────────


class PackageIn(BaseModel):
    """POST /api/packages body."""

    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class PackageOut(BaseModel):
    """Response shape for list + get + create + clone."""

    model_config = ConfigDict(from_attributes=True)

    package_id: uuid.UUID
    name: str
    description: str | None
    is_default: bool
    is_locked: bool
    created_at: datetime
    created_by: str | None
    archived_at: datetime | None
    parent_package_id: uuid.UUID | None


# ─── Helpers ───────────────────────────────────────────────────────


async def _get_or_404(session: AsyncSession, package_id: uuid.UUID) -> Package:
    pkg = await session.get(Package, package_id)
    if pkg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"package {package_id} not found",
        )
    return pkg


# ─── Routes ────────────────────────────────────────────────────────


@router.get("", response_model=list[PackageOut])
async def list_packages(user: CurrentUser, session: DbSession) -> list[PackageOut]:
    """Return all packages, newest first."""
    del user
    rows = await session.execute(select(Package).order_by(Package.created_at.desc()))
    return [PackageOut.model_validate(p) for p in rows.scalars().all()]


@router.post("", response_model=PackageOut, status_code=status.HTTP_201_CREATED)
async def create_package(
    payload: PackageIn,
    user: CurrentUser,
    session: DbSession,
) -> PackageOut:
    """Create a fresh, unlocked package with no parameter rows.

    The operator can then write thresholds via /api/config/... using
    this package_id. ``is_default`` is FALSE on creation; the default
    flag is owned by the bootstrap helper and shouldn't be flipped via
    this route.
    """
    del user
    pkg = Package(
        name=payload.name,
        description=payload.description,
        is_default=False,
        is_locked=False,
        created_by="api",
    )
    session.add(pkg)
    await session.flush()
    await session.refresh(pkg)
    await session.commit()
    return PackageOut.model_validate(pkg)


@router.get("/{package_id}", response_model=PackageOut)
async def get_package(
    package_id: uuid.UUID,
    user: CurrentUser,
    session: DbSession,
) -> PackageOut:
    del user
    pkg = await _get_or_404(session, package_id)
    return PackageOut.model_validate(pkg)


@router.post("/{package_id}/clone", response_model=PackageOut, status_code=status.HTTP_201_CREATED)
async def clone_package(
    package_id: uuid.UUID,
    payload: PackageIn,
    user: CurrentUser,
    session: DbSession,
) -> PackageOut:
    """Fork a package, copying every parameter row over.

    The clone is unlocked and references its source via
    ``parent_package_id`` so provenance can be traced. This is the
    canonical way to "edit a locked package": clone it, then edit the
    clone via /api/config and start a new session against the clone.
    """
    del user
    source = await _get_or_404(session, package_id)

    clone = Package(
        name=payload.name,
        description=payload.description,
        is_default=False,
        is_locked=False,
        created_by="api",
        parent_package_id=source.package_id,
    )
    session.add(clone)
    await session.flush()
    await session.refresh(clone)

    # Copy every parameter row from the source to the clone. Same
    # transaction so a failure rolls everything back. JSONB values are
    # immutable from SQLAlchemy's perspective; passing the same dict by
    # reference is safe because the new row will own its own row id.
    rows = (
        (await session.execute(select(Parameter).where(Parameter.package_id == source.package_id)))
        .scalars()
        .all()
    )
    for row in rows:
        session.add(
            Parameter(
                package_id=clone.package_id,
                key=row.key,
                value=row.value,
                scope=row.scope,
                device_id=row.device_id,
                sensor_id=row.sensor_id,
            )
        )
    await session.flush()
    await session.commit()
    return PackageOut.model_validate(clone)
