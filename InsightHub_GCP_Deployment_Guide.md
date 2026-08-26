# InsightHub — Google Cloud (Cloud Run) End-to-End Deployment Guide

*Prepared July 2026. Commands verified against current `gcloud` / Cloud Run docs. Replace `PROJECT_ID`, `REGION`, `SERVICE_NAME`, and domain values with your own throughout.*

This walks through taking InsightHub (Dash + Flask + SQLAlchemy, Neon Postgres backend) from zero to a live, secure, scale-to-zero Cloud Run deployment, then wiring up CI/CD.

Suggested values used below — swap for your own:
- `PROJECT_ID` = `insighthub-prod`
- `REGION` = `us-central1`
- `SERVICE_NAME` = `insighthub`
- `REPO` = `insighthub-repo`

---

## Step 0 — Prerequisites

- A Google account and a GCP project with billing enabled (billing must be attached even to stay in the free tier — nothing is charged until you exceed it).
- `gcloud` CLI installed locally, or use **Cloud Shell** in the browser (has `gcloud`, `docker`, and `git` preinstalled — fastest way to do this without installing anything).
- Your InsightHub repo, and a Neon Postgres connection string already working locally (you have this in `.env` as `PG_DSN`).
- Docker installed locally if you're not using Cloud Shell / `gcloud run deploy --source`.

```bash
gcloud auth login
gcloud config set project PROJECT_ID
```

---

## Step 1 — Enable the APIs you need

```bash
gcloud services enable \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com
```

---

## Step 2 — Containerize InsightHub

Add a production `Dockerfile` at the repo root (this replaces running `python app.py` directly — use `gunicorn` in production):

```dockerfile
# ---- build stage ----
FROM python:3.11-slim AS base

WORKDIR /app

# System deps (only what's needed to build wheels, e.g. psycopg2)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn

COPY . .

# Cloud Run injects $PORT — must listen on it, not a hardcoded port
ENV PORT=8080
EXPOSE 8080

# app:server assumes Flask's underlying server object is exposed as `server`
# in app.py (standard Dash pattern: server = app.server)
CMD exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 0 app:server
```

Notes specific to your app:
- Dash exposes the underlying Flask instance as `app.server` — make sure `app.py` has `server = app.server` (or equivalent) as the gunicorn entrypoint target. If your entrypoint variable is named differently, adjust `app:server` accordingly.
- **Cloud Run requires the container to listen on the `$PORT` env var it injects** (defaults to 8080) — don't hardcode 8050 in production.
- Nothing in the container filesystem should be assumed to persist across requests/restarts — Cloud Run instances are ephemeral and can be recycled at any time. Uploaded files / generated exports should go to Cloud Storage, not local disk, if they need to survive.
- Add a cheap `/healthz` route in Flask returning `200 OK` — not strictly required by Cloud Run (it uses TCP startup probing by default) but useful for your own monitoring.

Test it locally before deploying:
```bash
docker build -t insighthub-local .
docker run -p 8080:8080 --env-file .env insighthub-local
# visit http://localhost:8080
```

---

## Step 3 — Create an Artifact Registry repository

```bash
gcloud artifacts repositories create REPO \
  --repository-format=docker \
  --location=REGION \
  --description="InsightHub container images"

gcloud auth configure-docker REGION-docker.pkg.dev
```

Free tier: 0.5 GB storage/month free, then $0.10/GB — a handful of image versions will stay free indefinitely at this scale.

---

## Step 4 — Store secrets in Secret Manager (don't ship them in the image or as plain env vars)

```bash
echo -n "gsk_xxx..."           | gcloud secrets create groq-api-key      --data-file=-
echo -n "postgresql://..."     | gcloud secrets create pg-dsn            --data-file=-
echo -n "your-jwt-secret"      | gcloud secrets create jwt-secret        --data-file=-
echo -n "SG.xxx..."            | gcloud secrets create sendgrid-api-key  --data-file=-
echo -n "ACxxx..."             | gcloud secrets create twilio-sid        --data-file=-
echo -n "your-twilio-token"    | gcloud secrets create twilio-token      --data-file=-
echo -n "sk_test_..."          | gcloud secrets create stripe-secret-key --data-file=-
```

Free tier: 6 active secret versions + 10,000 access operations/month free. At demo scale this stays free.

You'll grant the Cloud Run service's runtime service account access to these in Step 6.

---

## Step 5 — Build and deploy to Cloud Run

Easiest path — build from source directly, Cloud Build handles the image build for you:

