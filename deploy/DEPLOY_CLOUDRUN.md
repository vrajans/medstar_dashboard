# InsightHub — Google Cloud Run Deployment (leadership demo)

Deploys the three services + keeps your **Neon** database:

```
Next.js customer app  ─┐
FastAPI backend        ├─ Cloud Run (containers, HTTPS, autoscale, scale-to-zero)
Dash admin app        ─┘
Neon Postgres  ← unchanged (your existing DB)
```

Commands are **PowerShell** (Windows). Run from the repo root (`C:\Projects\medstar_dashboard`)
unless noted. Replace the placeholders in Step 1.

---

## 0. One-time prerequisites
- Install the **gcloud CLI**: https://cloud.google.com/sdk/docs/install → then `gcloud init`.
- A GCP project with **billing enabled**.
- Your code pushed to GitHub (already done).
- Have your `.env` values handy (PG_DSN, PG_DSN_SYNC, GROQ_API_KEY, JWT_SECRET, FLASK_SECRET_KEY).

## 1. Set variables (edit these)
```powershell
$PROJECT = "your-gcp-project-id"
$REGION  = "us-central1"
$REPO    = "insighthub"
gcloud config set project $PROJECT
```

## 2. Enable APIs + create the image repository
```powershell
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com
gcloud artifacts repositories create $REPO --repository-format=docker --location=$REGION `
  --description="InsightHub images"
```

## 3. Build + deploy the FastAPI backend  →  get its URL
```powershell
# build image
gcloud builds submit --config deploy/cb-api.yaml `
  --substitutions=_IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/api:latest" .

# deploy (env vars inline for the demo; use Secret Manager for production — see Notes)
gcloud run deploy ih-api `
  --image "$REGION-docker.pkg.dev/$PROJECT/$REPO/api:latest" `
  --region $REGION --allow-unauthenticated --min-instances=1 --memory=1Gi `
  --set-env-vars "PG_DSN=PASTE_PG_DSN,GROQ_API_KEY=PASTE_GROQ_KEY,JWT_SECRET=PASTE_JWT_SECRET"

# capture the URL
$API_URL = gcloud run services describe ih-api --region $REGION --format="value(status.url)"
$API_URL
```
> `--min-instances=1` keeps it warm so there is **no cold start during the demo**.
> Note: `PG_DSN` is the **asyncpg** DSN, e.g. `postgresql+asyncpg://user:pw@host/db?ssl=require`.

## 4. Build + deploy the Dash admin  →  point it at the API
```powershell
gcloud builds submit --config deploy/cb-dash.yaml `
  --substitutions=_IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/dash:latest" .

gcloud run deploy ih-dash `
  --image "$REGION-docker.pkg.dev/$PROJECT/$REPO/dash:latest" `
  --region $REGION --allow-unauthenticated --min-instances=1 --memory=1Gi `
  --set-env-vars "FASTAPI_BASE_URL=$API_URL,GROQ_API_KEY=PASTE_GROQ_KEY,FLASK_SECRET_KEY=PASTE_SECRET,DASH_DEBUG=false,ADMIN_PASSWORD=PASTE_ADMIN_PW,VIEWER_PASSWORD=PASTE_VIEWER_PW,PG_DSN_SYNC=PASTE_PG_DSN_SYNC"

$DASH_URL = gcloud run services describe ih-dash --region $REGION --format="value(status.url)"
$DASH_URL
```

## 5. Build + deploy the Next.js customer app  (API URL baked in at build)
```powershell
gcloud builds submit --config deploy/cb-app.yaml `
  --substitutions=_IMAGE="$REGION-docker.pkg.dev/$PROJECT/$REPO/app:latest",_API_URL="$API_URL" `
  customer_app

gcloud run deploy ih-app `
  --image "$REGION-docker.pkg.dev/$PROJECT/$REPO/app:latest" `
  --region $REGION --allow-unauthenticated --min-instances=1 --memory=512Mi

$APP_URL = gcloud run services describe ih-app --region $REGION --format="value(status.url)"
$APP_URL
```

## 6. Let the API accept the deployed front-ends (CORS)
```powershell
# custom delimiter (^##^) because the value contains a comma
gcloud run services update ih-api --region $REGION `
  --set-env-vars "^##^CORS_ORIGINS=$APP_URL,$DASH_URL"
```

## 7. Seed / confirm the admin login (once)
Your Neon DB already has users. If you need a known admin for the Dash app or a
customer login, run these **locally** (they act on the same Neon DB):
```powershell
python -m api.create_admin                       # admin / admin123 in Neon
python -m api.create_user cognizant Demo@2026 <tenantId>   # a customer login
```
(Or use the Dash admin UI now that it's deployed.)

---

## You're live
- **Customer app:** `$APP_URL`  (share this for the demo)
- **Admin portal:** `$DASH_URL`
- **API / Swagger:** `$API_URL/docs`

**Demo flow:** open the admin portal → Tenants → create a Healthcare tenant → Users →
"Create Customer Login" → open the customer app URL → sign in → Payment Integrity →
upload `demo_claims.csv`.

---

## Cost (with `--min-instances=1` on all three for the demo)
Roughly **$25–45/month** total while kept warm 24/7. Drop `--min-instances` to `0`
after the demo and it costs **~$0** at idle (accepting a cold start on first hit).
Neon stays on its free tier. Scale is automatic — Cloud Run adds instances under load.

## Notes / production hardening
- **Secrets:** for the demo, env vars inline are fine. For production, move
  `GROQ_API_KEY`, `PG_DSN`, `JWT_SECRET`, `FLASK_SECRET_KEY` into **Secret Manager**
  and reference with `--set-secrets` instead of `--set-env-vars`.
- **HIPAA / real PHI:** Cloud Run is HIPAA-eligible **under a signed BAA with Google**
  and with PHI kept encrypted + access-controlled. The demo uses synthetic claims, so
  no BAA is needed now — but sign the BAA and lock down `--no-allow-unauthenticated` /
  IAP before any real member data flows.
- **Custom domains:** `gcloud run domain-mappings create --service ih-app --domain app.yourdomain.com`.
- **CI/CD from GitHub:** connect the repo in Cloud Build → Triggers, using these same
  `deploy/cb-*.yaml` configs, so every push auto-builds and deploys.
- **Dash data:** the Dash app's own SQLite is ephemeral on Cloud Run (fine — tenant/user
  and customer data live in Neon). Its MedStar demo analytics reset on redeploy.
