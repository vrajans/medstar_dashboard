# InsightHub — Azure Deployment Guide (Demo → First Paying Customer)

*Prepared July 2026. Pricing figures are pay-as-you-go, US regions, checked against vendor pricing pages/APIs as of June–July 2026 — always re-verify in the pricing calculator before committing, rates change.*

## 1. The situation this guide optimizes for

You want the app **live and demo-able**, but costing close to **$0 while nobody is looking at it**, and you don't want to re-architect anything when the first paying customer signs. That single requirement — "only cost money while someone is actually using it" — is the deciding factor for every service choice below. It rules out anything with a fixed hourly/monthly floor (VMs, App Service Basic+, load balancers, always-on databases) and points straight at **serverless containers + a scale-to-zero database**.

InsightHub is a good fit for this: it's stateless per-request (Dash + Flask + SQLAlchemy), already reads its Postgres connection from `.env` (Neon), and doesn't need local disk to persist between restarts. That means it can drop into a serverless container platform with no code changes beyond containerizing it.

---

## 2. Recommended Azure architecture (demo phase)

| Layer | Service | Why | Idle cost |
|---|---|---|---|
| Compute | **Azure Container Apps** — Consumption plan, `minReplicas: 0` | True scale-to-zero, per-second billing, free managed TLS on custom domains | **$0** |
| Container registry | **GitHub Container Registry (GHCR)** | Free storage + bandwidth; Container Apps pulls from it directly | **$0** |
| Database | **Neon Postgres** (already wired in your `.env`) | Free tier auto-suspends after 5 min idle; you're already using it — don't move it to Azure yet | **$0** (within free tier) |
| Secrets | **Azure Key Vault** | Keeps `GROQ_API_KEY`, `JWT_SECRET`, Stripe/Twilio/SendGrid keys out of env vars; Container Apps reads via managed identity | ~pennies/month |
| File exports/uploads (if persisted) | **Azure Blob Storage** (Cool tier) | Pay only for what's stored; no server to keep running | ~pennies/month |
| Logs/metrics | **Azure Monitor / Log Analytics** | 5 GB/month ingestion free | **$0** at demo volume |
| CI/CD | **GitHub Actions** → build → push to GHCR → `az containerapp update` | Free minutes at this scale | **$0** |

**Realistic total during the demo period: $0–10/month.** The Container Apps consumption free grant (180,000 vCPU-seconds, 360,000 GiB-seconds, 2,000,000 requests — every month, forever) will absorb essentially all of a pre-revenue demo workload. Nothing runs, nothing is billed, until a request comes in.

### Why not the "obvious" Azure choices
- **App Service (even the cheapest B1 tier)** bills hourly whether or not anyone visits — no scale-to-zero. Wrong fit pre-revenue.
- **Azure Database for PostgreSQL Flexible Server** (even Burstable B1ms) is a fixed ~$12–15/month always-on VM-backed instance. You already have Neon, which does the same job for $0 at this scale — don't pay twice.
- **Azure Container Registry Basic** has a flat ~$0.167/day (~$5/month) fee regardless of usage. GHCR is free and Container Apps pulls from it natively — swap to ACR later only if you need private geo-replication or vulnerability scanning at scale.
- **Front Door / Application Gateway + WAF** is worth having once you have paying customers (DDoS protection, edge caching), but it has its own ~$35+/month floor. Container Apps' built-in free managed certificate covers HTTPS + custom domain for the demo phase without it.

### Containerization specifics
Yes — containerize it, don't try to run it directly on a PaaS runtime stack. A few things matter for cost and cold-start latency on serverless containers:
- Multi-stage `Dockerfile`, `python:3.11-slim` base, `gunicorn` (not the Dash dev server) as the WSGI entrypoint, e.g. `gunicorn -w 2 -k gthread --threads 4 -b 0.0.0.0:8050 app:server`.
- Keep the image small — smaller image = faster cold start = less billed startup time on scale-from-zero.
- No reliance on local disk surviving a restart — Container Apps replicas are ephemeral; anything that must persist (uploaded files, generated PDFs) goes to Blob Storage, not the container filesystem.
- Add a lightweight `/healthz` route for Container Apps' liveness probe.
- Set `minReplicas: 0`, `maxReplicas: 2–3` for the demo phase, HTTP-concurrency scale rule.
- **Security while demo-only**: turn on Container Apps IP restrictions (allow-list the prospects/your office IP) so random internet traffic can't rack up compute charges or probe an app that isn't hardened for production traffic yet. This costs nothing and is easy to remove later.

---

## 3. What changes once you land the first paying customer

