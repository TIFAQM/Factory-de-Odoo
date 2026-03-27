"""Migration generator module: produce Odoo pre-migrate.py and post-migrate.py scripts.

Consumes diff_specs() output from spec_differ.py and generates per-change helper
functions using parameterized SQL (psycopg2.sql) following Odoo migration conventions.

Provides:
- generate_migration(): Main entry point returning MigrationResult dict
- generate_versioned_migration(): Auto-versioned pipeline with collision avoidance
- OdooVersion: Parse/validate/bump Odoo 5-segment version strings
- compute_migration_version(): Determine next version from diff severity
- discover_migrations(): Scan existing migration directories
- MigrationResult / VersionedMigrationResult: TypedDict result structures
- _model_to_table(): Convert dot-separated model name to Odoo table name
"""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass
from functools import total_ordering
from pathlib import Path
from typing import Any, TypedDict

logger = logging.getLogger("amil.migration")


# ---------------------------------------------------------------------------
# Type Definitions
# ---------------------------------------------------------------------------

class MigrationResult(TypedDict):
    pre_migrate_code: str
    post_migrate_code: str
    migration_required: bool
    version: str


class VersionedMigrationResult(TypedDict):
    pre_migrate_code: str
    post_migrate_code: str
    migration_required: bool
    version: str
    computed_version: bool


# ---------------------------------------------------------------------------
# Odoo Version Parsing & Bumping
# ---------------------------------------------------------------------------

_ODOO_VERSION_RE = re.compile(r'^(\d+)\.(\d+)\.(\d+)\.(\d+)\.(\d+)$')


@total_ordering
@dataclass(frozen=True)
class OdooVersion:
    """Immutable Odoo 5-segment version: {odoo_major}.{odoo_minor}.{major}.{minor}.{patch}.

    The first two segments (e.g. 17.0) identify the Odoo series and are never
    modified by bump(). The last three segments follow module-level semver.
    """

    odoo_major: int
    odoo_minor: int
    major: int
    minor: int
    patch: int

    @classmethod
    def parse(cls, version_str: str) -> OdooVersion:
        """Parse a dotted version string into an OdooVersion.

        Args:
            version_str: Version string like "17.0.1.0.0".

        Raises:
            ValueError: If the string doesn't match the 5-segment format.
        """
        match = _ODOO_VERSION_RE.match(version_str.strip())
        if not match:
            raise ValueError(
                f"Invalid Odoo version: {version_str!r}. "
                f"Expected format: X.Y.A.B.C (e.g. 17.0.1.0.0)"
            )
        return cls(
            odoo_major=int(match.group(1)),
            odoo_minor=int(match.group(2)),
            major=int(match.group(3)),
            minor=int(match.group(4)),
            patch=int(match.group(5)),
        )

    def bump(self, bump_type: str) -> OdooVersion:
        """Return a new OdooVersion with the specified segment bumped.

        - "patch": increments patch, preserves minor/major
        - "minor": increments minor, resets patch to 0
        - "major": increments major, resets minor and patch to 0

        The Odoo series (odoo_major.odoo_minor) is never changed.

        Args:
            bump_type: One of "patch", "minor", "major".

        Raises:
            ValueError: If bump_type is not recognized.
        """
        if bump_type == "patch":
            return OdooVersion(self.odoo_major, self.odoo_minor,
                               self.major, self.minor, self.patch + 1)
        if bump_type == "minor":
            return OdooVersion(self.odoo_major, self.odoo_minor,
                               self.major, self.minor + 1, 0)
        if bump_type == "major":
            return OdooVersion(self.odoo_major, self.odoo_minor,
                               self.major + 1, 0, 0)
        raise ValueError(
            f"Invalid bump_type: {bump_type!r}. Must be 'patch', 'minor', or 'major'."
        )

    def __str__(self) -> str:
        return f"{self.odoo_major}.{self.odoo_minor}.{self.major}.{self.minor}.{self.patch}"

    def _sort_key(self) -> tuple[int, int, int, int, int]:
        return (self.odoo_major, self.odoo_minor, self.major, self.minor, self.patch)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, OdooVersion):
            return NotImplemented
        return self._sort_key() == other._sort_key()

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, OdooVersion):
            return NotImplemented
        return self._sort_key() < other._sort_key()

    def __hash__(self) -> int:
        return hash(self._sort_key())


