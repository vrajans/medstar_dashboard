"""
api/routers/domains.py
Domain library endpoints — read-only reference data.

GET /domains                            list available domains
GET /domains/{domain_type}/entities     list entities in a domain
GET /domains/{domain_type}/{entity}     canonical schema for one entity
"""

from __future__ import annotations

from typing import List

from fastapi import APIRouter, HTTPException

from ..deps import CurrentUser
from ..domain_library import AVAILABLE_DOMAINS, DOMAIN_REGISTRY, get_domain_schema
from ..schemas import CanonicalField, DomainSchemaOut

router = APIRouter(prefix="/domains", tags=["Domain Library"])


@router.get("", summary="List available domains")
async def list_domains(_user: CurrentUser):
    return {
        "domains": [
            {
                "domain_type": d,
                "entities":    list(DOMAIN_REGISTRY[d].keys()),
                "field_counts": {
                    entity: len(fields)
                    for entity, fields in DOMAIN_REGISTRY[d].items()
                },
            }
            for d in AVAILABLE_DOMAINS
        ]
    }


@router.get("/{domain_type}/entities", summary="List entities in a domain")
async def list_entities(domain_type: str, _user: CurrentUser):
    if domain_type not in DOMAIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_type}' not found")
    return {"domain_type": domain_type, "entities": list(DOMAIN_REGISTRY[domain_type].keys())}


@router.get("/{domain_type}/{entity}", response_model=DomainSchemaOut,
            summary="Get canonical schema for a domain entity")
async def get_entity_schema(domain_type: str, entity: str, _user: CurrentUser):
    if domain_type not in DOMAIN_REGISTRY:
        raise HTTPException(status_code=404, detail=f"Domain '{domain_type}' not found")
    fields = get_domain_schema(domain_type, entity)
    if not fields:
        raise HTTPException(
            status_code=404,
            detail=f"Entity '{entity}' not found in domain '{domain_type}'"
        )
    return DomainSchemaOut(
        domain_type = domain_type,
        entity      = entity,
        fields      = [CanonicalField(**f) for f in fields],
    )
