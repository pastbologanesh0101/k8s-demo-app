"""Kubernetes Demo App: a small Flask key-value store service.

Designed to demonstrate real Kubernetes deployment concerns:
 - /health for liveness/readiness probes
 - /ready for readiness (can differ from liveness in real systems)
 - a real, stateful (in-memory) key-value API to give the probes and
   scaling behavior something meaningful to act on
 - configuration pulled from environment variables, matching how the
   ConfigMap/Secret in k8s/ are wired into the container
"""
from __future__ import annotations

import os
import socket
import time
from typing import Any

from flask import Flask, jsonify, request

app = Flask(__name__)

# In-memory data store. Each pod has its own copy -- this is intentional:
# it demonstrates why the Deployment's replicas are NOT interchangeable
# for stateful data (a real system would back this with Redis/Postgres).
_STORE: dict[str, Any] = {}

START_TIME = time.time()

# Configuration is read from environment variables so it can be supplied by
# the ConfigMap (APP_ENV, MAX_KEYS, GREETING) and Secret (API_TOKEN) defined
# in k8s/configmap.yaml and k8s/secret.yaml.
APP_ENV = os.environ.get("APP_ENV", "development")
MAX_KEYS = int(os.environ.get("MAX_KEYS", "1000"))
GREETING = os.environ.get("GREETING", "Hello from the Kubernetes Demo App")
API_TOKEN = os.environ.get("API_TOKEN", "")


@app.route("/health", methods=["GET"])
def health() -> Any:
    """Liveness probe target: process is up and can serve requests."""
    return (
        jsonify(
            {
                "status": "ok",
                "uptime_seconds": round(time.time() - START_TIME, 3),
                "hostname": socket.gethostname(),
            }
        ),
        200,
    )


@app.route("/ready", methods=["GET"])
def ready() -> Any:
    """Readiness probe target: distinct from /health on purpose.

    In a real service this would check downstream dependencies (DB
    connection, cache, etc). Here it reports whether the store is within
    its configured capacity, which is a genuine local invariant.
    """
    is_ready = len(_STORE) <= MAX_KEYS
    body = {"status": "ready" if is_ready else "overloaded", "keys": len(_STORE)}
    return jsonify(body), (200 if is_ready else 503)


@app.route("/", methods=["GET"])
def index() -> Any:
    return jsonify({"message": GREETING, "env": APP_ENV}), 200


@app.route("/api/kv/<key>", methods=["GET"])
def get_key(key: str) -> Any:
    if key not in _STORE:
        return jsonify({"error": f"key '{key}' not found"}), 404
    return jsonify({"key": key, "value": _STORE[key]}), 200


@app.route("/api/kv/<key>", methods=["PUT"])
def put_key(key: str) -> Any:
    if not request.is_json:
        return jsonify({"error": "request body must be JSON"}), 400

    payload = request.get_json(silent=True)
    if payload is None or "value" not in payload:
        return jsonify({"error": "JSON body must contain a 'value' field"}), 400

    if key not in _STORE and len(_STORE) >= MAX_KEYS:
        return jsonify({"error": "store is at capacity", "max_keys": MAX_KEYS}), 507

    _STORE[key] = payload["value"]
    return jsonify({"key": key, "value": _STORE[key]}), 200


@app.route("/api/kv/<key>", methods=["DELETE"])
def delete_key(key: str) -> Any:
    if key not in _STORE:
        return jsonify({"error": f"key '{key}' not found"}), 404
    del _STORE[key]
    return jsonify({"deleted": key}), 200


@app.route("/api/kv", methods=["GET"])
def list_keys() -> Any:
    return jsonify({"keys": sorted(_STORE.keys()), "count": len(_STORE)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
