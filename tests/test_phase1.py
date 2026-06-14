"""
Phase 1 — Automated Test Suite
Black-box, White-box, Security, Usability checks
Run: python -m pytest tests/test_phase1.py -v
"""

import json, re, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest

# ══════════════════════════════════════════════════════════════
# WHITE-BOX — Import & Module Tests
# ══════════════════════════════════════════════════════════════

class TestImports:
    def test_security_module_imports(self):
        from api.security import hash_password, verify_password, create_access_token, \
                                  decode_access_token, create_refresh_token, JWT_SECRET
        assert JWT_SECRET

    def test_domain_library_imports(self):
        from api.domain_library import (
            PHARMACY_SALES_SCHEMA, PHARMACY_PURCHASES_SCHEMA,
            DEFAULT_MODULES, MEDSTAR_DEFAULT_MAPPINGS, get_canonical_schema
        )
        assert len(DEFAULT_MODULES) == 6
        assert len(MEDSTAR_DEFAULT_MAPPINGS) > 0

    def test_pydantic_schemas_import(self):
        from api.schemas import (
            LoginRequest, TokenResponse, TenantCreate,
            TenantModuleUpdate, SchemaMappingCreate
        )
        assert "username" in LoginRequest.model_fields
        assert "slug"     in TenantCreate.model_fields

    def test_login_page_renders(self):
        from login_page import render_login
        html = render_login()
        assert "InsightHub"        in html
        assert "__ERROR_BLOCK__"   not in html
        assert "__NEXT_URL__"      not in html
        assert "__USERNAME_VAL__"  not in html

    def test_login_page_substitutes_error(self):
        from login_page import render_login
        html = render_login(error="Bad credentials", next_url="/dash", username_val="admin")
        assert "Bad credentials" in html
        assert "/dash"           in html
        assert "admin"           in html


# ══════════════════════════════════════════════════════════════
# WHITE-BOX — Security Unit Tests
# ══════════════════════════════════════════════════════════════

class TestSecurity:
    def test_password_hash_is_bcrypt(self):
        from api.security import hash_password
        h = hash_password("admin123")
        assert h.startswith("$2b$")

    def test_password_verify_correct(self):
        from api.security import hash_password, verify_password
        h = hash_password("mypassword")
        assert verify_password("mypassword", h) is True

    def test_password_verify_wrong(self):
        from api.security import hash_password, verify_password
        h = hash_password("mypassword")
        assert verify_password("wrongpass", h) is False

    def test_access_token_round_trip(self):
        from api.security import create_access_token, decode_access_token
        tok = create_access_token(user_id=1, username="admin", role="admin")
        p = decode_access_token(tok)
        assert p is not None
        assert p["sub"]      == "1"
        assert p["username"] == "admin"
        assert p["role"]     == "admin"
        assert p["type"]     == "access"

    def test_tampered_token_rejected(self):
        from api.security import create_access_token, decode_access_token
        tok = create_access_token(user_id=1, username="admin", role="admin")
        assert decode_access_token(tok[:-5] + "XXXXX") is None

    def test_refresh_token_not_valid_as_access(self):
        from api.security import create_refresh_token, decode_access_token
        tok, _, _ = create_refresh_token(user_id=1)
        assert decode_access_token(tok) is None

    def test_hash_jti_deterministic_and_unique(self):
        from api.security import hash_jti
        assert hash_jti("abc") == hash_jti("abc")
        assert hash_jti("abc") != hash_jti("xyz")
        assert len(hash_jti("abc")) == 64


# ══════════════════════════════════════════════════════════════
# WHITE-BOX — Domain Library
# ══════════════════════════════════════════════════════════════

class TestDomainLibrary:
    def test_pharmacy_sales_required_fields(self):
        from api.domain_library import PHARMACY_SALES_SCHEMA
        names = [f["canonical_name"] for f in PHARMACY_SALES_SCHEMA]
        for req in ["bill_date", "net_amount", "margin_pct", "total_bills"]:
            assert req in names

    def test_pharmacy_purchases_required_fields(self):
        from api.domain_library import PHARMACY_PURCHASES_SCHEMA
        names = [f["canonical_name"] for f in PHARMACY_PURCHASES_SCHEMA]
        for req in ["grn_date", "supplier_name", "net_amount", "total_gst"]:
            assert req in names

    def test_all_fields_have_display_name_and_description(self):
        from api.domain_library import PHARMACY_SALES_SCHEMA, PHARMACY_PURCHASES_SCHEMA
        for schema in [PHARMACY_SALES_SCHEMA, PHARMACY_PURCHASES_SCHEMA]:
            for f in schema:
                assert f.get("display_name"),  f"No display_name: {f['canonical_name']}"
                assert f.get("description"),   f"No description: {f['canonical_name']}"

    def test_get_canonical_schema_known(self):
        from api.domain_library import get_canonical_schema
        assert get_canonical_schema("pharmacy", "sales")     is not None
        assert get_canonical_schema("pharmacy", "purchases") is not None
        assert get_canonical_schema("retail",   "sales")     is not None

    def test_get_canonical_schema_unknown_returns_none(self):
        from api.domain_library import get_canonical_schema
        assert get_canonical_schema("unknown", "sales") is None
        assert get_canonical_schema("pharmacy", "xyz")  is None

    def test_default_modules_exact_set(self):
        from api.domain_library import DEFAULT_MODULES
        assert set(DEFAULT_MODULES) == {
            "sales_analytics","purchase_analytics","pdf_reports",
            "data_upload","threshold_alerts","branch_compare"
        }

    def test_medstar_mappings_are_identity(self):
        from api.domain_library import MEDSTAR_DEFAULT_MAPPINGS
        for m in MEDSTAR_DEFAULT_MAPPINGS:
            assert m["source_column"] == m["canonical_column"], \
                f"Non-identity mapping: {m}"


