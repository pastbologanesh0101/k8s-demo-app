"""Structural validation tests for the Kubernetes manifests in k8s/.

There is no live cluster available in this environment, so these tests do
NOT call `kubectl apply --dry-run` or talk to an API server. Instead they
parse each manifest with PyYAML and assert real structural invariants that
a live `kubectl apply` would also implicitly require or that are simply
good practice:
  - required fields exist for each kind (apiVersion/kind/metadata.name)
  - the Deployment's pod-template labels match its own selector
  - the Service's selector matches the Deployment's pod labels (so the
    Service actually routes to those pods)
  - resource requests <= limits for both cpu and memory
  - probes point at a real, existing endpoint path in the Flask app
  - the HPA's scaleTargetRef names the actual Deployment
  - the Ingress backend references the actual Service and a real port
  - the Secret is explicitly marked as a non-real placeholder
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

K8S_DIR = Path(__file__).resolve().parent.parent / "k8s"

# Endpoints that really exist in app/main.py -- kept in sync manually since
# there's no live cluster to verify probes against.
REAL_APP_ENDPOINTS = {"/health", "/ready", "/"}


def load(filename: str) -> dict:
    path = K8S_DIR / filename
    with path.open() as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def deployment():
    return load("deployment.yaml")


@pytest.fixture(scope="module")
def service():
    return load("service.yaml")


@pytest.fixture(scope="module")
def configmap():
    return load("configmap.yaml")


@pytest.fixture(scope="module")
def secret():
    return load("secret.yaml")


@pytest.fixture(scope="module")
def ingress():
    return load("ingress.yaml")


@pytest.fixture(scope="module")
def hpa():
    return load("hpa.yaml")


def test_all_manifests_parse_and_have_required_top_level_fields():
    for filename in [
        "deployment.yaml",
        "service.yaml",
        "configmap.yaml",
        "secret.yaml",
        "ingress.yaml",
        "hpa.yaml",
    ]:
        doc = load(filename)
        assert doc is not None, f"{filename} did not parse to a mapping"
        for field in ("apiVersion", "kind", "metadata"):
            assert field in doc, f"{filename} missing top-level '{field}'"
        assert "name" in doc["metadata"], f"{filename} missing metadata.name"


def test_deployment_selector_matches_pod_template_labels(deployment):
    selector_labels = deployment["spec"]["selector"]["matchLabels"]
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert selector_labels, "Deployment selector.matchLabels must not be empty"
    for key, value in selector_labels.items():
        assert pod_labels.get(key) == value, (
            f"Deployment selector key '{key}'={value!r} does not match pod "
            f"template label {pod_labels.get(key)!r}"
        )


def test_deployment_has_at_least_two_replicas(deployment):
    assert deployment["spec"]["replicas"] >= 2


def test_service_selector_matches_deployment_pod_labels(deployment, service):
    pod_labels = deployment["spec"]["template"]["metadata"]["labels"]
    svc_selector = service["spec"]["selector"]

    assert svc_selector, "Service selector must not be empty"
    for key, value in svc_selector.items():
        assert pod_labels.get(key) == value, (
            "Service selector does not match the Deployment's pod labels; "
            "the Service would not route traffic to these pods"
        )


def test_service_is_clusterip_and_ports_reference_named_container_port(
    deployment, service
):
    assert service["spec"]["type"] == "ClusterIP"

    container = deployment["spec"]["template"]["spec"]["containers"][0]
    container_port_names = {p["name"] for p in container.get("ports", [])}

    for port in service["spec"]["ports"]:
        assert port["targetPort"] in container_port_names, (
            f"Service targetPort '{port['targetPort']}' does not match any "
            f"named containerPort in the Deployment ({container_port_names})"
        )


def test_container_resource_requests_do_not_exceed_limits(deployment):
    def parse_cpu(v: str) -> float:
        # "100m" -> 0.1 cores, "1" -> 1 core
        return float(v[:-1]) / 1000 if v.endswith("m") else float(v)

    def parse_memory(v: str) -> float:
        units = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30}
        for suffix, factor in units.items():
            if v.endswith(suffix):
                return float(v[: -len(suffix)]) * factor
        return float(v)

    for container in deployment["spec"]["template"]["spec"]["containers"]:
        resources = container.get("resources")
        assert resources, f"container '{container['name']}' has no resources block"
        requests = resources.get("requests")
        limits = resources.get("limits")
        assert requests, f"container '{container['name']}' has no resource requests"
        assert limits, f"container '{container['name']}' has no resource limits"

        assert parse_cpu(requests["cpu"]) <= parse_cpu(limits["cpu"]), (
            "cpu request exceeds cpu limit"
        )
        assert parse_memory(requests["memory"]) <= parse_memory(limits["memory"]), (
            "memory request exceeds memory limit"
        )


def test_probes_target_real_application_endpoints(deployment):
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    readiness_path = container["readinessProbe"]["httpGet"]["path"]
    liveness_path = container["livenessProbe"]["httpGet"]["path"]

    assert readiness_path in REAL_APP_ENDPOINTS, (
        f"readinessProbe path '{readiness_path}' is not a real app endpoint"
    )
    assert liveness_path in REAL_APP_ENDPOINTS, (
        f"livenessProbe path '{liveness_path}' is not a real app endpoint"
    )
    # Readiness and liveness intentionally check different things.
    assert readiness_path != liveness_path


def test_deployment_pulls_config_and_secret_via_envfrom(deployment, configmap, secret):
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    env_from = container.get("envFrom", [])

    configmap_names = {
        item["configMapRef"]["name"] for item in env_from if "configMapRef" in item
    }
    secret_names = {
        item["secretRef"]["name"] for item in env_from if "secretRef" in item
    }

    assert configmap["metadata"]["name"] in configmap_names
    assert secret["metadata"]["name"] in secret_names


def test_secret_is_clearly_marked_as_a_placeholder_template():
    raw_text = (K8S_DIR / "secret.yaml").read_text().lower()
    assert "not real" in raw_text or "placeholder" in raw_text, (
        "secret.yaml must clearly mark itself as a non-real template, not "
        "actual credentials"
    )
    assert "template" in raw_text or "example" in raw_text


def test_hpa_references_the_deployment_by_name(deployment, hpa):
    ref = hpa["spec"]["scaleTargetRef"]
    assert ref["kind"] == "Deployment"
    assert ref["name"] == deployment["metadata"]["name"]
    assert hpa["spec"]["minReplicas"] <= hpa["spec"]["maxReplicas"]


def test_ingress_backend_references_the_real_service_and_port(service, ingress):
    service_name = service["metadata"]["name"]
    service_port_numbers = {p["port"] for p in service["spec"]["ports"]}

    rule = ingress["spec"]["rules"][0]
    path = rule["http"]["paths"][0]
    backend_service = path["backend"]["service"]

    assert backend_service["name"] == service_name
    assert backend_service["port"]["number"] in service_port_numbers


def test_all_resources_share_a_consistent_app_label(
    deployment, service, configmap, secret, ingress, hpa
):
    app_name = deployment["metadata"]["labels"]["app"]
    for doc in (deployment, service, configmap, secret, ingress, hpa):
        assert doc["metadata"]["labels"]["app"] == app_name