# ---------------------------------------------------------------------------
# Identifier Validation & Table Name Conversion
# ---------------------------------------------------------------------------

_FIELD_TYPE_TO_PG: dict[str, str] = {
    "Char": "VARCHAR",
    "Text": "TEXT",
    "Integer": "INTEGER",
    "Float": "DOUBLE PRECISION",
    "Monetary": "NUMERIC",
    "Boolean": "BOOLEAN",
    "Date": "DATE",
    "Datetime": "TIMESTAMP",
    "Binary": "BYTEA",
    "Selection": "VARCHAR",
    "Many2one": "INTEGER",
    "Html": "TEXT",
}

_VALID_PG_IDENTIFIER = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _field_type_to_pg(field_type: str) -> str:
    """Map an Odoo field type to a PostgreSQL column type for backup columns."""
    return _FIELD_TYPE_TO_PG.get(field_type, "VARCHAR")


def _model_to_table(model_name: str) -> str:
    """Convert dot-separated Odoo model name to underscore table name.

    Example: "fee.invoice" -> "fee_invoice"
    """
    return model_name.replace(".", "_")


def _validate_identifier(name: str, context: str) -> None:
    """Validate that a name is a safe PostgreSQL identifier.

    Raises ValueError if the name contains characters that could
    enable SQL injection. Defense-in-depth alongside psycopg2.sql.Identifier
    in the generated migration scripts.
    """
    if not _VALID_PG_IDENTIFIER.match(name):
        raise ValueError(
            f"Unsafe SQL identifier in {context}: {name!r}. "
            f"Must match [a-zA-Z_][a-zA-Z0-9_]*"
        )


# ---------------------------------------------------------------------------
# Helper Generation: Pre-Migrate
# ---------------------------------------------------------------------------

