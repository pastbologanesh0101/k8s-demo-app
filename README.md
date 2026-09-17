# Kubernetes Demo App

A small, genuinely functional Flask key-value store service, packaged with a
complete set of Kubernetes manifests (`k8s/`) demonstrating common
production practices: resource requests/limits, liveness/readiness probes,
multiple replicas, ConfigMap/Secret-based configuration, a ClusterIP
Service, an Ingress, and a HorizontalPodAutoscaler.

## Important: how this project was validated (read this first)

**No live Kubernetes cluster was available while building this project.**
`kubectl` was installed but not connected to any cluster, so
`kubectl apply --dry-run=client` could not be used (it needs to fetch the
OpenAPI schema from a live API server).

To be honest about what "tested" means here:

- The **manifests in `k8s/`** are correct by careful, deliberate construction
  and by **structural validation** in Python (`tests/test_manifests.py`),
  not by testing against a real cluster. Those tests parse every manifest
  with PyYAML and assert real invariants: the Deployment's label selector
  matches its pod template labels, the Service's selector matches the
  Deployment's pod labels (so it would actually route traffic), resource
  requests never exceed limits, the probes point at endpoints that really
  exist in the Flask app, the HPA's `scaleTargetRef` names the actual
  Deployment, and the Ingress backend references the actual Service and
  port.
- The **Flask application itself** is fully real and is tested directly and
  independently of Kubernetes, using Flask's test client
  (`tests/test_app.py`), with no mocking of its own logic.
- To actually deploy this, run it against a real cluster:
  ```
  kubectl apply -f k8s/
  ```
  on minikube, kind, EKS, GKE, or any other real Kubernetes cluster.

## Architecture

```
                    ┌─────────────┐
   Client  ───────▶ │   Ingress   │  (host-based routing)
                    └──────┬──────┘
                           │
                    ┌──────▼──────┐
                    │   Service   │  (ClusterIP, port 80 -> 8080)
                    └──────┬──────┘
                           │
              ┌────────────┴────────────┐
              │                         │
        ┌─────▼─────┐             ┌─────▼─────┐
        │  Pod (app) │   ...      │  Pod (app) │   <- Deployment, 2+ replicas,
        │  Flask +   │            │  Flask +   │      scaled by the HPA based
        │  gunicorn  │            │  gunicorn  │      on CPU/memory utilization
        └─────┬──────┘            └─────┬──────┘
              │                         │
     env vars from ConfigMap + Secret (APP_ENV, MAX_KEYS, GREETING, API_TOKEN)
```

The app itself is a simple in-memory key-value store. Each pod's store is
independent (no shared backing datastore) -- this is intentional, to make
the multi-replica/scaling behavior visible and to be upfront that a real
stateful service would need a shared backend (Redis, Postgres, etc.)
instead of in-process memory.

### Endpoints

| Method | Path            | Description                                   |
|--------|-----------------|------------------------------------------------|
| GET    | `/`             | Greeting + current environment                |
| GET    | `/health`       | Liveness probe target                          |
| GET    | `/ready`        | Readiness probe target (checks store capacity) |
| GET    | `/api/kv`       | List all keys                                  |
| GET    | `/api/kv/<key>` | Get a value                                    |
| PUT    | `/api/kv/<key>` | Set a value (`{"value": ...}` JSON body)       |
| DELETE | `/api/kv/<key>` | Delete a key                                   |

## Running the app locally (development, no Docker/Kubernetes needed)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main          # serves on http://localhost:8080
```

Try it:

```bash
curl http://localhost:8080/health
curl -X PUT http://localhost:8080/api/kv/color -H 'Content-Type: application/json' -d '{"value": "blue"}'
curl http://localhost:8080/api/kv/color
```

## Running the app in Docker

```bash
docker build -t k8s-demo-app:latest .
docker run -p 8080:8080 k8s-demo-app:latest
```

## Deploying to a real Kubernetes cluster

This has **not** been run against a live cluster in this environment, but
the manifests are ready to use:

```bash
# 1. Build the image and make it available to your cluster.
docker build -t k8s-demo-app:latest .
# For kind:   kind load docker-image k8s-demo-app:latest
# For minikube: eval $(minikube docker-env) && docker build -t k8s-demo-app:latest .

# 2. Apply all manifests.
kubectl apply -f k8s/

# 3. Check status.
kubectl get pods,svc,deploy,hpa -l app=k8s-demo-app

# 4. Port-forward to try it without an Ingress controller.
kubectl port-forward svc/k8s-demo-app 8080:80
curl http://localhost:8080/health
```

To use the Ingress example, you need an ingress controller installed
(e.g. ingress-nginx) and `k8s-demo-app.example.com` resolving to it (or use
`curl --resolve` / edit `/etc/hosts` for local testing).

The Secret in `k8s/secret.yaml` is a **placeholder template**, not a real
credential — see the comments in that file. In a real deployment, create it
with `kubectl create secret generic ...` or a secrets manager instead of
committing it to git.

## Running the tests

```bash
pip install -r requirements.txt
pytest -v                        # runs both suites
pytest tests/test_app.py -v      # Flask app logic only
pytest tests/test_manifests.py -v  # k8s manifest structural validation only
```

Both suites also run in CI on every push/PR via
`.github/workflows/tests.yml`, against Python 3.11 and 3.12 — no live
cluster required.

## Repository layout

```
app/               Flask application (real logic, tested via Flask test client)
k8s/               Kubernetes manifests: Deployment, Service, ConfigMap,
                   Secret (placeholder), Ingress, HorizontalPodAutoscaler
tests/test_app.py        Unit tests for the app's own logic
tests/test_manifests.py  Structural validation tests for the k8s manifests
.github/workflows/tests.yml  CI: runs both suites on Python 3.11 and 3.12
Dockerfile         Production-shaped container image (gunicorn, non-root user)
```

## License

MIT — see [LICENSE](LICENSE).
