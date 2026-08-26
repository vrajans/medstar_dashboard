# InsightHub — Phase 1 Build Summary & Test Runbook

*Prepared July 2026. Covers the data-platform build completed this phase, its verification status, and exactly how to run and test the complete flow.*

---

## 1. What was built

Everything below is **additive and flag-gated** — the existing Dash demo runs unchanged with all flags off. Each increment was tested before moving on.

| # | Increment | Files | Verified |
|---|---|---|---|
| 1 | **Dimensional warehouse** — star schema (dim_date/tenant/entity/product + fct_transaction), idempotent SCD2 loads, serving marts | `warehouse.py`, `test_warehouse.py` | 12/12 tests; exact parity |
| 2 | **Analytics cutover** — tenant analytics reads the marts behind a flag, auto-fallback to flat | `app.py` (flagged), `warehouse.load_tenant_df_from_mart` | flat vs mart identical |
| 3 | **dbt transformation** — staging → SCD2 snapshot → incremental fact → marts, portable DuckDB/Postgres | `warehouse_dbt/` | dbt build 19/19; parity proven |
| 4 | **LLM gateway** — Groq/OpenAI/Anthropic/Azure/+ with per-tenant BYO keys | `ai/llm_gateway.py`, `test_llm_gateway.py` | 22/22 routing tests |
| 5 | **AI Settings UI** — tenant tab to configure BYO provider/key/model | `llm_settings.py`, `app.py` | render + save/test/reset verified |
| 6 | **Orchestrator** — validate→transform→verify pipeline with run history, retries, event + nightly triggers | `orchestrator.py`, `test_orchestrator.py` | 17/17 tests |
| 7 | **Dagster graduation path** — same steps as assets + schedule + upload sensor | `orchestration/` | `dagster definitions validate` passed |
| 8 | **Customer API** — `/analytics/*` + `/ai/chat` reading the marts | `api/routers/analytics.py`, `api/routers/ai.py` | SQL parity vs warehouse KPIs |
| 9 | **Next.js customer app** — login, overview (KPIs+chart+top clients), AI chat | `customer_app/` | imports resolve; runs locally |
| 10 | **End-to-end test** — the whole flow in one command | `test_end_to_end.py` | ✅ 17/17 checks |

---

## 2. How it fits together (the spine)

```
Upload / connector
      │  (confirm_upload → background)
      ▼
Orchestrator.run_pipeline ──► validate ─► transform_load ─► verify (parity gate)
      │        │ records every run + step to pipeline_runs / pipeline_step_runs
      │        └ transform engine = inline warehouse.py  (or dbt: ORCHESTRATOR_TRANSFORM=dbt)
      ▼
Star-schema warehouse (SCD2)  ─►  serving marts (wh_mart_transaction)
      │                                     │
      ├── Dash analytics (WAREHOUSE_ANALYTICS=1)      ← internal cockpit
      └── FastAPI /analytics/* + /ai/chat  ─►  Next.js customer app  ← customer-facing
                                    │
                                    └── LLM gateway (Groq default, or tenant BYO key)
```

Nightly at 02:30 the orchestrator sweeps all tenants (reconciles deletes, refreshes marts).

---

## 3. Feature flags & env toggles

| Env var | Default | Effect |
|---|---|---|
| `WAREHOUSE_ANALYTICS` | `0` | `1` = Dash tenant analytics reads the marts (auto-fallback to flat) |
| `ORCHESTRATOR_TRANSFORM` | `inline` | `dbt` = pipeline runs `dbt build` instead of the inline loader |
| `LLM_PROVIDER` / `LLM_MODEL` | `groq` / llama | platform-wide default model |
| `NEXT_PUBLIC_API_URL` | `http://localhost:8000` | customer app → FastAPI base URL |

Nothing above needs to be set for the current Dash demo to work exactly as before.

---

## 4. Test the COMPLETE flow