def _generate_pre_helpers(diff_result: dict) -> list[dict]:
    """Generate pre-migrate helper dicts from diff result.

    For each destructive/possibly-destructive change, produces a helper with:
    - name: function name
    - docstring: descriptive with DESTRUCTIVE:/POSSIBLY DESTRUCTIVE: prefix
    - body: list of code lines using psycopg2.sql for safe SQL composition

    Returns:
        List of helper dicts, each with 'name', 'docstring', 'body' keys.

    Raises:
        ValueError: If any model/field name is not a valid SQL identifier.
    """
    helpers: list[dict] = []
    changes = diff_result.get("changes", {})
    models = changes.get("models", {})

    # Model removals: backup all data
    for model in models.get("removed", []):
        model_name = model["name"]
        table = _model_to_table(model_name)
        _validate_identifier(table, f"model removal ({model_name})")
        backup_table = f"{table}_backup"
        _validate_identifier(backup_table, f"backup table for {model_name}")
        helpers.append({
            "name": f"_backup_model_{table}",
            "docstring": f'DESTRUCTIVE: Backup all data from {model_name} before model removal.',
            "body": [
                f'cr.execute(sql.SQL("SELECT COUNT(*) FROM {{}}").format(sql.Identifier("{table}")))',
                'count = cr.fetchone()[0]',
                f'_logger.info("Table {table} has %s rows to backup", count)',
                'if count > 0:',
                '    cr.execute(',
                '        sql.SQL("CREATE TABLE IF NOT EXISTS {} AS SELECT * FROM {}")',
                f'        .format(sql.Identifier("{backup_table}"), sql.Identifier("{table}"))',
                '    )',
                f'    _logger.info("Backed up %s rows from {table}", count)',
            ],
        })

    # Field-level changes in modified models
    for model_name, model_data in models.get("modified", {}).items():
        table = _model_to_table(model_name)
        _validate_identifier(table, f"modified model ({model_name})")
        fields_data = model_data.get("fields", {})

        # Removed fields: backup data
        for field in fields_data.get("removed", []):
            field_name = field["name"]
            _validate_identifier(field_name, f"removed field ({model_name}.{field_name})")
            field_type = field.get("type", "Char")
            pg_type = _field_type_to_pg(field_type)
            backup_col = f"{field_name}_backup"
            _validate_identifier(backup_col, f"backup column for {model_name}.{field_name}")
            helpers.append({
                "name": f"_backup_{field_name}",
                "docstring": (
                    f"DESTRUCTIVE: Backup column '{field_name}' from {model_name} "
                    f"before field removal."
                ),
                "body": [
                    'cr.execute(',
                    f'    sql.SQL("ALTER TABLE {{}} ADD COLUMN IF NOT EXISTS {{}} {pg_type}")',
                    f'    .format(sql.Identifier("{table}"), sql.Identifier("{backup_col}"))',
                    ')',
                    'cr.execute(',
                    '    sql.SQL("UPDATE {} SET {} = {} WHERE {} IS NOT NULL")',
                    '    .format(',
                    f'        sql.Identifier("{table}"),',
                    f'        sql.Identifier("{backup_col}"),',
                    f'        sql.Identifier("{field_name}"),',
                    f'        sql.Identifier("{field_name}"),',
                    '    )',
                    ')',
                    '_logger.info(',
                    f'    "Backed up %s rows of {field_name} in {table}", cr.rowcount',
                    ')',
                ],
            })

        # Modified fields
        for field in fields_data.get("modified", []):
            field_name = field["name"]
            _validate_identifier(field_name, f"modified field ({model_name}.{field_name})")
            field_changes = field.get("changes", {})
            severity = field.get("severity", "non_destructive")

            if severity == "non_destructive":
                continue

            # Type change: backup column
            if "type" in field_changes:
                old_type = field_changes["type"]["old"]
                new_type = field_changes["type"]["new"]
                pg_type = _field_type_to_pg(old_type)
                backup_col = f"{field_name}_backup"
                _validate_identifier(backup_col, f"backup column for {model_name}.{field_name}")
                prefix = "DESTRUCTIVE" if severity == "always_destructive" else "POSSIBLY DESTRUCTIVE"
                helpers.append({
                    "name": f"_backup_{field_name}",
                    "docstring": (
                        f"{prefix}: Backup column '{field_name}' in {model_name} "
                        f"before type change {old_type} -> {new_type}."
                    ),
                    "body": [
                        'cr.execute(',
                        f'    sql.SQL("ALTER TABLE {{}} ADD COLUMN IF NOT EXISTS {{}} {pg_type}")',
                        f'    .format(sql.Identifier("{table}"), sql.Identifier("{backup_col}"))',
                        ')',
                        'cr.execute(',
                        '    sql.SQL("UPDATE {} SET {} = {} WHERE {} IS NOT NULL")',
                        '    .format(',
                        f'        sql.Identifier("{table}"),',
                        f'        sql.Identifier("{backup_col}"),',
                        f'        sql.Identifier("{field_name}"),',
                        f'        sql.Identifier("{field_name}"),',
                        '    )',
                        ')',
                        '_logger.info(',
                        f'    "Backed up %s rows of {field_name} in {table}", cr.rowcount',
                        ')',
                    ],
                })

            # Required false -> true: validation query
            elif "required" in field_changes:
                req_change = field_changes["required"]
                if req_change.get("old") is False and req_change.get("new") is True:
                    helpers.append({
                        "name": f"_validate_{field_name}",
                        "docstring": (
                            f"POSSIBLY DESTRUCTIVE: Check for NULL values in '{field_name}' "
                            f"of {model_name} before making it required."
                        ),
                        "body": [
                            'cr.execute(',
                            '    sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IS NULL")',
                            f'    .format(sql.Identifier("{table}"), sql.Identifier("{field_name}"))',
                            ')',
                            'null_count = cr.fetchone()[0]',
                            '_logger.info(',
                            f'    "Found %s NULL values in {table}.{field_name}", null_count',
                            ')',
                            'if null_count > 0:',
                            '    _logger.warning(',
                            f'        "{table}.{field_name} has %s NULL rows, backfilling with safe default",',
                            '        null_count,',
                            '    )',
                            '    cr.execute(',
                            '        sql.SQL("UPDATE {} SET {} = CURRENT_DATE WHERE {} IS NULL")',
                            '        .format(',
                            f'            sql.Identifier("{table}"),',
                            f'            sql.Identifier("{field_name}"),',
                            f'            sql.Identifier("{field_name}"),',
                            '        )',
                            '    )',
                            f'    _logger.info("Backfilled %s rows in {table}.{field_name}", cr.rowcount)',
                        ],
                    })

            # Selection options removed: validation
            elif "selection" in field_changes:
                sel_change = field_changes["selection"]
                removed_opts = sel_change.get("options_removed", [])
                if removed_opts:
                    removed_tuple = repr(tuple(removed_opts))
                    helpers.append({
                        "name": f"_validate_{field_name}_selection",
                        "docstring": (
                            f"POSSIBLY DESTRUCTIVE: Check for invalid selection values "
                            f"in '{field_name}' of {model_name} after options removed: "
                            f"{', '.join(removed_opts)}."
                        ),
                        "body": [
                            'cr.execute(',
                            '    sql.SQL("SELECT COUNT(*) FROM {} WHERE {} IN %s")',
                            f'    .format(sql.Identifier("{table}"), sql.Identifier("{field_name}")),',
                            f'    ({removed_tuple},)',
                            ')',
                            'invalid_count = cr.fetchone()[0]',
                            '_logger.info(',
                            f'    "Found %s rows with removed selection values in'
                            f' {table}.{field_name}", invalid_count',
                            ')',
                            'if invalid_count > 0:',
                            '    _logger.warning(',
                            f'        "{table}.{field_name} has %s rows with removed values,'
                            f' review needed",',
                            '        invalid_count,',
                            '    )',
                        ],
                    })

    return helpers


