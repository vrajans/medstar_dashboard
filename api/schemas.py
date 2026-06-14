"""
api/schemas.py
Pydantic v2 request / response schemas for InsightHub FastAPI service.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


# ── Auth schemas ──────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token:  str
    refresh_token: str
    token_type:    str = "bearer"
    expires_in:    int  # access token TTL in seconds


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    """Returned by /auth/refresh — only a new access token."""
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int


# ── User schemas ──────────────────────────────────────────────────────────────

class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:           int
    username:     str
    display_name: str
    role:         str
    is_active:    bool
    created_at:   datetime


class UserCreate(BaseModel):
    username:     str = Field(..., min_length=2, max_length=64)
    display_name: str = Field("", max_length=128)
    password:     str = Field(..., min_length=6)
    role:         str = Field("viewer", pattern="^(admin|viewer)$")


class UserUpdate(BaseModel):
    role:      Optional[str]  = Field(None, pattern="^(admin|viewer)$")
    is_active: Optional[bool] = None


# ── Sales schemas ─────────────────────────────────────────────────────────────

class SalesOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               int
    branch:           str
    month_label:      str
    bill_date:        str
    net_amount:       float
    cash_bill_count:  Optional[float] = None
    cash_sales:       Optional[float] = None
    credit_bill_count:Optional[float] = None
    credit_sales:     Optional[float] = None
    card_bill_count:  Optional[float] = None
    card_sales:       Optional[float] = None
    return_count:     Optional[float] = None
    cash_return:      Optional[float] = None
    discount:         Optional[float] = None
    total_bills:      Optional[float] = None
    pharma_sales:     Optional[float] = None
    non_pharma_sales: Optional[float] = None
    cash_in_hand:     Optional[float] = None
    cost_of_sales:    Optional[float] = None
    value:            Optional[float] = None
    margin_pct:       Optional[float] = None


class SalesSummary(BaseModel):
    """Aggregated sales totals for a period / branch filter."""
    branch:        Optional[str] = None
    total_revenue: float
    total_cost:    float
    gross_margin:  float          # (revenue - cost) / revenue * 100
    total_bills:   float
    return_count:  float
    pharma_pct:    float          # pharma_sales / net_amount * 100
    period_start:  Optional[str] = None
    period_end:    Optional[str] = None
    row_count:     int


# ── Purchase schemas ──────────────────────────────────────────────────────────

class PurchaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               int
    branch:           str
    month_label:      str
    supplier_code:    Optional[str] = None
    supplier_name:    Optional[str] = None
    gross_amount:     Optional[float] = None
    discount_pct:     Optional[float] = None
    adjustment_value: Optional[float] = None
    net_amount:       float
    vat_amount:       Optional[float] = None
    grn_number:       Optional[str] = None
    grn_date:         Optional[str] = None
    invoice_number:   Optional[str] = None
    invoice_date:     Optional[str] = None
    base_amount:      Optional[float] = None
    sgst:             Optional[float] = None
    cgst:             Optional[float] = None
    igst:             Optional[float] = None
    total_gst:        Optional[float] = None
    amount:           Optional[float] = None
    dealer_type:      Optional[str] = None


class PurchaseSummary(BaseModel):
    """Aggregated purchase totals for a period / branch filter."""
    branch:          Optional[str] = None
    total_net:       float
    total_gross:     float
    total_gst:       float
    total_discount:  float
    supplier_count:  int
    grn_count:       int
    period_start:    Optional[str] = None
    period_end:      Optional[str] = None
    row_count:       int


# ── Generic paginated response ────────────────────────────────────────────────

class PaginatedResponse(BaseModel, Generic[T]):
    items:   List[Any]
    total:   int
    page:    int
    size:    int
    pages:   int


# ── Health check ──────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:  str = "ok"
    version: str = "1.0.0"
    db:      str = "connected"


# ── Tenant schemas ────────────────────────────────────────────────────────────

class TenantCreate(BaseModel):
    name:          str = Field(..., min_length=2, max_length=128)
    slug:          str = Field(..., min_length=2, max_length=64,
                               pattern=r"^[a-z0-9-]+$")   # lowercase, hyphens only
    domain_type:   str = Field(..., pattern="^(pharmacy|retail)$")
    plan:          str = Field("basic", pattern="^(basic|pro|enterprise)$")
    contact_email: str = Field("", max_length=256)


class TenantUpdate(BaseModel):
    name:          Optional[str] = Field(None, min_length=2, max_length=128)
    plan:          Optional[str] = Field(None, pattern="^(basic|pro|enterprise)$")
    contact_email: Optional[str] = Field(None, max_length=256)
    is_active:     Optional[bool] = None


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:            int
    name:          str
    slug:          str
    domain_type:   str
    plan:          str
    contact_email: str
    is_active:     bool
    created_at:    datetime


# ── Tenant Module schemas ─────────────────────────────────────────────────────

class TenantModuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:          int
    tenant_id:   int
    module_name: str
    is_enabled:  bool


class TenantModuleUpdate(BaseModel):
    """Bulk update — send the full list of module states."""
    modules: List[dict]   # [{"module_name": "sales_analytics", "is_enabled": true}, ...]


# ── Schema Mapping schemas ────────────────────────────────────────────────────

class SchemaMappingCreate(BaseModel):
    domain_type:      str = Field(..., pattern="^(pharmacy|retail)$")
    entity:           str = Field(..., pattern="^(sales|purchases|inventory)$")
    source_column:    str = Field(..., min_length=1, max_length=128)
    canonical_column: str = Field(..., min_length=1, max_length=128)


class SchemaMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               int
    tenant_id:        int
    domain_type:      str
    entity:           str
    source_column:    str
    canonical_column: str


# ── Domain Library schemas ────────────────────────────────────────────────────

class CanonicalField(BaseModel):
    canonical_name: str
    display_name:   str
    data_type:      str          # "date" | "float" | "integer" | "string"
    category:       str          # "identifier" | "financial" | "operational" | "gst"
    is_required:    bool
    description:    str


class DomainSchemaOut(BaseModel):
    domain_type: str
    entity:      str
    fields:      List[CanonicalField]