| Change | Why |
|---|---|
| Set `minReplicas: 1` on Container Apps | Eliminates cold-start latency for paying users. Rough cost for one small always-on replica (0.5 vCPU / 1 GiB): ~$35–45/month. |
| Move Neon free → Neon paid tier, or Azure Postgres Flexible Server B1ms | Free tier's storage/compute-hour caps and cold-start-on-first-query become a real UX problem once someone depends on it. Neon paid is usage-metered (~$0.106/CU-hr + $0.35/GB storage); Azure Flexible Server B1ms is a flat ~$12–15/month. Stay on Neon unless data residency/compliance forces Azure. |
| Add autoscale rules (HTTP concurrency, CPU) | So a real traffic spike doesn't 503. |
| ACR Basic (swap from GHCR) | Private registry, vulnerability scanning, geo-replication if you expand regions. |
| Azure Front Door + WAF | DDoS protection, edge TLS, caching — now justified by real traffic and real risk. |
| Azure Cache for Redis (Basic C0, ~$16/month) | Only if you need shared session state across replicas or want to cache expensive aggregations. |
| Application Insights (paid tier) | Once log volume exceeds the 5 GB free grant. |

None of this requires re-architecting — it's dial-turning on the same Container Apps + Neon/Postgres foundation, which is the point of picking a scale-to-zero platform from day one.

---

## 4. Cross-cloud comparison

| | **Azure Container Apps** | **AWS** | **Google Cloud Run** |
|---|---|---|---|
| True scale-to-zero (idle = $0) | Yes | **No** — Fargate has no scale-to-zero and needs an ALB (~$16–20/month fixed even at zero traffic); App Runner *pauses* but still bills idle memory and carries a ~58% compute premium over Fargate | Yes |
| Free monthly grant | 180,000 vCPU-s / 360,000 GiB-s / 2M requests | None comparable at this idle-friendliness | 180,000–240,000 vCPU-s / 360,000–450,000 GiB-s / 2M requests |
| Cheapest *always-on* raw compute | Mid | **Cheapest** (Fargate ~$29.55/vCPU-month vs ~$63/vCPU-month for Cloud Run/Container Apps request-based) | Mid–high unless instance-based billing (always-warm) |
| Free managed TLS on custom domain | Yes | Requires ACM + ALB/CloudFront setup | Yes |
| Simplicity of deploy | Moderate (`az containerapp` CLI, Bicep/ARM) | More moving parts (ALB, target groups, task defs) unless using App Runner | Simplest — `gcloud run deploy` from a Dockerfile |
| Fits "demo until first customer" requirement | **Yes** | **Poor fit** without extra engineering (Lambda + container image + WSGI adapter to get true $0 idle) | **Best fit** |
| Ecosystem fit if you're already Microsoft-oriented (Key Vault, AD/SSO, enterprise sales) | **Best** | Neutral | Neutral |

### Bottom line
- **If cost + simplicity were the only factors:** Google Cloud Run is the purest match for "pay nothing until someone opens the demo" — genuinely $0 idle, no fixed floor anywhere in the stack, simplest deploy command.
- **AWS is the weakest fit for this specific requirement.** Fargate has no scale-to-zero and the ALB alone costs money sitting idle; App Runner reduces but doesn't eliminate idle cost. You'd need Lambda + a WSGI adapter (Mangum/serverless-wsgi) to get true $0-idle on AWS, which is extra rework for a Dash/Flask app that isn't built for that model.
- **Azure Container Apps is a very close second to Cloud Run** on the numbers — same scale-to-zero behavior, comparable free tier, free managed certs — and it's the right call for you specifically *because* you asked for Azure: if there's an enterprise/Microsoft-shop reason behind that (SSO expectations, procurement, compliance posture, Azure credits), Container Apps gets you there without giving up any of the cost benefits Cloud Run would have offered. You're not paying a meaningful premium to stay on Azure here.

**Recommendation:** stay on Azure Container Apps as planned. It's not the theoretical cheapest of the three, but it's close enough that the gap is noise at demo-stage traffic volumes, and it keeps you inside one ecosystem (Key Vault, Blob, Monitor, AD) for when compliance and enterprise-sales questions start mattering.

---

## 5. Deployment checklist

1. Write a production `Dockerfile` (multi-stage, slim base, gunicorn entrypoint, `/healthz` route).
2. Push code to GitHub; enable GHCR; add a GitHub Actions workflow that builds and pushes the image on merge to `main`.
3. Create a Container Apps **Environment** (Consumption plan) + the Container App itself, `minReplicas: 0`.
4. Point the app at your existing Neon `PG_DSN` via a Container Apps secret (or better, a Key Vault reference + managed identity).
5. Move `GROQ_API_KEY`, `JWT_SECRET`, `STRIPE_*`, `TWILIO_*`, `SENDGRID_*` into Key Vault; grant the Container App's managed identity `get` access.
6. Bind your custom domain, request the free managed certificate.
7. Turn on IP allow-listing for the demo period.
8. Set a budget alert in Azure Cost Management at, say, $15/month so you get a heads-up if something misbehaves (a bug causing infinite scaling, a crawler hammering the endpoint, etc.) — costs nothing to set up and is your safety net against the one scenario where "scale-to-zero" surprises you.