# ---------------------------------------------------------------------------
# Helper Generation: Post-Migrate
# ---------------------------------------------------------------------------

def _generate_post_helpers(diff_result: dict) -> list[dict]:
    """Generate post-migrate helper dicts from diff result.

    For each destructive change, produces a restore/cleanup helper.

    Returns:
        List of helper dicts, each with 'name', 'docstring', 'body' keys.

    Raises:
        ValueError: If any model/field name is not a valid SQL identifier.
    """
    helpers: list[dict] = []
    changes = diff_result.get("changes", {})
    models = changes.get("models", {})

    # Model removals: drop table (Odoo may have already, but be safe)
    for model in models.get("removed", []):
        model_name = model["name"]
        table = _model_to_table(model_name)
        _validate_identifier(table, f"model removal ({model_name})")
        helpers.append({
            "name": f"_drop_model_{table}",
            "docstring": f'DESTRUCTIVE: Drop table {table} after model {model_name} removal.',
            "body": [
                f'cr.execute(sql.SQL("DROP TABLE IF EXISTS {{}} CASCADE").format(sql.Identifier("{table}")))',
                f'_logger.info("Dropped table {table} (rowcount=%s)", cr.rowcount)',
            ],
        })

    # Field-level changes in modified models
    for model_name, model_data in models.get("modified", {}).items():
        table = _model_to_table(model_name)
        _validate_identifier(table, f"modified model ({model_name})")
        fields_data = model_data.get("fields", {})

        # Removed fields: drop backup column
        for field in fields_data.get("removed", []):
            field_name = field["name"]
            _validate_identifier(field_name, f"removed field ({model_name}.{field_name})")
            backup_col = f"{field_name}_backup"
            _validate_identifier(backup_col, f"backup column for {model_name}.{field_name}")
            helpers.append({
                "name": f"_drop_backup_{field_name}",
                "docstring": (
                    f"DESTRUCTIVE: Drop backup column '{field_name}_backup' from {model_name} "
                    f"after field removal verified."
                ),
                "body": [
                    'cr.execute(',
                    '    sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {}")',
                    f'    .format(sql.Identifier("{table}"), sql.Identifier("{backup_col}"))',
                    ')',
                    '_logger.info(',
                    f'    "Dropped backup column {backup_col} from {table}'
                    f' (rowcount=%s)", cr.rowcount',
                    ')',
                ],
            })

        # Modified fields
        for field in fields_data.get("modified", []):
            field_name = field["name"]
            _validate_identifier(field_name, f"modified field ({model_name}.{field_name})")
            field_changes = field.get("changes", {})
            severity = field.get("severity", "non_destructive")

            if severity == "non_destructive":
                continue

            # Type change: restore from backup + drop backup
            if "type" in field_changes:
                old_type = field_changes["type"]["old"]
                new_type = field_changes["type"]["new"]
                backup_col = f"{field_name}_backup"
                _validate_identifier(backup_col, f"backup column for {model_name}.{field_name}")
                prefix = "DESTRUCTIVE" if severity == "always_destructive" else "POSSIBLY DESTRUCTIVE"
                helpers.append({
                    "name": f"_restore_{field_name}",
                    "docstring": (
                        f"{prefix}: Restore '{field_name}' in {model_name} from backup "
                        f"after type change {old_type} -> {new_type}, then drop backup."
                    ),
                    "body": [
                        'cr.execute(',
                        '    sql.SQL("UPDATE {} SET {} = {} WHERE {} IS NOT NULL")',
                        '    .format(',
                        f'        sql.Identifier("{table}"),',
                        f'        sql.Identifier("{field_name}"),',
                        f'        sql.Identifier("{backup_col}"),',
                        f'        sql.Identifier("{backup_col}"),',
                        '    )',
                        ')',
                        '_logger.info(',
                        f'    "Restored %s rows of {field_name} in {table}", cr.rowcount',
                        ')',
                        'cr.execute(',
                        '    sql.SQL("ALTER TABLE {} DROP COLUMN IF EXISTS {}")',
                        f'    .format(sql.Identifier("{table}"), sql.Identifier("{backup_col}"))',
                        ')',
                        '_logger.info(',
                        f'    "Dropped backup column {backup_col} from {table}'
                        f' (rowcount=%s)", cr.rowcount',
                        ')',
                    ],
                })

    return helpers


