"""Tests for webhooks preprocessor."""
from __future__ import annotations

from typing import Any

import pytest

from amil_utils.preprocessors.webhooks import (
    _process_webhook_patterns,
    _build_webhook_endpoint_model,
    _build_event_payload_spec,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_spec(
    models: list[dict[str, Any]] | None = None,
    module_name: str = "test_mod",
    **overrides: Any,
) -> dict[str, Any]:
    """Build a minimal spec dict for webhook testing."""
    base: dict[str, Any] = {
        "module_name": module_name,
        "depends": ["base"],
        "models": models or [],
    }
    base.update(overrides)
    return base


def _make_model(
    name: str = "test.model",
    webhooks: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Build a minimal model dict."""
    model: dict[str, Any] = {
        "name": name,
        "description": name.replace(".", " ").title(),
        "fields": [
            {"name": "name", "type": "Char", "required": True},
            {"name": "value", "type": "Integer"},
        ],
        "override_sources": {},
        **kwargs,
    }
    if webhooks is not None:
        model["webhooks"] = webhooks
    return model


# ---------------------------------------------------------------------------
# Tests: _build_webhook_endpoint_model
# ---------------------------------------------------------------------------


class TestBuildWebhookEndpointModel:
    def test_model_name(self) -> None:
        model = _build_webhook_endpoint_model("test_mod")
        assert model["name"] == "webhook.endpoint"

    def test_synthesized_flag(self) -> None:
        model = _build_webhook_endpoint_model("test_mod")
        assert model["_synthesized"] is True
        assert model["_is_webhook_endpoint"] is True

    def test_has_required_fields(self) -> None:
        model = _build_webhook_endpoint_model("test_mod")
        names = [f["name"] for f in model["fields"]]
        assert "name" in names
        assert "url" in names
        assert "secret_token" in names
        assert "events" in names
        assert "target_model" in names
        assert "max_retries" in names

    def test_has_unique_constraint(self) -> None:
        model = _build_webhook_endpoint_model("test_mod")
        constraint_names = [c["name"] for c in model.get("sql_constraints", [])]
        assert "unique_url_model" in constraint_names

    def test_has_security_acl(self) -> None:
        model = _build_webhook_endpoint_model("test_mod")
        assert len(model["security_acl"]) > 0
        assert model["security_acl"][0]["role"] == "manager"


# ---------------------------------------------------------------------------
# Tests: _build_event_payload_spec
# ---------------------------------------------------------------------------


class TestBuildEventPayloadSpec:
    def test_create_event(self) -> None:
        model = {"name": "test.model"}
        result = _build_event_payload_spec(model, "create", [])
        assert result["event_type"] == "create"
        assert result["model_name"] == "test.model"
        assert result["payload_fields"] == ["id", "display_name"]
        assert result["include_old_values"] is False

    def test_write_event_includes_old_values(self) -> None:
        model = {"name": "test.model"}
        result = _build_event_payload_spec(model, "write", ["status", "amount"])
        assert result["event_type"] == "write"
        assert result["payload_fields"] == ["status", "amount"]
        assert result["include_old_values"] is True

    def test_unlink_event(self) -> None:
        model = {"name": "test.model"}
        result = _build_event_payload_spec(model, "unlink", [])
        assert result["include_old_values"] is False
        assert result["payload_fields"] == ["id", "display_name"]


# ---------------------------------------------------------------------------
# Tests: _process_webhook_patterns (main entry)
# ---------------------------------------------------------------------------


class TestProcessWebhookPatterns:
    def test_happy_path_full_config(self) -> None:
        model = _make_model(
            name="sale.order",
            webhooks={
                "on_create": True,
                "on_write": ["state", "amount_total"],
                "on_unlink": True,
            },
        )
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)

        assert result is not spec  # immutability
        # Find the enriched sale.order model
        sale_model = next(m for m in result["models"] if m["name"] == "sale.order")
        assert sale_model["has_webhooks"] is True
        assert sale_model["webhook_on_create"] is True
        assert sale_model["webhook_on_write"] is True
        assert sale_model["webhook_on_unlink"] is True
        assert sale_model["webhook_watched_fields"] == ["state", "amount_total"]

        # Retry/queue defaults
        assert sale_model["webhook_max_retries"] == 3
        assert sale_model["webhook_retry_delay"] == 60
        assert sale_model["webhook_async"] is True

        # Event payloads
        payloads = sale_model["webhook_event_payloads"]
        event_types = [p["event_type"] for p in payloads]
        assert "create" in event_types
        assert "write" in event_types
        assert "unlink" in event_types

        # Override sources
        assert "webhooks" in sale_model["override_sources"].get("create", set())
        assert "webhooks" in sale_model["override_sources"].get("write", set())

        # Synthesized endpoint model
        assert result["has_webhook_endpoints"] is True
        endpoint = next(m for m in result["models"] if m["name"] == "webhook.endpoint")
        assert endpoint["_synthesized"] is True

    def test_empty_spec_no_webhooks(self) -> None:
        spec = _make_spec(models=[])
        result = _process_webhook_patterns(spec)
        assert result is spec  # unchanged

    def test_models_without_webhooks_unchanged(self) -> None:
        model = _make_model(name="res.partner")
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)
        assert result is spec

    def test_only_on_create(self) -> None:
        model = _make_model(
            name="test.model",
            webhooks={"on_create": True},
        )
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)
        enriched = next(m for m in result["models"] if m["name"] == "test.model")
        assert enriched["webhook_on_create"] is True
        assert enriched["webhook_on_write"] is False
        assert enriched["webhook_on_unlink"] is False
        payloads = enriched["webhook_event_payloads"]
        assert len(payloads) == 1
        assert payloads[0]["event_type"] == "create"

    def test_only_on_write(self) -> None:
        model = _make_model(
            name="test.model",
            webhooks={"on_write": ["status"]},
        )
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)
        enriched = next(m for m in result["models"] if m["name"] == "test.model")
        assert enriched["webhook_on_create"] is False
        assert enriched["webhook_on_write"] is True
        payloads = enriched["webhook_event_payloads"]
        assert len(payloads) == 1
        assert payloads[0]["event_type"] == "write"

    def test_custom_retry_config(self) -> None:
        model = _make_model(
            name="test.model",
            webhooks={
                "on_create": True,
                "max_retries": 5,
                "retry_delay_seconds": 120,
                "async": False,
            },
        )
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)
        enriched = next(m for m in result["models"] if m["name"] == "test.model")
        assert enriched["webhook_max_retries"] == 5
        assert enriched["webhook_retry_delay"] == 120
        assert enriched["webhook_async"] is False

    def test_mixed_models_only_webhook_enriched(self) -> None:
        plain = _make_model(name="plain.model")
        with_hooks = _make_model(
            name="hooked.model",
            webhooks={"on_create": True},
        )
        spec = _make_spec(models=[plain, with_hooks])
        result = _process_webhook_patterns(spec)
        plain_result = next(m for m in result["models"] if m["name"] == "plain.model")
        hooked_result = next(m for m in result["models"] if m["name"] == "hooked.model")
        assert "has_webhooks" not in plain_result
        assert hooked_result["has_webhooks"] is True

    def test_does_not_mutate_original_model(self) -> None:
        model = _make_model(
            name="test.model",
            webhooks={"on_create": True},
        )
        original_keys = set(model.keys())
        spec = _make_spec(models=[model])
        _process_webhook_patterns(spec)
        assert set(model.keys()) == original_keys
        assert "has_webhooks" not in model

    def test_endpoint_model_appended_last(self) -> None:
        model = _make_model(
            name="test.model",
            webhooks={"on_create": True},
        )
        spec = _make_spec(models=[model])
        result = _process_webhook_patterns(spec)
        last_model = result["models"][-1]
        assert last_model["name"] == "webhook.endpoint"
