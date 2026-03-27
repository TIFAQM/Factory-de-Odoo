"""Webhook pattern processing.

FLAW-11: Event-driven webhook dispatch with endpoint model synthesis,
payload builders, and retry/queue configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from amil_utils.preprocessors._registry import register_preprocessor
from amil_utils.utils.copy import deep_copy_model, merge_override_source

logger = logging.getLogger(__name__)


def _build_webhook_endpoint_model(module_name: str) -> dict[str, Any]:
    """FLAW-11: Synthesize webhook.endpoint companion model.

    Stores registered webhook endpoints with URL, secret token,
    subscribed events, and retry configuration.
    """
    return {
        "name": "webhook.endpoint",
        "description": "Webhook Endpoint",
        "_synthesized": True,
        "_is_webhook_endpoint": True,
        "model_order": "name",
        "fields": [
            {
                "name": "name",
                "type": "Char",
                "string": "Endpoint Name",
                "required": True,
            },
            {
                "name": "url",
                "type": "Char",
                "string": "Webhook URL",
                "required": True,
            },
            {
                "name": "secret_token",
                "type": "Char",
                "string": "Secret Token",
                "copy": False,
                "groups": "base.group_system",
                "help": "HMAC signing secret for payload verification",
            },
            {
                "name": "events",
                "type": "Char",
                "string": "Subscribed Events",
                "required": True,
                "help": "Comma-separated event names (e.g. create,write,unlink)",
            },
            {
                "name": "target_model",
                "type": "Char",
                "string": "Target Model",
                "required": True,
                "index": True,
            },
            {
                "name": "active",
                "type": "Boolean",
                "string": "Active",
                "default": True,
            },
            {
                "name": "max_retries",
                "type": "Integer",
                "string": "Max Retries",
                "default": 3,
            },
            {
                "name": "retry_delay_seconds",
                "type": "Integer",
                "string": "Retry Delay (s)",
                "default": 60,
            },
            {
                "name": "last_success",
                "type": "Datetime",
                "string": "Last Success",
                "readonly": True,
            },
            {
                "name": "last_error",
                "type": "Text",
                "string": "Last Error",
                "readonly": True,
            },
        ],
        "sql_constraints": [
            {
                "name": "unique_url_model",
                "definition": "UNIQUE(url, target_model)",
                "message": "Webhook URL must be unique per target model.",
            },
        ],
        # Synthesized after earlier preprocessors (security@60, init_override_sources@15),
        # so we must provide keys they would normally set.
        "security_acl": [
            {"role": "manager", "perm_create": 1, "perm_read": 1,
             "perm_write": 1, "perm_unlink": 1},
        ],
        "record_rule_scopes": [],
        "record_rule_bindings": {},
        "override_sources": {},
    }


def _build_event_payload_spec(
    model: dict[str, Any], event_type: str, watched_fields: list[str]
) -> dict[str, Any]:
    """FLAW-11: Build event payload metadata for a webhook trigger."""
    return {
        "event_type": event_type,
        "model_name": model["name"],
        "payload_fields": watched_fields or ["id", "display_name"],
        "include_old_values": event_type == "write",
    }


@register_preprocessor(order=100, name="webhook_patterns")
def _process_webhook_patterns(spec: dict[str, Any]) -> dict[str, Any]:
    """Pre-process webhook configuration on models.

    For each model with a ``webhooks`` block:
    1. Parses ``on_create`` (bool), ``on_write`` (field list), ``on_unlink`` (bool)
    2. Sets ``webhook_config``, ``webhook_watched_fields``, ``has_webhooks``
    3. Sets ``webhook_on_create``, ``webhook_on_write``, ``webhook_on_unlink``
    4. Adds ``"webhooks"`` to ``override_sources["create"]`` if ``on_create``
    5. Adds ``"webhooks"`` to ``override_sources["write"]`` if ``on_write`` non-empty
    6. FLAW-11: Builds event payload specs per trigger type
    7. FLAW-11: Synthesizes webhook.endpoint companion model
    8. FLAW-11: Adds retry/queue configuration

    Returns a new spec dict. Pure function -- does NOT mutate the input spec.
    """
    models = spec.get("models", [])
    webhook_models = [m for m in models if m.get("webhooks")]
    if not webhook_models:
        return spec

    module_name = spec.get("module_name", "module")
    new_models = []
    has_any_webhooks = False

    for model in models:
        webhooks = model.get("webhooks")
        if not webhooks:
            new_models.append(model)
            continue

        has_any_webhooks = True
        new_model = deep_copy_model(model)

        on_create = webhooks.get("on_create", False)
        on_write = webhooks.get("on_write", [])
        on_unlink = webhooks.get("on_unlink", False)

        new_model["has_webhooks"] = True
        new_model["webhook_config"] = {
            "on_create": on_create,
            "on_write": on_write,
            "on_unlink": on_unlink,
        }
        new_model["webhook_watched_fields"] = on_write
        new_model["webhook_on_create"] = on_create
        new_model["webhook_on_write"] = bool(on_write)
        new_model["webhook_on_unlink"] = on_unlink

        # FLAW-11: Retry/queue configuration
        new_model["webhook_max_retries"] = webhooks.get("max_retries", 3)
        new_model["webhook_retry_delay"] = webhooks.get("retry_delay_seconds", 60)
        new_model["webhook_async"] = webhooks.get("async", True)

        # FLAW-11: Event payload specs
        event_payloads = []
        if on_create:
            event_payloads.append(
                _build_event_payload_spec(model, "create", [])
            )
            merge_override_source(new_model, "create", "webhooks")
        if on_write:
            event_payloads.append(
                _build_event_payload_spec(model, "write", on_write)
            )
            merge_override_source(new_model, "write", "webhooks")
        if on_unlink:
            event_payloads.append(
                _build_event_payload_spec(model, "unlink", [])
            )
        new_model["webhook_event_payloads"] = event_payloads

        new_models.append(new_model)

    new_spec: dict[str, Any] = {**spec, "models": new_models}

    # FLAW-11: Synthesize webhook.endpoint model if any model uses webhooks
    if has_any_webhooks:
        endpoint_model = _build_webhook_endpoint_model(module_name)
        new_spec["models"] = [*new_spec["models"], endpoint_model]
        new_spec["has_webhook_endpoints"] = True

    return new_spec