```bash
gcloud run deploy SERVICE_NAME \
  --source . \
  --region REGION \
  --platform managed \
  --allow-unauthenticated \
  --min-instances=0 \
  --max-instances=3 \
  --cpu=1 \
  --memory=1Gi \
  --timeout=60 \
  --set-secrets="GROQ_API_KEY=groq-api-key:latest,PG_DSN=pg-dsn:latest,JWT_SECRET=jwt-secret:latest,SENDGRID_API_KEY=sendgrid-api-key:latest,TWILIO_ACCOUNT_SID=twilio-sid:latest,TWILIO_AUTH_TOKEN=twilio-token:latest,STRIPE_SECRET_KEY=stripe-secret-key:latest" \
  --set-env-vars="APP_NAME=InsightHub,PORT=8080"
```

Or, if you already built and pushed the image manually in Step 3/4:

```bash
docker build -t REGION-docker.pkg.dev/PROJECT_ID/REPO/SERVICE_NAME:latest .
docker push REGION-docker.pkg.dev/PROJECT_ID/REPO/SERVICE_NAME:latest

gcloud run deploy SERVICE_NAME \
  --image=REGION-docker.pkg.dev/PROJECT_ID/REPO/SERVICE_NAME:latest \
  --region REGION \
  --min-instances=0 --max-instances=3 --cpu=1 --memory=1Gi \
  --set-secrets="GROQ_API_KEY=groq-api-key:latest,PG_DSN=pg-dsn:latest,..." \
  --allow-unauthenticated
```

Key flags explained:
- `--min-instances=0` — this is what makes the demo phase free. No traffic, no bill.
- `--allow-unauthenticated` — public HTTPS access (required for a customer-facing demo). Swap to IAM-authenticated invocation later if you want to lock the URL down to specific Google accounts during private demos (see Step 9).
- `--cpu=1 --memory=1Gi` — right-size after your first real usage; Dash callback processing + pandas aggregation is the heaviest part of your request cycle, 1 vCPU/1GiB is a safe starting point.
- `--timeout=60` — bump if large Excel/CSV uploads take longer to parse; Cloud Run allows up to 3600s.

Cloud Run gives you a `*.run.app` HTTPS URL immediately — the deploy output prints it. That's your working demo URL before you even touch a custom domain.

---

## Step 6 — Grant the runtime service account access to secrets

```bash
PROJECT_NUMBER=$(gcloud projects describe PROJECT_ID --format="value(projectNumber)")
SERVICE_ACCOUNT="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

for SECRET in groq-api-key pg-dsn jwt-secret sendgrid-api-key twilio-sid twilio-token stripe-secret-key; do
  gcloud secrets add-iam-policy-binding $SECRET \
    --member="serviceAccount:${SERVICE_ACCOUNT}" \
    --role="roles/secretmanager.secretAccessor"
done
```

Better practice: create a **dedicated** service account for the Cloud Run service instead of using the default compute service account, and grant it only what it needs (secret access + nothing else). This matters more once you have real customer data flowing through:

```bash
gcloud iam service-accounts create insighthub-run \
  --display-name="InsightHub Cloud Run runtime"

gcloud run services update SERVICE_NAME \
  --region REGION \
  --service-account="insighthub-run@PROJECT_ID.iam.gserviceaccount.com"
```

Then repeat the secret-binding loop against `insighthub-run@PROJECT_ID.iam.gserviceaccount.com` instead of the default compute SA, and revoke the default SA's access.

---

## Step 7 — Custom domain + managed SSL

```bash
gcloud run domain-mappings create \
  --service=SERVICE_NAME \
  --domain=app.insighthub.ai \
  --region=REGION
```

This prints the DNS records to add at your registrar (typically a `CNAME` for a subdomain, or `A`/`AAAA` records for an apex domain). Add them, then wait — Google provisions and auto-renews the managed TLS certificate, usually within 15 minutes, occasionally up to 24 hours.

Note: domain mappings are region-limited and best suited for non-production/early-stage use, which fits your current phase exactly. If you later need multi-region failover or Cloud CDN in front of it, migrate to a Global External Application Load Balancer + serverless NEG — that's a Step-11-later problem, not a now problem.

---

## Step 8 — Lock the demo down (optional but cheap and worth doing)

While you're only showing this to specific prospects, you don't need it wide open to the entire internet:

```bash
# Require Google-account auth instead of public access
gcloud run services remove-iam-policy-binding SERVICE_NAME \
  --region REGION \
  --member="allUsers" --role="roles/run.invoker"

# Grant specific people access
gcloud run services add-iam-policy-binding SERVICE_NAME \
  --region REGION \
  --member="user:prospect@theircompany.com" --role="roles/run.invoker"
```

