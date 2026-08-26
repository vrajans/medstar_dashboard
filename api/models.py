"""
api/models.py
SQLAlchemy ORM models for MedStar / InsightHub PostgreSQL schema.

Tables:
  users         -- authentication (mirrors SQLite users table from auth.py)
  sales         -- daily sales records (mirrors SQLite sales table)
  purchases     -- GRN / purchase records (mirrors SQLite purchases table)
  refresh_tokens -- JWT refresh token store for blacklisting on logout
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, Integer, JSON,
    String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class AdhocDataset(Base):
    """Persisted ad-hoc dataset — schema-on-read. Rows are stored as JSON so any
    tabular data can land in the warehouse, tenant-scoped and durable across
    sessions (satisfies the persistence claim vs one-time chat analysis)."""
    __tablename__ = "adhoc_dataset"

    id:         Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:  Mapped[int | None] = mapped_column(Integer, index=True)
    name:       Mapped[str]  = mapped_column(String(200), nullable=False)
    columns:    Mapped[list] = mapped_column(JSON, default=list)
    row_count:  Mapped[int]  = mapped_column(Integer, default=0)
    data:       Mapped[list] = mapped_column(JSON, default=list)   # list of row dicts
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PIRun(Base):
    """A persisted Payment-Integrity analysis run. The raw claims land in a
    warehouse dataset (adhoc_dataset, linked by dataset_id); this row holds the
    computed result so a customer's findings survive across sessions."""
    __tablename__ = "pi_run"

    id:             Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:      Mapped[int | None] = mapped_column(Integer, index=True)
    name:           Mapped[str]  = mapped_column(String(200), nullable=False)
    dataset_id:     Mapped[int | None] = mapped_column(Integer)   # → adhoc_dataset.id (lineage)
    row_count:      Mapped[int]  = mapped_column(Integer, default=0)
    total_paid:     Mapped[float] = mapped_column(Float, default=0.0)
    amount_at_risk: Mapped[float] = mapped_column(Float, default=0.0)
    pct_at_risk:    Mapped[float] = mapped_column(Float, default=0.0)
    flagged_claims: Mapped[int]  = mapped_column(Integer, default=0)
    result:         Mapped[dict] = mapped_column(JSON, default=dict)   # summary/findings/providers
    created_at:     Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ── Users ─────────────────────────────────────────────────────────────────────
class User(Base):
    __tablename__ = "users"

    id:           Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    username:     Mapped[str]  = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str]  = mapped_column(String(128), nullable=False, default="")
    password_hash:Mapped[str]  = mapped_column(Text, nullable=False)
    role:         Mapped[str]  = mapped_column(String(16), nullable=False, default="viewer")
    is_active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Which customer (tenant) this login belongs to. NULL = internal/admin (all-access).
    tenant_id:    Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    created_at:   Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def is_admin(self) -> bool:
        return self.role == "admin"

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r} role={self.role!r}>"


# ── JWT Refresh Tokens ────────────────────────────────────────────────────────
class RefreshToken(Base):
    """Stores issued refresh tokens so we can revoke on logout."""
    __tablename__ = "refresh_tokens"

    id:         Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id:    Mapped[int]  = mapped_column(Integer, nullable=False, index=True)
    token_hash: Mapped[str]  = mapped_column(String(128), nullable=False, unique=True, index=True)
    issued_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ── Sales ─────────────────────────────────────────────────────────────────────
class SalesRecord(Base):
    """One row = one day's sales summary for a branch.
    Columns mirror SALES_COLS in data_loader.py exactly.
    """
    __tablename__ = "sales"

    id:               Mapped[int]   = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    branch:           Mapped[str]   = mapped_column(String(64),  nullable=False, index=True)
    month_label:      Mapped[str]   = mapped_column(String(32),  nullable=False)
    bill_date:        Mapped[str]   = mapped_column(String(32),  nullable=False, index=True)
    net_amount:       Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cash_bill_count:  Mapped[float] = mapped_column(Float, nullable=True)
    cash_sales:       Mapped[float] = mapped_column(Float, nullable=True)
    credit_bill_count:Mapped[float] = mapped_column(Float, nullable=True)
    credit_sales:     Mapped[float] = mapped_column(Float, nullable=True)
    card_bill_count:  Mapped[float] = mapped_column(Float, nullable=True)
    card_sales:       Mapped[float] = mapped_column(Float, nullable=True)
    return_count:     Mapped[float] = mapped_column(Float, nullable=True)
    cash_return:      Mapped[float] = mapped_column(Float, nullable=True)
    discount:         Mapped[float] = mapped_column(Float, nullable=True)
    total_bills:      Mapped[float] = mapped_column(Float, nullable=True)
    pharma_sales:     Mapped[float] = mapped_column(Float, nullable=True)
    non_pharma_sales: Mapped[float] = mapped_column(Float, nullable=True)
    cash_in_hand:     Mapped[float] = mapped_column(Float, nullable=True)
    cost_of_sales:    Mapped[float] = mapped_column(Float, nullable=True)
    value:            Mapped[float] = mapped_column(Float, nullable=True)
    margin_pct:       Mapped[float] = mapped_column(Float, nullable=True)

    __table_args__ = (
        UniqueConstraint("branch", "bill_date", name="uq_sales_branch_date"),
    )

    def __repr__(self) -> str:
        return f"<SalesRecord branch={self.branch!r} date={self.bill_date!r} net={self.net_amount}>"


