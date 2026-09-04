# Liya Backend — Cloud Run Deployment Guide

## Prerequisites

| Tool | Install |
|---|---|
| `gcloud` CLI | https://cloud.google.com/sdk/docs/install |
| Docker Desktop | https://www.docker.com/products/docker-desktop |
| A GCP project | https://console.cloud.google.com |

---

## One-time GCP Setup (run once per project)

```bash
# 1. Set your project
gcloud config set project YOUR_PROJECT_ID

# 2. Enable required APIs
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  containerregistry.googleapis.com

# 3. Create Firestore database (Native mode, choose a region)
gcloud firestore databases create --location=us-central1

# 4. Store secrets in Secret Manager
echo -n "YOUR_LIYA_API_KEY"   | gcloud secrets create liya-api-key    --data-file=-
echo -n "YOUR_GEMINI_API_KEY" | gcloud secrets create gemini-api-key  --data-file=-

# Optional: a separate, revocable key to hand out to hackathon judges
# instead of your real liya-api-key — see JUDGE_TESTING.md.
echo -n "YOUR_JUDGE_KEY"      | gcloud secrets create liya-judge-key  --data-file=-

# 5. Grant Cloud Build access to secrets
PROJECT_NUMBER=$(gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)")
gcloud secrets add-iam-policy-binding liya-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# If you created liya-judge-key above, grant it access too, and add
# LIYA_JUDGE_KEY=liya-judge-key:latest to cloudbuild.yaml's --set-secrets line.
gcloud secrets add-iam-policy-binding liya-judge-key \
  --member="serviceAccount:${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

---

## Deploy (every release)

```bash
# From the project root:
gcloud builds submit --config cloudbuild.yaml \
  --substitutions _REGION=us-central1,_SERVICE=liya-backend \
  --project YOUR_PROJECT_ID
```

Cloud Build will:
1. Build the Docker image
2. Push it to Container Registry
3. Deploy to Cloud Run automatically

---

## Verify Deployment

```bash
# Get the deployed URL
SERVICE_URL=$(gcloud run services describe liya-backend \
  --region us-central1 --format "value(status.url)")

echo "Service URL: $SERVICE_URL"

# Health check (no auth needed)
curl "$SERVICE_URL/health"

# Status check
curl "$SERVICE_URL/status"

# Submit a task
curl -X POST "$SERVICE_URL/task" \
  -H "Content-Type: application/json" \
  -H "X-Liya-Key: YOUR_LIYA_API_KEY" \
  -d '{"goal": "Search for the weather in London and summarize it"}'

# Poll task status (replace TASK_ID with returned value)
curl "$SERVICE_URL/task/TASK_ID" \
  -H "X-Liya-Key: YOUR_LIYA_API_KEY"
```

---

## Local Testing (before deploying)

```bash
# Install deps
pip install -r requirements.txt

# Run server locally (no Docker needed)
LIYA_API_KEY=dev python backend/server.py

# In another terminal:
curl http://localhost:8080/health
curl http://localhost:8080/status
curl -X POST http://localhost:8080/task \
  -H "Content-Type: application/json" \
  -d '{"goal": "Search for AI news today"}'
```

---

## API Reference

| Method | Endpoint | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Liveness probe |
| GET | `/status` | None | Agent + Firestore health |
| POST | `/task` | X-Liya-Key | Submit a task |
| GET | `/task/{id}` | X-Liya-Key | Poll task status |
| GET | `/tasks` | X-Liya-Key | List tasks (`?history=true` for Firestore) |
| POST | `/memory/remember` | X-Liya-Key | Write memory entry |
| GET | `/memory` | X-Liya-Key | Read full memory |

Interactive API docs: `https://YOUR_SERVICE_URL/docs`

---

## Architecture

```
User / Hackathon Judge
        │
        ▼
  Cloud Run (liya-backend)
  ┌─────────────────────────────┐
  │  FastAPI (backend/server.py)│
  │  POST /task ──► TaskQueue   │
  │  GET  /task/{id} ◄── poll  │
  │  GET  /health  (liveness)  │
  │  GET  /status  (readiness) │
  └────────────┬────────────────┘
               │ read/write
               ▼
         Cloud Firestore
         ├── tasks/{task_id}
         └── users/default/memory/{category}
```
