# Observability

This stack is backend-only. It is independent from the Angular frontend.

## Included

- Prometheus scraping `http://host.docker.internal:8000/health/metrics/`
- Grafana with a pre-provisioned `PushIT Backend Overview` dashboard
- Alert rules for:
  - API target down
  - high 5xx rate
  - notification failed/partial spikes
  - app-token auth anomaly spikes

## Run

Start the Django API on port `8000`, then run:

```bash
docker compose -f docker-compose.observability.yml up -d
```

Access:

- Prometheus: `http://localhost:9090`
- Grafana: `http://localhost:3001`
  - user: `admin`
  - password: `admin`

## Metrics protection

If `METRICS_AUTH_TOKEN` is set in Django, `/health/metrics/` requires header `X-Metrics-Token`.

The provided Prometheus config assumes the metrics endpoint is reachable without that token on an internal trusted network.
If you want to protect it in the same setup, place Prometheus on the same private network path or add an auth proxy in front of Django.