# ── Purchases ─────────────────────────────────────────────────────────────────
class PurchaseRecord(Base):
    """One row = one GRN line item.
    Columns mirror PURCHASE_COLS in data_loader.py exactly.
    """
    __tablename__ = "purchases"

    id:               Mapped[int]   = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    branch:           Mapped[str]   = mapped_column(String(64),  nullable=False, index=True)
    month_label:      Mapped[str]   = mapped_column(String(32),  nullable=False)
    supplier_code:    Mapped[str]   = mapped_column(String(32),  nullable=True)
    supplier_name:    Mapped[str]   = mapped_column(String(256), nullable=True, index=True)
    gross_amount:     Mapped[float] = mapped_column(Float, nullable=True)
    discount_pct:     Mapped[float] = mapped_column(Float, nullable=True)
    adjustment_value: Mapped[float] = mapped_column(Float, nullable=True)
    net_amount:       Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    vat_amount:       Mapped[float] = mapped_column(Float, nullable=True)
    grn_number:       Mapped[str]   = mapped_column(String(64),  nullable=True)
    grn_date:         Mapped[str]   = mapped_column(String(32),  nullable=True, index=True)
    invoice_number:   Mapped[str]   = mapped_column(String(64),  nullable=True)
    invoice_date:     Mapped[str]   = mapped_column(String(32),  nullable=True)
    base_amount:      Mapped[float] = mapped_column(Float, nullable=True)
    sgst:             Mapped[float] = mapped_column(Float, nullable=True)
    cgst:             Mapped[float] = mapped_column(Float, nullable=True)
    igst:             Mapped[float] = mapped_column(Float, nullable=True)
    total_gst:        Mapped[float] = mapped_column(Float, nullable=True)
    amount:           Mapped[float] = mapped_column(Float, nullable=True)
    dealer_type:      Mapped[str]   = mapped_column(String(64),  nullable=True)

    def __repr__(self) -> str:
        return f"<PurchaseRecord branch={self.branch!r} grn={self.grn_number!r} net={self.net_amount}>"


# ── Tenants ───────────────────────────────────────────────────────────────────

class Tenant(Base):
    """One row = one InsightHub customer / organisation."""
    __tablename__ = "tenants"

    id:            Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    name:          Mapped[str]  = mapped_column(String(128), nullable=False)
    slug:          Mapped[str]  = mapped_column(String(64),  nullable=False, unique=True, index=True)
    domain_type:   Mapped[str]  = mapped_column(String(32),  nullable=False)   # pharmacy|retail|saas|accounting|generic
    plan:          Mapped[str]  = mapped_column(String(32),  nullable=False, default="basic")  # basic|pro|enterprise
    contact_email: Mapped[str]  = mapped_column(String(256), nullable=False, default="")
    is_active:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at:    Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id} slug={self.slug!r} domain={self.domain_type!r}>"


# ── Tenant Modules ────────────────────────────────────────────────────────────

class TenantModule(Base):
    """Feature flags per tenant — which InsightHub modules are enabled."""
    __tablename__ = "tenant_modules"

    id:         Mapped[int]  = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:  Mapped[int]  = mapped_column(Integer, nullable=False, index=True)
    module_name:Mapped[str]  = mapped_column(String(64), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "module_name", name="uq_tenant_module"),
    )

    def __repr__(self) -> str:
        return f"<TenantModule tenant={self.tenant_id} module={self.module_name!r} enabled={self.is_enabled}>"


# ── Schema Mappings ───────────────────────────────────────────────────────────

class SchemaMapping(Base):
    """Maps a tenant's raw source column name to an InsightHub canonical column."""
    __tablename__ = "schema_mappings"

    id:               Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id:        Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    domain_type:      Mapped[str] = mapped_column(String(32),  nullable=False)
    entity:           Mapped[str] = mapped_column(String(32),  nullable=False)  # "sales" | "inventory" | "purchases"
    source_column:    Mapped[str] = mapped_column(String(128), nullable=False)
    canonical_column: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "domain_type", "entity", "canonical_column",
                         name="uq_mapping_tenant_canonical"),
    )

    def __repr__(self) -> str:
        return (f"<SchemaMapping tenant={self.tenant_id} "
                f"{self.source_column!r} -> {self.canonical_column!r}>")