# ---------------------------------------------------------------------------
# Script Rendering
# ---------------------------------------------------------------------------

def _render_script(script_type: str, helpers: list[dict], version: str) -> str:
    """Render a complete migration script as a Python source string.

    Args:
        script_type: Either "pre" or "post".
        helpers: List of helper dicts with 'name', 'docstring', 'body'.
        version: Migration version string.

    Returns:
        Complete Python script as a string.
    """
    lines: list[str] = []

    # Module docstring
    label = "pre" if script_type == "pre" else "post"
    lines.append(f'"""{label}-migrate script for version {version}.')
    lines.append("")
    lines.append(f"Auto-generated by amil-utils migration generator.")
    lines.append(f'Runs {"BEFORE" if label == "pre" else "AFTER"} Odoo ORM updates the schema.')
    lines.append('"""')
    lines.append("")

    # Imports
    lines.append("import logging")
    lines.append("")
    lines.append("from psycopg2 import sql")
    lines.append("")
    lines.append("_logger = logging.getLogger(__name__)")
    lines.append("")
    lines.append("")

    # Helper functions
    for helper in helpers:
        lines.append(f"def {helper['name']}(cr):")
        lines.append(f'    """{helper["docstring"]}"""')
        for body_line in helper["body"]:
            lines.append(f"    {body_line}")
        lines.append("")
        lines.append("")

    # migrate() entry point
    lines.append("def migrate(cr, version):")
    lines.append(f'    """Main migration entry point for version {version}."""')
    if helpers:
        lines.append(f'    _logger.info("Running {label}-migrate for version %s", version)')
        for helper in helpers:
            lines.append(f"    {helper['name']}(cr)")
    else:
        lines.append(f'    _logger.info("No {label}-migration actions for version %s", version)')
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main Entry Point
# ---------------------------------------------------------------------------

