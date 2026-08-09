import json
from pathlib import Path

import pytest
import schemathesis
import yaml
from hypothesis import HealthCheck, settings
from schemathesis.specs.openapi.checks import positive_data_acceptance

from app.main import app

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "docs" / "openapi.yaml"

schema = schemathesis.openapi.from_asgi("/api/v1/openapi.json", app)


@schema.parametrize()
@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
def test_api_conforms_to_its_openapi_schema(case):
    case.call_and_validate(excluded_checks=[positive_data_acceptance])


def test_exported_specification_is_up_to_date():
    if not SPEC_PATH.exists():
        pytest.fail(f"{SPEC_PATH} is missing, run `make openapi`")

    exported = yaml.safe_load(SPEC_PATH.read_text(encoding="utf-8"))
    current = json.loads(json.dumps(app.openapi()))
    assert exported == current, "docs/openapi.yaml is stale, run `make openapi`"


def test_specification_documents_every_error_code():
    spec = app.openapi()
    operation = spec["paths"]["/api/v1/isochrone"]["post"]
    for status in ("400", "422", "429", "500", "503", "504"):
        assert status in operation["responses"]
        assert "application/problem+json" in operation["responses"][status]["content"]


def test_no_schema_is_left_undocumented():
    spec = app.openapi()
    for name, definition in spec["components"]["schemas"].items():
        properties = definition.get("properties", {})
        if not properties:
            continue
        undocumented = [
            field
            for field, meta in properties.items()
            if not meta.get("description")
            and not meta.get("allOf")
            and not meta.get("$ref")
            and "anyOf" not in meta
        ]
        assert not undocumented, f"{name} has undocumented fields: {undocumented}"
