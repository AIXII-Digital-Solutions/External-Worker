# external-worker

ARQ worker for all external-API interaction (FlightRadar, Aviation Edge, Airlabs,
Microsoft Graph) and the scheduled domain jobs (ARQ cron). Receives on-demand tasks
from core-api over Redis (`core:external`) and publishes task status to the shared
`job_statuses` table + Redis `status:events`.

## Layout
```
worker/   # Config, Database* (generated), Schemas, Utils, Queue.py, API/, Scheduler/, main.py, tasks.py, status.py
Dockerfile, docker-compose.yml, entrypoint.sh, .env.example
```
`worker/Database` is a GENERATED copy from core-api's `db-contract` (do not edit).

## Run (Docker)
```bash
cp .env.example .env           # fill DB_*/REDIS_* + MS_*/AIRLABS_*/FLIGHT_RADAR_*/AVIATION_EDGE_*
docker compose up -d --build
```
- Use the **same Redis** as core-api (ARQ broker).
- **Scheduler:** set `SCHEDULER_ENABLED=true` on **exactly one** instance — otherwise
  the ARQ cron jobs run multiple times.

## Updating models
Models are owned by core-api. Change them there, run `python db-contract/sync.py`, commit.