def generate_migration(
    diff_result: dict,
    version: str,
    output_dir: str | Path | None = None,
) -> MigrationResult:
    """Generate Odoo migration scripts from spec diff output.

    Consumes the output of diff_specs() and generates pre-migrate.py and
    post-migrate.py with per-change helper functions using parameterized SQL.

    Args:
        diff_result: Output from spec_differ.diff_specs().
        version: Migration version string (e.g., "17.0.1.1.0").
        output_dir: Optional directory to write migration files to.
            Creates {output_dir}/migrations/{version}/ with pre-migrate.py
            and post-migrate.py.

    Returns:
        MigrationResult with pre_migrate_code, post_migrate_code,
        migration_required boolean, and version string.
    """
    migration_required = diff_result.get("migration_required", False)

    # Generate helpers
    pre_helpers = _generate_pre_helpers(diff_result)
    post_helpers = _generate_post_helpers(diff_result)

    # Render scripts
    pre_code = _render_script("pre", pre_helpers, version)
    post_code = _render_script("post", post_helpers, version)

    # Write files if output_dir specified
    if output_dir is not None:
        out_path = Path(output_dir) / "migrations" / version
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "pre-migrate.py").write_text(pre_code, encoding="utf-8")
        (out_path / "post-migrate.py").write_text(post_code, encoding="utf-8")

    return {
        "pre_migrate_code": pre_code,
        "post_migrate_code": post_code,
        "migration_required": migration_required,
        "version": version,
    }


# ---------------------------------------------------------------------------
# Version Discovery & Computation
# ---------------------------------------------------------------------------

_DEFAULT_BASE_VERSION = "19.0.1.0.0"


def discover_migrations(module_dir: str | Path) -> list[str]:
    """Scan a module's migrations/ directory for existing version directories.

    Returns version strings sorted in ascending numeric order (not lexicographic).
    Non-version directories (e.g. __pycache__) are silently skipped.

    Args:
        module_dir: Path to the Odoo module root directory.

    Returns:
        Sorted list of version strings found in migrations/.
    """
    mig_path = Path(module_dir) / "migrations"
    if not mig_path.is_dir():
        return []

    versions: list[OdooVersion] = []
    for entry in mig_path.iterdir():
        if not entry.is_dir():
            continue
        try:
            versions.append(OdooVersion.parse(entry.name))
        except ValueError:
            continue

    return [str(v) for v in sorted(versions)]


def _classify_bump_type(diff_result: dict) -> str:
    """Determine the appropriate version bump type from diff severity.

    - Model removed → "major"
    - Field removed / type change / destructive modification → "minor"
    - Non-destructive (field added, attribute change) → "patch"

    Args:
        diff_result: Output from spec_differ.diff_specs().

    Returns:
        One of "major", "minor", "patch".
    """
    changes = diff_result.get("changes", {})
    models = changes.get("models", {})

    # Model removal is the most disruptive → major
    if models.get("removed"):
        return "major"

    # Any destructive field-level change → minor
    for _model_name, model_data in models.get("modified", {}).items():
        fields_data = model_data.get("fields", {})
        if fields_data.get("removed"):
            return "minor"
        for field in fields_data.get("modified", []):
            severity = field.get("severity", "non_destructive")
            if severity != "non_destructive":
                return "minor"

    return "patch"


