"""
api/routers/tenants.py
Tenant management endpoints — admin-only.

POST   /tenants                       create tenant
GET    /tenants                       list all tenants
GET    /tenants/{tenant_id}           get one tenant
PATCH  /tenants/{tenant_id}           update name / plan / status
DELETE /tenants/{tenant_id}           soft-delete (deactivate)

GET    /tenants/{tenant_id}/modules            list module states
PUT    /tenants/{tenant_id}/modules            bulk-update module toggles

GET    /tenants/{tenant_id}/mappings           list schema mappings
POST   /tenants/{tenant_id}/mappings           create / upsert a mapping
DELETE /tenants/{tenant_id}/mappings/{map_id}  remove a mapping
"""

from __future__ import annotations

from typing import Annotated, List

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select

from ..deps import AdminUser, DBSession
from ..domain_library import DEFAULT_MODULES
from ..models import SchemaMapping, Tenant, TenantModule
from ..schemas import (
    SchemaMappingCreate,
    SchemaMappingOut,
    TenantCreate,
    TenantModuleOut,
    TenantModuleUpdate,
    TenantOut,
    TenantUpdate,
)

router = APIRouter(prefix="/tenants", tags=["Tenants"])


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_tenant_or_404(tenant_id: int, db: DBSession) -> Tenant:
    result = await db.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return tenant


async def _seed_modules(tenant_id: int, db: DBSession) -> None:
    """Seed DEFAULT_MODULES for a new tenant — all enabled."""
    for module_name in DEFAULT_MODULES:
        db.add(TenantModule(tenant_id=tenant_id, module_name=module_name, is_enabled=True))
    await db.flush()


# ── Tenant CRUD ───────────────────────────────────────────────────────────────

