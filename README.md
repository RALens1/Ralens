# R.A.Lens — Production-style competition MVP

## Included
- Animated project logo in the UI
- Responsive mobile/desktop interface
- Nginx frontend
- FastAPI backend with 4 workers
- PostgreSQL persistent regulatory database
- Dedicated daily collection worker
- Official-source connectors for FDA/USFDA, CDSCO, EMA and MHRA
- AI impact assessment through OpenAI Responses API
- Evidence-linked analysis
- Product/submission context
- Feedback form
- No judge Q&A / competition notes in the website

## Daily data collection
The `worker` container runs a collection cycle and sleeps for 24 hours. It is a single worker, so multiple API replicas do not duplicate scheduled jobs.

## 1,000-user target
The architecture separates static frontend, API workers and database. Four FastAPI workers are configured in the starter deployment. For a real public launch, scale API replicas horizontally and use managed PostgreSQL/load balancing. Actual 1,000 concurrent-user capacity must be load-tested on the selected server size; this package does not make a guarantee.

## Start locally
1. Install Docker Desktop.
2. Copy `.env.example` to `.env`.
3. Put your OpenAI API key in `.env`.
4. Change the database/admin passwords.
5. Run `docker compose up --build`.
6. Open `http://localhost`.

## First collection
The worker automatically collects once when it starts, then every 24 hours.
For an immediate collection, call the protected endpoint:
`POST /api/admin/collect` with header/query value `x_admin_key=<ADMIN_KEY>`.

## Important regulatory/data note
R.A.Lens uses authoritative public sources as evidence. It does not claim that all regulatory information is legally binding. FDA itself distinguishes guidance documents from binding laws/regulations. Final regulatory interpretation remains with qualified professionals.

## Feedback
Customer feedback is stored in PostgreSQL. Add an email/CRM integration later if desired.

## Production hardening still required before commercial launch
- HTTPS/TLS
- real domain
- managed PostgreSQL backups
- secret manager
- authentication/authorization
- rate limiting/WAF
- monitoring/logging
- privacy policy and retention rules
- load testing
- source parser tests and change/version tracking
- enterprise tenant isolation
- email/digest delivery provider
