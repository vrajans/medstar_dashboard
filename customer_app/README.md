# InsightHub — Customer Web App (Next.js)

The customer-facing front end (BRD §5.7). Next.js (App Router) + TypeScript +
Tailwind, talking to the existing **FastAPI** backend, which serves analytics from
the **dimensional warehouse serving marts** and AI answers from the **LLM gateway**.
The internal Dash app stays as the ops cockpit.

```
Next.js (:3000)  ──HTTP/JSON──>  FastAPI (:8000)  ──SQL──>  wh_mart_transaction
                                      │
                                      └── /ai/chat ── ai.llm_gateway (Groq / BYO)
```

## Pages
- `/login` — obtains a JWT from `/auth/login`.
- `/overview` — KPI cards (revenue, cost, net cash flow, margin, txns, customers),
  a monthly Revenue-vs-Cost chart (Recharts), and a top-clients table.
- `/ai` — grounded chat over the tenant's warehouse summary.

## API surface consumed
| Endpoint | Purpose |
|---|---|
| `POST /auth/login` | authenticate, returns `access_token` |
| `GET  /analytics/overview?tenant_id=` | headline KPIs |
| `GET  /analytics/timeseries?tenant_id=` | monthly revenue/cost |
| `GET  /analytics/top-entities?tenant_id=` | top clients |
| `POST /ai/chat` | grounded AI answer |

All calls send `Authorization: Bearer <token>`; a 401 clears the token and
redirects to `/login`.

## Run it

1. Start the backend (from the repo root):
   ```bash
   uvicorn api.main:app --reload --port 8000
   ```
2. Start the front end:
   ```bash
   cd customer_app
   cp .env.local.example .env.local        # set NEXT_PUBLIC_API_URL if not :8000
   npm install
   npm run dev                              # http://localhost:3000
   ```
3. Sign in (seeded dev creds: `admin` / `admin123`).

Type-check / build:
```bash
npm run typecheck
npm run build
```

## Notes
- **Auth storage:** this scaffold keeps the JWT in `localStorage` for simplicity.
  For production, switch to httpOnly + SameSite cookies (mitigates XSS token theft).
- **Tenant scoping:** the app passes `tenant_id` from `localStorage`. In production,
  derive it from the authenticated user's tenant claim server-side and reject
  cross-tenant requests — the API SQL is already tenant-parameterized.
- **Data source:** analytics read the warehouse marts kept fresh by the orchestrator
  (on upload + nightly), so the numbers match the Dash app exactly.