def compute_migration_version(diff_result: dict) -> str:
    """Auto-compute the migration version from diff contents.

    Strategy:
    1. If diff has distinct old_version and new_version, use new_version.
    2. Otherwise, parse old_version and bump based on change severity.
    3. Falls back to bumping a default base version if versions are unparseable.

    Args:
        diff_result: Output from spec_differ.diff_specs().

    Returns:
        Computed version string (e.g. "17.0.1.1.0").
    """
    old_ver_str = diff_result.get("old_version", "unknown")
    new_ver_str = diff_result.get("new_version", "unknown")

    # If new_version is explicitly different from old_version and parseable, use it
    if new_ver_str != old_ver_str and new_ver_str != "unknown":
        try:
            OdooVersion.parse(new_ver_str)
            return new_ver_str
        except ValueError as exc:
            logger.debug("Cannot parse new_version %r, falling through to bump: %s", new_ver_str, exc)

    # Parse old_version as the base for bumping
    bump_type = _classify_bump_type(diff_result)
    try:
        base = OdooVersion.parse(old_ver_str)
    except ValueError:
        logger.warning(
            "Cannot parse old_version %r, using default base %s",
            old_ver_str, _DEFAULT_BASE_VERSION,
        )
        base = OdooVersion.parse(_DEFAULT_BASE_VERSION)

    return str(base.bump(bump_type))


# ---------------------------------------------------------------------------
# Versioned Migration Pipeline
# ---------------------------------------------------------------------------

def generate_versioned_migration(
    diff_result: dict,
    module_dir: str | Path | None = None,
    *,
    version_override: str | None = None,
) -> VersionedMigrationResult:
    """Generate versioned Odoo migration scripts with auto-version computation.

    Full pipeline:
    1. Compute version from diff severity (or use version_override).
    2. Check for version collisions against existing migrations.
    3. Generate pre-migrate.py and post-migrate.py.
    4. Write files to module_dir/migrations/{version}/ if module_dir given.

    Args:
        diff_result: Output from spec_differ.diff_specs().
        module_dir: Optional module root directory for file output and
            version collision detection.
        version_override: Explicit version to use (skips auto-computation).

    Returns:
        VersionedMigrationResult with all MigrationResult keys plus
        computed_version (True if version was auto-computed).
    """
    computed = version_override is None

    if version_override is not None:
        version = version_override
    else:
        version = compute_migration_version(diff_result)

    # Collision avoidance: if version directory already exists, bump further
    if module_dir is not None and computed:
        existing = set(discover_migrations(module_dir))
        bump_type = _classify_bump_type(diff_result)
        try:
            v = OdooVersion.parse(version)
            max_bumps = 100  # safety limit
            while str(v) in existing and max_bumps > 0:
                v = v.bump(bump_type)
                max_bumps -= 1
            version = str(v)
        except ValueError as exc:
            logger.debug("Unparseable version for collision avoidance, skipping: %s", exc)

    migration_required = diff_result.get("migration_required", False)

    # Generate helpers
    pre_helpers = _generate_pre_helpers(diff_result)
    post_helpers = _generate_post_helpers(diff_result)

    # Render scripts
    pre_code = _render_script("pre", pre_helpers, version)
    post_code = _render_script("post", post_helpers, version)

    # Write files if module_dir specified
    if module_dir is not None:
        out_path = Path(module_dir) / "migrations" / version
        out_path.mkdir(parents=True, exist_ok=True)
        (out_path / "pre-migrate.py").write_text(pre_code, encoding="utf-8")
        (out_path / "post-migrate.py").write_text(post_code, encoding="utf-8")
        logger.info("Wrote migration scripts to %s", out_path)

    return {
        "pre_migrate_code": pre_code,
        "post_migrate_code": post_code,
        "migration_required": migration_required,
        "version": version,
        "computed_version": computed,
    }