If you'd rather keep it a plain public URL (simpler for prospects to click without a Google-account prompt), instead just keep `--allow-unauthenticated` and rely on your app's own login screen — InsightHub already has RBAC and auth built in, so this is a reasonable choice too. Either way, this costs nothing either direction — pick based on demo friction, not cost.

---

## Step 9 — CI/CD with GitHub Actions

Add `.github/workflows/deploy.yml`:

```yaml
name: Deploy to Cloud Run

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      id-token: write   # for workload identity federation, no long-lived key needed

    steps:
      - uses: actions/checkout@v4

      - id: auth
        uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: "projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github-pool/providers/github-provider"
          service_account: "insighthub-deployer@PROJECT_ID.iam.gserviceaccount.com"

      - uses: google-github-actions/deploy-cloudrun@v2
        with:
          service: SERVICE_NAME
          region: REGION
          source: .
```

Use **Workload Identity Federation** (shown above) rather than downloading a JSON service-account key into a GitHub secret — it avoids a long-lived credential sitting in your repo secrets entirely. Setup is a one-time `gcloud iam workload-identity-pools create` + provider + binding to a deploy-only service account scoped to `roles/run.developer` and `roles/artifactregistry.writer` — worth the 10 minutes versus a static key that has to be rotated manually forever.

Every merge to `main` now rebuilds and redeploys automatically, and because `min-instances=0`, a bad deploy that nobody visits costs nothing while you notice and roll back.

---

## Step 10 — Observability and a cost safety net

```bash
# Structured logs are automatic — view them:
gcloud run services logs read SERVICE_NAME --region REGION --limit=50

# Set a budget alert so a runaway loop or crawler doesn't surprise you
gcloud billing budgets create \
  --billing-account=BILLING_ACCOUNT_ID \
  --display-name="InsightHub demo budget" \
  --budget-amount=15 \
  --threshold-rule=percent=0.5 \
  --threshold-rule=percent=1.0
```

Cloud Logging's free tier (50 GiB/month ingestion, more generous than Azure's 5 GB) comfortably covers demo-stage traffic. The budget alert is your only real risk mitigation against "scale-to-zero" surprising you — set it and forget it.

---

## Step 11 — Database: keep Neon

Don't migrate off Neon for the demo phase. Your `PG_DSN` already points at it, it auto-suspends after 5 minutes idle (matches Cloud Run's own scale-to-zero behavior), and its free tier (0.5 GB storage, 100 compute-hours/month) will absorb demo traffic entirely. Moving to **Cloud SQL for PostgreSQL** means paying for an always-on instance (even the smallest shared-core tier runs continuously) — that reintroduces exactly the fixed idle cost you're trying to avoid. Revisit this only once a paying customer needs guaranteed low-latency co-located database access or you need it inside a VPC for compliance reasons.

---

## Cost summary — demo phase

| Service | Free tier covers | Expected monthly cost |
|---|---|---|
| Cloud Run (min-instances=0) | 2M requests, 180k vCPU-s, 360k GiB-s/month | **$0** |
| Artifact Registry | 0.5 GB storage | **$0** |
| Secret Manager | 6 active secrets, 10k accesses/month | **$0** |
| Cloud Logging | 50 GiB ingestion/month | **$0** |
| Neon Postgres | 0.5 GB storage, 100 compute-hrs/month | **$0** |
| Domain mapping + managed TLS | n/a | **$0** |
| **Total** | | **$0–5/month** (only egress past 1 GiB/month or overflow past free grants bills anything) |

---

## What changes after your first paying customer

1. `gcloud run services update SERVICE_NAME --min-instances=1` — removes cold-start latency; expect roughly **$35–50/month** for one small always-warm instance (1 vCPU / 1 GiB running continuously).
2. Add a proper autoscaling `--max-instances` ceiling based on real concurrency, and consider `--concurrency` tuning (Cloud Run defaults to 80 concurrent requests/instance — Dash callback processing is CPU-bound, so you may want to lower this).
3. Move Neon free → Neon paid (metered, ~$0.106/CU-hr + $0.35/GB storage) or Cloud SQL if you need VPC-private connectivity.
4. Migrate the domain mapping to a Global External Application Load Balancer + Cloud CDN if you want edge caching or multi-region.
5. Add **Cloud Armor** (WAF/DDoS rules) in front of the load balancer once there's real traffic and real risk to defend against — this has its own cost floor, so it's a "now you have revenue to justify it" step, not a day-one one.
6. Switch the service account back to fully private (`roles/run.invoker` limited to your own backend services) if you introduce internal-only microservices alongside the public-facing app.

Nothing above requires re-containerizing or re-architecting — it's the same image and the same Cloud Run service, just with different scaling and access knobs turned once revenue justifies the always-on cost.
