#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Environment Configuration Validator
=====================================
Run this before starting the server or in CI to catch misconfigured env vars
before they cause cryptic runtime errors.

Usage:
    python scripts/validate_env.py
    python scripts/validate_env.py --env-file .env.local

Exit codes:
    0 — all validations passed
    1 — one or more validations failed

Example CI usage in GitHub Actions:
    - run: python scripts/validate_env.py
      env:
        SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
        ...
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# ---------------------------------------------------------------------------
# Terminal colours (graceful fallback for Windows without ANSI support)
# ---------------------------------------------------------------------------

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if os.name == "nt":
    import io

    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

_ANSI_SUPPORTED = sys.stdout.isatty() or os.name != "nt"


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _ANSI_SUPPORTED else text


def _green(t: str) -> str:
    return _c("92", t)


def _red(t: str) -> str:
    return _c("91", t)


def _yellow(t: str) -> str:
    return _c("93", t)


def _bold(t: str) -> str:
    return _c("1", t)


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------


@dataclass
class ValidationResult:
    name: str
    passed: bool
    message: str
    severity: str = "error"  # "error" | "warning"

    def display(self) -> str:
        if self.passed:
            icon = _green("✔")
        elif self.severity == "warning":
            icon = _yellow("⚠")
        else:
            icon = _red("✘")
        label = _bold(self.name)
        return f"  {icon}  {label:<40}  {self.message}"


@dataclass
class ValidationReport:
    results: List[ValidationResult] = field(default_factory=list)

    def add(self, result: ValidationResult) -> None:
        self.results.append(result)

    @property
    def errors(self) -> List[ValidationResult]:
        return [r for r in self.results if not r.passed and r.severity == "error"]

    @property
    def warnings(self) -> List[ValidationResult]:
        return [r for r in self.results if not r.passed and r.severity == "warning"]

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


# ---------------------------------------------------------------------------
# Individual validators
# ---------------------------------------------------------------------------


def _check_required(report: ValidationReport, name: str) -> Optional[str]:
    """Verify that an env var is set and non-empty. Returns value or None."""
    value = os.getenv(name, "").strip()
    if not value:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message="MISSING — required variable not set",
                severity="error",
            )
        )
        return None
    masked = value[:6] + "…" if len(value) > 8 else "***"
    report.add(
        ValidationResult(
            name=name,
            passed=True,
            message=f"set  ({masked})",
        )
    )
    return value


def _check_url(report: ValidationReport, name: str, value: Optional[str]) -> None:
    """Verify that a value looks like a valid HTTPS URL."""
    if value is None:
        return
    if not re.match(r"^https?://", value):
        report.add(
            ValidationResult(
                name=f"{name} format",
                passed=False,
                message=f"expected https://... got: {value[:40]}",
                severity="error",
            )
        )
    else:
        report.add(
            ValidationResult(
                name=f"{name} format",
                passed=True,
                message="valid URL",
            )
        )


def _check_neo4j_uri(report: ValidationReport, value: Optional[str]) -> None:
    """Verify that NEO4J_URI uses a supported scheme."""
    if value is None:
        return
    valid_schemes = ("bolt://", "neo4j+s://", "neo4j://", "bolt+s://")
    if not any(value.startswith(s) for s in valid_schemes):
        report.add(
            ValidationResult(
                name="NEO4J_URI scheme",
                passed=False,
                message=(f"expected one of {valid_schemes}, got: {value[:40]}"),
                severity="error",
            )
        )
    else:
        report.add(
            ValidationResult(
                name="NEO4J_URI scheme",
                passed=True,
                message=f"valid scheme ({value.split('://')[0]}://...)",
            )
        )


def _check_int(
    report: ValidationReport,
    name: str,
    min_val: Optional[int] = None,
    max_val: Optional[int] = None,
) -> None:
    """Verify that an optional numeric env var can be parsed as int."""
    raw = os.getenv(name, "")
    if not raw:
        return  # Optional — absence is fine
    try:
        val = int(raw)
    except ValueError:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"must be an integer, got: {raw!r}",
                severity="error",
            )
        )
        return
    if min_val is not None and val < min_val:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"must be >= {min_val}, got {val}",
                severity="error",
            )
        )
        return
    if max_val is not None and val > max_val:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"must be <= {max_val}, got {val}",
                severity="error",
            )
        )
        return
    report.add(ValidationResult(name=name, passed=True, message=f"= {val}"))