### A) Fastest — one command (no servers, proves the data spine)

From the repo root:
```bash
python test_end_to_end.py     # upload → orchestrator → warehouse → marts → analytics SQL → idempotency
```
Also runnable individually:
```bash
python test_warehouse.py      # star schema + SCD2 + parity
python test_orchestrator.py   # pipeline + run history + retries
python test_llm_gateway.py    # provider routing + BYO
```
Expected: each ends with **ALL PASS ✅** / **COMPLETE FLOW PASS**.

### B) Live stack — click through the real product

**1. Dash app (existing product + warehouse-backed analytics)**
```bash
pip install -r requirements.txt
WAREHOUSE_ANALYTICS=1 python app.py        # http://localhost:8050
```
Test path:
- Log in as a tenant → **Upload Data** → drop an Excel/CSV → it auto-detects domain, redirects to **Overview**.
- Terminal shows `[orchestrator] upload pipeline: {'status': 'success', ...}` and `source=WAREHOUSE`.
- Open **🤖 AI Settings** → optionally paste your own OpenAI/Anthropic key → **Test connection**.
- Ask a question in **AI Chat** → grounded answer (uses your BYO key if set).

**2. FastAPI backend (serves the customer app)**
```bash
uvicorn api.main:app --reload --port 8000  # docs at http://localhost:8000/docs
```
Test path: open `/docs` → `POST /auth/login` (`admin`/`admin123`) → copy the token → try `GET /analytics/overview?tenant_id=3` (Authorize first). You should see revenue/costs/margin JSON.

**3. Next.js customer app**
```bash
cd customer_app
cp .env.local.example .env.local
npm install
npm run dev                                # http://localhost:3000
```
Test path: sign in → **Overview** shows KPI cards + Revenue-vs-Cost chart + top clients (identical numbers to Dash) → **AI Insights** → ask a question.

**4. (Optional) Dagster UI**
```bash
pip install dagster dagster-webserver
dagster dev -f orchestration/definitions.py # http://localhost:3000 (use a different port than Next.js)
```
Materialize the `raw_sources → warehouse → marts_verified` assets; watch the run succeed.

### Prereqs for the live stack
- The Dash app defaults to local SQLite (`medstar.db`) — works out of the box.
- The FastAPI backend targets Postgres (`PG_DSN` in `.env`). For the customer app to show data, the warehouse marts must exist in that same database — run the app once (or the orchestrator) against it so `wh_mart_transaction` is populated. Locally you can point both at the same DB.

---

## 5. What each test proves

| Test | Proves |
|---|---|
| `test_end_to_end.py` | The full chain works and stays correct across re-uploads (idempotency + change propagation) |
| `test_warehouse.py` | Star schema, SCD2 history, reconcile-deletes, mart parity |
| `test_orchestrator.py` | Pipeline success/failure recording, retries, run history, verify gate |
| `test_llm_gateway.py` | Provider dispatch (OpenAI/Anthropic/Azure), BYO precedence, context routing |
| `dbt build` (DuckDB) | The dbt models reproduce the warehouse exactly (19 nodes, 9 data tests) |

---

## 6. Safety notes

- Every new capability is **additive**: new `wh_*`, `pipeline_*`, `tenant_llm_config` tables; new modules; new API routers; a separate Next.js app. No existing table or Dash behavior was changed destructively.
- Warehouse loads and pipeline runs are **background + guarded** — a failure is logged and recorded, never raised into the upload UI.
- Analytics cutover is **flag-gated with auto-fallback** — enabling it can't blank a dashboard.
- Your live `medstar.db` was never modified during development/testing (all tests use throwaway temp DBs).

---

## 7. Remaining roadmap (not yet built)

From the BRD's Phase 2: connector framework + scheduled syncs, executive PWA/mobile, ad-hoc playground GA, plan-limit enforcement + usage metering, SOC 2 control program, and the semantic metric layer. The data spine they'd build on is now in place.