@router.post("", response_model=TenantOut, status_code=201, summary="Create new tenant")
async def create_tenant(body: TenantCreate, _admin: AdminUser, db: DBSession):
    # Check slug uniqueness
    existing = await db.execute(select(Tenant).where(Tenant.slug == body.slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Slug '{body.slug}' already taken")

    tenant = Tenant(
        name          = body.name,
        slug          = body.slug,
        domain_type   = body.domain_type,
        plan          = body.plan,
        contact_email = body.contact_email,
    )
    db.add(tenant)
    await db.flush()
    await db.refresh(tenant)

    # Seed default modules
    await _seed_modules(tenant.id, db)

    return tenant


@router.get("", response_model=List[TenantOut], summary="List all tenants")
async def list_tenants(
    _admin:    AdminUser,
    db:        DBSession,
    active_only: Annotated[bool, Query()] = False,
):
    q = select(Tenant).order_by(Tenant.created_at.desc())
    if active_only:
        q = q.where(Tenant.is_active == True)  # noqa: E712
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{tenant_id}", response_model=TenantOut, summary="Get one tenant")
async def get_tenant(tenant_id: int, _admin: AdminUser, db: DBSession):
    return await _get_tenant_or_404(tenant_id, db)


@router.patch("/{tenant_id}", response_model=TenantOut, summary="Update tenant")
async def update_tenant(tenant_id: int, body: TenantUpdate, _admin: AdminUser, db: DBSession):
    tenant = await _get_tenant_or_404(tenant_id, db)
    if body.name          is not None: tenant.name          = body.name
    if body.plan          is not None: tenant.plan          = body.plan
    if body.contact_email is not None: tenant.contact_email = body.contact_email
    if body.is_active     is not None: tenant.is_active     = body.is_active
    await db.flush()
    await db.refresh(tenant)
    return tenant


@router.delete("/{tenant_id}", summary="Deactivate tenant (soft delete)")
async def deactivate_tenant(tenant_id: int, _admin: AdminUser, db: DBSession):
    tenant = await _get_tenant_or_404(tenant_id, db)
    tenant.is_active = False
    return {"detail": f"Tenant '{tenant.name}' deactivated"}


# ── Module Toggles ────────────────────────────────────────────────────────────

@router.get("/{tenant_id}/modules", response_model=List[TenantModuleOut], summary="List tenant modules")
async def list_modules(tenant_id: int, _admin: AdminUser, db: DBSession):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(TenantModule)
        .where(TenantModule.tenant_id == tenant_id)
        .order_by(TenantModule.module_name)
    )
    return result.scalars().all()


@router.put("/{tenant_id}/modules", response_model=List[TenantModuleOut], summary="Bulk update module toggles")
async def update_modules(tenant_id: int, body: TenantModuleUpdate, _admin: AdminUser, db: DBSession):
    await _get_tenant_or_404(tenant_id, db)

    for item in body.modules:
        module_name = item.get("module_name")
        is_enabled  = item.get("is_enabled", True)
        if not module_name:
            continue

        result = await db.execute(
            select(TenantModule).where(
                TenantModule.tenant_id   == tenant_id,
                TenantModule.module_name == module_name,
            )
        )
        mod = result.scalar_one_or_none()
        if mod:
            mod.is_enabled = is_enabled
        else:
            db.add(TenantModule(tenant_id=tenant_id, module_name=module_name, is_enabled=is_enabled))

    await db.flush()

    result = await db.execute(
        select(TenantModule)
        .where(TenantModule.tenant_id == tenant_id)
        .order_by(TenantModule.module_name)
    )
    return result.scalars().all()


# ── Schema Mappings ───────────────────────────────────────────────────────────

@router.get("/{tenant_id}/mappings", response_model=List[SchemaMappingOut], summary="List schema mappings")
async def list_mappings(
    tenant_id:   int,
    _admin:      AdminUser,
    db:          DBSession,
    entity:      Annotated[str | None, Query()] = None,
    domain_type: Annotated[str | None, Query()] = None,
):
    await _get_tenant_or_404(tenant_id, db)
    q = select(SchemaMapping).where(SchemaMapping.tenant_id == tenant_id)
    if entity:
        q = q.where(SchemaMapping.entity == entity)
    if domain_type:
        q = q.where(SchemaMapping.domain_type == domain_type)
    result = await db.execute(q.order_by(SchemaMapping.entity, SchemaMapping.canonical_column))
    return result.scalars().all()


@router.post("/{tenant_id}/mappings", response_model=SchemaMappingOut, status_code=201,
             summary="Create or update a schema mapping")
async def upsert_mapping(tenant_id: int, body: SchemaMappingCreate, _admin: AdminUser, db: DBSession):
    await _get_tenant_or_404(tenant_id, db)

    # Upsert: if a mapping for this canonical_column already exists, update it
    result = await db.execute(
        select(SchemaMapping).where(
            SchemaMapping.tenant_id        == tenant_id,
            SchemaMapping.domain_type      == body.domain_type,
            SchemaMapping.entity           == body.entity,
            SchemaMapping.canonical_column == body.canonical_column,
        )
    )
    mapping = result.scalar_one_or_none()

    if mapping:
        mapping.source_column = body.source_column
    else:
        mapping = SchemaMapping(
            tenant_id        = tenant_id,
            domain_type      = body.domain_type,
            entity           = body.entity,
            source_column    = body.source_column,
            canonical_column = body.canonical_column,
        )
        db.add(mapping)

    await db.flush()
    await db.refresh(mapping)
    return mapping


@router.delete("/{tenant_id}/mappings/{mapping_id}", summary="Delete a schema mapping")
async def delete_mapping(tenant_id: int, mapping_id: int, _admin: AdminUser, db: DBSession):
    await _get_tenant_or_404(tenant_id, db)
    result = await db.execute(
        select(SchemaMapping).where(
            SchemaMapping.id        == mapping_id,
            SchemaMapping.tenant_id == tenant_id,
        )
    )
    mapping = result.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Mapping not found")
    await db.delete(mapping)
    return {"detail": "Mapping deleted"}