def _check_float_range(
    report: ValidationReport,
    name: str,
    min_val: float,
    max_val: float,
) -> None:
    """Verify that an optional numeric env var is within [min_val, max_val]."""
    raw = os.getenv(name, "")
    if not raw:
        return
    try:
        val = float(raw)
    except ValueError:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"must be a float, got: {raw!r}",
                severity="error",
            )
        )
        return
    if not (min_val <= val <= max_val):
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"must be in [{min_val}, {max_val}], got {val}",
                severity="error",
            )
        )
        return
    report.add(ValidationResult(name=name, passed=True, message=f"= {val}  ✓ in range"))


def _check_optional_warning(report: ValidationReport, name: str, hint: str) -> None:
    """Emit a warning if a recommended (non-required) var is absent."""
    value = os.getenv(name, "").strip()
    if not value:
        report.add(
            ValidationResult(
                name=name,
                passed=False,
                message=f"not set — {hint}",
                severity="warning",
            )
        )
    else:
        masked = value[:6] + "…" if len(value) > 8 else "***"
        report.add(
            ValidationResult(
                name=name,
                passed=True,
                message=f"set  ({masked})",
            )
        )


# ---------------------------------------------------------------------------
# Main validation routine
# ---------------------------------------------------------------------------


def validate(env_file: Optional[str] = None) -> ValidationReport:
    # Load env file if provided (without importing dotenv at the top level)
    if env_file:
        env_path = Path(env_file)
        if not env_path.exists():
            print(_red(f"ERROR: env file not found: {env_path}"), file=sys.stderr)
            sys.exit(1)
        try:
            from dotenv import load_dotenv  # type: ignore[import-not-found]

            load_dotenv(env_path, override=True)
        except ImportError:
            print(
                _yellow(
                    "WARNING: python-dotenv not installed; reading from process environment only."
                ),
                file=sys.stderr,
            )

    report = ValidationReport()

    print(f"\n{_bold('--- Required Variables ---')}")

    supabase_url = _check_required(report, "SUPABASE_URL")
    _check_url(report, "SUPABASE_URL", supabase_url)

    _check_required(report, "SUPABASE_SERVICE_ROLE_KEY")

    neo4j_uri = _check_required(report, "NEO4J_URI")
    _check_neo4j_uri(report, neo4j_uri)

    _check_required(report, "NEO4J_USER")
    _check_required(report, "NEO4J_PASSWORD")

    print(f"\n{_bold('--- Recommended Variables (warnings only) ---')}")

    _check_optional_warning(
        report,
        "GROQ_API_KEY",
        "LLM calls will fail without this key",
    )
    _check_optional_warning(
        report,
        "MONGODB_URI",
        "Auth and credit system require MongoDB",
    )
    _check_optional_warning(
        report,
        "JWT_SECRET",
        "Default is insecure; set a strong secret in production",
    )

    print(f"\n{_bold('--- Numeric Configuration ---')}")

    _check_int(report, "MAX_GRAPH_NODES", min_val=1, max_val=500)
    _check_int(report, "GROQ_TIMEOUT", min_val=1, max_val=120)
    _check_int(report, "EMBED_TIMEOUT", min_val=1, max_val=120)
    _check_int(report, "RATE_LIMIT_PER_MIN", min_val=1, max_val=10000)
    _check_int(report, "CACHE_TTL", min_val=10)
    _check_int(report, "CACHE_MAX", min_val=1)
    _check_int(report, "FREE_CREDITS_PER_DAY", min_val=0)

    _check_float_range(report, "RELEVANCE_FLOOR", 0.0, 1.0)
    _check_float_range(report, "MMR_LAMBDA", 0.0, 1.0)

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate environment variables for GraphRag Research Assistant."
    )
    parser.add_argument(
        "--env-file",
        metavar="PATH",
        default=None,
        help="Path to a .env file to load before validating (e.g. .env.local)",
    )
    args = parser.parse_args()

    report = validate(env_file=args.env_file)

    # Print all results
    for result in report.results:
        print(result.display())

    # Summary
    n_ok = sum(1 for r in report.results if r.passed)
    n_err = len(report.errors)
    n_warn = len(report.warnings)

    print()
    if report.passed:
        print(_green(_bold(f"✔  All validations passed  ({n_ok} checks, {n_warn} warnings)")))
        return 0
    else:
        print(
            _red(
                _bold(
                    f"✘  {n_err} validation error(s), {n_warn} warning(s).  "
                    "Fix the issues above before starting the server."
                )
            )
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