# ══════════════════════════════════════════════════════════════
# BLACK-BOX — Pydantic Schema Validation
# ══════════════════════════════════════════════════════════════

class TestSchemaValidation:
    def test_tenant_slug_valid_lowercase_hyphen(self):
        from api.schemas import TenantCreate
        t = TenantCreate(name="Apollo", slug="apollo-pharmacy",
                         domain_type="pharmacy", plan="basic")
        assert t.slug == "apollo-pharmacy"

    def test_tenant_slug_rejects_uppercase(self):
        from api.schemas import TenantCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TenantCreate(name="A", slug="Apollo-Pharmacy",
                         domain_type="pharmacy", plan="basic")

    def test_tenant_slug_rejects_spaces(self):
        from api.schemas import TenantCreate
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            TenantCreate(name="A", slug="apollo pharmacy",
                         domain_type="pharmacy", plan="basic")

    def test_login_request_rejects_empty(self):
        from api.schemas import LoginRequest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            LoginRequest()

    def test_tenant_module_bulk_update(self):
        from api.schemas import TenantModuleUpdate
        t = TenantModuleUpdate(modules=[
            {"module_name": "sales_analytics", "is_enabled": True},
            {"module_name": "pdf_reports",     "is_enabled": False},
        ])
        assert len(t.modules) == 2


# ══════════════════════════════════════════════════════════════
# SECURITY — Static Source Analysis
# ══════════════════════════════════════════════════════════════

class TestSecurityStatic:
    BASE = os.path.dirname(os.path.dirname(__file__))

    def _src(self, relpath):
        with open(os.path.join(self.BASE, relpath), encoding="utf-8") as f:
            return f.read()

    def test_no_hardcoded_password_in_security_module(self):
        src = self._src("api/security.py")
        assert "admin123"    not in src
        assert "password123" not in src

    def test_passwords_seeded_via_hash_function(self):
        src = self._src("api/main.py")
        assert "hash_password(" in src

    def test_jwt_secret_from_env(self):
        src = self._src("api/security.py")
        assert 'os.getenv("JWT_SECRET"' in src

    def test_cors_not_wildcard(self):
        src = self._src("api/main.py")
        assert 'allow_origins=["*"]'  not in src
        assert 'allow_origins  = ['       in src

    def test_no_fstring_sql_injection(self):
        for path in ["api/routers/auth.py","api/routers/tenants.py",
                     "api/routers/sales.py","api/routers/purchases.py"]:
            src = self._src(path)
            hits = re.findall(r'f["\'].*?SELECT.*?\{', src, re.IGNORECASE)
            assert not hits, f"Possible SQLi in {path}: {hits}"

    def test_refresh_token_hashed_before_db(self):
        src = self._src("api/routers/auth.py")
        assert "hash_jti(" in src

    def test_login_page_uses_replace_not_format(self):
        src = self._src("login_page.py")
        after_def = src.split("def render_login")[1]
        # strip docstring block
        import re as _re
        after_def = _re.sub(r'""".*?"""', "", after_def, flags=_re.DOTALL)
        assert ".format(" not in after_def
        assert ".replace("     in after_def

    def test_no_debug_true_in_production_config(self):
        src = self._src("api/main.py")
        assert "debug=True" not in src


# ══════════════════════════════════════════════════════════════
# USABILITY — UI & UX Checks
# ══════════════════════════════════════════════════════════════

class TestUsability:
    def test_login_shows_default_credentials_hint(self):
        from login_page import render_login
        h = render_login()
        assert "admin123"  in h
        assert "viewer123" in h

    def test_login_preserves_next_url(self):
        from login_page import render_login
        h = render_login(next_url="/sales")
        assert "/sales" in h

    def test_login_error_shown_with_icon(self):
        from login_page import render_login
        h = render_login(error="Invalid credentials")
        assert "Invalid credentials" in h
        assert 'class="error"'       in h

    def test_module_labels_cover_all_defaults(self):
        from api.domain_library import DEFAULT_MODULES
        from tenant_portal import MODULE_LABELS
        for mod in DEFAULT_MODULES:
            assert mod in MODULE_LABELS, f"No label for module '{mod}'"

    def test_all_module_labels_are_human_readable(self):
        from tenant_portal import MODULE_LABELS
        for key, label in MODULE_LABELS.items():
            assert label[0].isupper(), f"Label not capitalised: '{label}'"
            assert "_" not in label,   f"Label has underscore: '{label}'"

    def test_domain_options_have_label_and_value(self):
        from tenant_portal import DOMAIN_OPTIONS, PLAN_OPTIONS
        for opt in DOMAIN_OPTIONS + PLAN_OPTIONS:
            assert "label" in opt
            assert "value" in opt

