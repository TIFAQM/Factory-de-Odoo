"""Tests for archival strategy preprocessor."""

from __future__ import annotations

import pytest

from amil_utils.preprocessors.archival import _process_archival_strategy


def _make_spec(
    *,
    models=None,
    cron_jobs=None,
    module_name="test_module",
):
    """Build a minimal spec dict for testing."""
    spec = {
        "module_name": module_name,
        "models": models or [],
    }
    if cron_jobs is not None:
        spec["cron_jobs"] = cron_jobs
    return spec


def _make_model(
    *,
    name="test.record",
    description="Test Record",
    fields=None,
    archival_policy=None,
):
    """Build a minimal model dict."""
    result = {
        "name": name,
        "description": description,
        "fields": fields or [
            {"name": "name", "type": "Char"},
            {"name": "create_date", "type": "Datetime"},
        ],
    }
    if archival_policy is not None:
        result["archival_policy"] = archival_policy
    return result


class TestProcessArchivalStrategy:
    """Tests for _process_archival_strategy preprocessor."""

    def test_happy_path_default_policy(self):
        """Model with archival_policy gets full archival enrichment with defaults."""
        policy = {"archive_after_days": 180}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)

        assert result is not spec
        enriched = result["models"][0]

        # Model-level flags
        assert enriched["has_archival_policy"] is True
        assert enriched["archival_archive_after_days"] == 180
        assert enriched["archival_partition_field"] == "create_date"
        assert enriched["archival_destination"] == "active"
        assert enriched["archival_batch_size"] == 500

        # Default retention tiers
        tiers = enriched["archival_retention_tiers"]
        assert "hot" in tiers
        assert "warm" in tiers
        assert "cold" in tiers

        # archived_date field injected
        field_names = [f["name"] for f in enriched["fields"]]
        assert "archived_date" in field_names

        # active field injected (destination=active)
        assert "active" in field_names

        # Cron job generated
        cron_jobs = result["cron_jobs"]
        assert len(cron_jobs) == 1
        assert cron_jobs[0]["method"] == "_cron_archive_old_records"
        assert cron_jobs[0]["model_name"] == "test.record"

    def test_custom_retention_tiers(self):
        """Custom retention tiers override defaults."""
        custom_tiers = {
            "hot": {"days": 30, "description": "Recent"},
            "cold": {"days": 730, "description": "Old"},
        }
        policy = {"retention_tiers": custom_tiers}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_retention_tiers"] == custom_tiers
        assert "warm" not in enriched["archival_retention_tiers"]

    def test_purge_after_days_adds_purge_cron(self):
        """Setting purge_after_days generates a purge cron job."""
        policy = {"purge_after_days": 365 * 3}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_purge_after_days"] == 365 * 3

        cron_jobs = result["cron_jobs"]
        assert len(cron_jobs) == 2
        methods = [c["method"] for c in cron_jobs]
        assert "_cron_archive_old_records" in methods
        assert "_cron_purge_expired_records" in methods

    def test_no_purge_days_means_no_purge_cron(self):
        """Without purge_after_days, only archive cron is generated."""
        policy = {"archive_after_days": 90}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)

        cron_jobs = result["cron_jobs"]
        assert len(cron_jobs) == 1
        assert cron_jobs[0]["method"] == "_cron_archive_old_records"

    def test_custom_partition_field(self):
        """Custom partition_field is stored."""
        policy = {"partition_field": "order_date"}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_partition_field"] == "order_date"

    def test_destination_table(self):
        """destination='table' does not inject active field."""
        policy = {"destination": "table"}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_destination"] == "table"
        field_names = [f["name"] for f in enriched["fields"]]
        # archived_date always injected
        assert "archived_date" in field_names
        # active NOT injected for table destination
        assert "active" not in field_names

    def test_empty_spec_no_archival_models(self):
        """No archival models returns spec unchanged."""
        model = _make_model(archival_policy=None)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)

        assert result is spec

    def test_empty_models_list(self):
        """Empty models list returns spec unchanged."""
        spec = _make_spec(models=[])

        result = _process_archival_strategy(spec)

        assert result is spec

    def test_existing_archived_date_not_duplicated(self):
        """If archived_date field exists, it is not added again."""
        fields = [
            {"name": "name", "type": "Char"},
            {"name": "archived_date", "type": "Datetime"},
        ]
        policy = {"archive_after_days": 30}
        model = _make_model(fields=fields, archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        archived_fields = [f for f in enriched["fields"] if f["name"] == "archived_date"]
        assert len(archived_fields) == 1

    def test_existing_active_field_not_duplicated(self):
        """If active field already exists, it is not added again."""
        fields = [
            {"name": "name", "type": "Char"},
            {"name": "active", "type": "Boolean", "default": True},
        ]
        policy = {"destination": "active"}
        model = _make_model(fields=fields, archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        active_fields = [f for f in enriched["fields"] if f["name"] == "active"]
        assert len(active_fields) == 1

    def test_immutability_original_not_mutated(self):
        """Original spec and model dicts are not mutated."""
        policy = {"archive_after_days": 60}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        original_model_keys = set(model.keys())
        original_field_count = len(model["fields"])

        _process_archival_strategy(spec)

        assert set(model.keys()) == original_model_keys
        assert len(model["fields"]) == original_field_count
        assert "has_archival_policy" not in model

    def test_non_archival_models_passed_through(self):
        """Models without archival_policy are included unchanged."""
        plain = _make_model(name="test.plain", archival_policy=None)
        archival = _make_model(name="test.archival", archival_policy={"archive_after_days": 30})
        spec = _make_spec(models=[plain, archival])

        result = _process_archival_strategy(spec)

        assert result["models"][0]["name"] == "test.plain"
        assert result["models"][0].get("has_archival_policy") is None
        assert result["models"][1]["name"] == "test.archival"
        assert result["models"][1]["has_archival_policy"] is True

    def test_custom_batch_size(self):
        """Custom batch_size is propagated."""
        policy = {"batch_size": 1000}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_batch_size"] == 1000

    def test_custom_cron_interval(self):
        """Custom cron_interval_days is used in archival cron."""
        policy = {"cron_interval_days": 7}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        cron = result["cron_jobs"][0]

        assert cron["interval_number"] == 7
        assert cron["interval_type"] == "days"

    def test_existing_cron_jobs_preserved(self):
        """Pre-existing cron_jobs in spec are preserved."""
        existing_cron = {"name": "Existing Cron", "method": "do_something"}
        policy = {"archive_after_days": 30}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model], cron_jobs=[existing_cron])

        result = _process_archival_strategy(spec)
        cron_jobs = result["cron_jobs"]

        assert len(cron_jobs) == 2
        assert cron_jobs[0] == existing_cron
        assert cron_jobs[1]["method"] == "_cron_archive_old_records"

    def test_cron_description_uses_model_description(self):
        """Cron job name uses the model description."""
        policy = {"archive_after_days": 30}
        model = _make_model(
            description="Purchase Order",
            archival_policy=policy,
        )
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        cron = result["cron_jobs"][0]

        assert "Purchase Order" in cron["name"]

    def test_purge_cron_interval(self):
        """Custom purge_interval_days is used in purge cron."""
        policy = {
            "purge_after_days": 365,
            "purge_interval_days": 14,
        }
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        purge_cron = [c for c in result["cron_jobs"] if "Purge" in c["name"]][0]

        assert purge_cron["interval_number"] == 14

    def test_default_archive_after_days(self):
        """Default archive_after_days is 365 when not specified in policy."""
        # Note: empty dict {} is falsy, so we need at least one key to trigger
        policy = {"destination": "active"}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)
        enriched = result["models"][0]

        assert enriched["archival_archive_after_days"] == 365

    def test_empty_policy_dict_skips_enrichment(self):
        """An empty archival_policy dict ({}) is falsy, so enrichment is skipped."""
        policy = {}
        model = _make_model(archival_policy=policy)
        spec = _make_spec(models=[model])

        result = _process_archival_strategy(spec)

        # Empty dict is falsy, so preprocessor returns spec unchanged
        assert result is spec
