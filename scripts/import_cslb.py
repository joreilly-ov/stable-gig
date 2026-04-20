#!/usr/bin/env python3
"""Import CSLB licence and personnel CSV data into Supabase.

Usage
-----
    python scripts/import_cslb.py [--licences PATH] [--personnel PATH] [--batch INT] [--dry-run]

Defaults
--------
    --licences   docs/MasterLicenseData.csv
    --personnel  docs/PersonnelData.csv
    --batch      500     rows per upsert call
    --dry-run    False   parse & validate only, no DB writes

Requires
--------
    SUPABASE_URL and SUPABASE_SERVICE_KEY in backend/.env or environment.
    pip install supabase python-dotenv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Env / credentials
# ---------------------------------------------------------------------------

def _load_env() -> dict[str, str]:
    """Load .env from backend/ and return a clean key→value dict."""
    env_path = Path(__file__).resolve().parents[1] / "backend" / ".env"
    result: dict[str, str] = {}

    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                k, _, rest = line.partition("=")
                v = rest.strip()
                # Strip inline comment (e.g. KEY=   # actual_value)
                if v.startswith("#"):
                    v = v[1:].strip()
                elif " #" in v:
                    v = v[: v.index(" #")].strip()
                result[k.strip()] = v

    # Environment variables override .env
    for k in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if k in os.environ:
            result[k] = os.environ[k]

    return result


# ---------------------------------------------------------------------------
# Cleaning helpers
# ---------------------------------------------------------------------------

_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _clean(value: str) -> str | None:
    """Strip whitespace; return None for empty / whitespace-only strings."""
    v = value.strip()
    return v if v else None


def _date(value: str) -> str | None:
    """Parse MM/DD/YYYY → YYYY-MM-DD ISO string, or None."""
    v = (value or "").strip()
    if not v:
        return None
    m = _DATE_RE.match(v)
    if not m:
        return None
    mm, dd, yyyy = m.groups()
    try:
        date(int(yyyy), int(mm), int(dd))
        return f"{yyyy}-{mm.zfill(2)}-{dd.zfill(2)}"
    except ValueError:
        return None


def _int(value: str) -> int | None:
    """Parse integer string, return None for empty / non-numeric."""
    v = (value or "").strip().replace(",", "")
    try:
        return int(float(v)) if v else None
    except (ValueError, TypeError):
        return None


def _pipe_array(value: str) -> list[str] | None:
    """Split pipe-separated string into a cleaned list; return None if empty."""
    v = (value or "").strip()
    if not v:
        return None
    parts = [p.strip() for p in v.split("|")]
    parts = [p for p in parts if p]
    return parts if parts else None


# ---------------------------------------------------------------------------
# Row builders
# ---------------------------------------------------------------------------

def build_licence_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "licence_number":       _clean(row["LicenseNo"]),
        "last_update":          _date(row["LastUpdate"]),
        "business_name":        _clean(row["BusinessName"]) or "",
        "business_name_2":      _clean(row["BUS-NAME-2"]),
        "full_business_name":   _clean(row["FullBusinessName"]),
        "name_type":            _clean(row["NAME-TP-2"]),
        "mailing_address":      _clean(row["MailingAddress"]),
        "city":                 _clean(row["City"]),
        "state":                _clean(row["State"]),
        "county":               _clean(row["County"]),
        "zip_code":             _clean(row["ZIPCode"]),
        "country":              _clean(row["country"]),
        "business_phone":       _clean(row["BusinessPhone"]),
        "business_type":        _clean(row["BusinessType"]),
        "issue_date":           _date(row["IssueDate"]),
        "reissue_date":         _date(row["ReissueDate"]),
        "expiration_date":      _date(row["ExpirationDate"]),
        "inactivation_date":    _date(row["InactivationDate"]),
        "reactivation_date":    _date(row["ReactivationDate"]),
        "pending_suspension":   _date(row["PendingSuspension"]),
        "pending_class_removal":_date(row["PendingClassRemoval"]),
        "primary_status":       _clean(row["PrimaryStatus"]) or "UNKNOWN",
        "secondary_status":     _clean(row["SecondaryStatus"]),
        "classifications":      _clean(row["Classifications(s)"]),
        "asbestos_reg":         _clean(row["AsbestosReg"]),
        "wc_coverage_type":     _clean(row["WorkersCompCoverageType"]),
        "wc_insurance_company": _clean(row["WCInsuranceCompany"]),
        "wc_policy_number":     _clean(row["WCPolicyNumber"]),
        "wc_effective_date":    _date(row["WCEffectiveDate"]),
        "wc_expiration_date":   _date(row["WCExpirationDate"]),
        "wc_cancellation_date": _date(row["WCCancellationDate"]),
        "wc_suspend_date":      _date(row["WCSuspendDate"]),
        "cb_surety_company":    _clean(row["CBSuretyCompany"]),
        "cb_number":            _clean(row["CBNumber"]),
        "cb_effective_date":    _date(row["CBEffectiveDate"]),
        "cb_cancellation_date": _date(row["CBCancellationDate"]),
        "cb_amount":            _int(row["CBAmount"]),
        "wb_surety_company":    _clean(row["WBSuretyCompany"]),
        "wb_number":            _clean(row["WBNumber"]),
        "wb_effective_date":    _date(row["WBEffectiveDate"]),
        "wb_cancellation_date": _date(row["WBCancellationDate"]),
        "wb_amount":            _int(row["WBAmount"]),
        "db_surety_company":    _clean(row["DBSuretyCompany"]),
        "db_number":            _clean(row["DBNumber"]),
        "db_effective_date":    _date(row["DBEffectiveDate"]),
        "db_cancellation_date": _date(row["DBCancellationDate"]),
        "db_amount":            _int(row["DBAmount"]),
        "db_date_required":     _date(row["DateRequired"]),
        "db_discp_case_region": _clean(row["DiscpCaseRegion"]),
        "db_bond_reason":       _clean(row["DBBondReason"]),
        "db_case_no":           _clean(row["DBCaseNo"]),
    }


def build_personnel_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "licence_number":         _clean(row["LIC-NO"]),
        "seq_no":                 _clean(row["SEQ-NO"]),
        "last_updated":           _date(row["LastUpdated"]),
        "record_type":            _clean(row["REC-TP"]),
        "name_type":              _clean(row["Name-TP"]),
        "name":                   _clean(row["Name"]) or "",
        "titles":                 _pipe_array(row["EMP-Titl-CDE"]),
        "class_codes":            _pipe_array(row["CL-CDE"]),
        "class_code_statuses":    _pipe_array(row["CL-CDE-STAT"]),
        "association_dates":      _pipe_array(row["ASSN-DT"]),
        "disassociation_dates":   _pipe_array(row["DIS-ASSN-DT"]),
        "surety_type":            _clean(row["SURETY-TP"]),
        "surety_company":         _clean(row["SuretyCompany"]),
        "bond_number":            _clean(row["BOND-NO"]),
        "bond_amount":            _int(row["BOND-AMT"]),
        "bond_effective_date":    _date(row["EffectiveDate"]),
        "bond_cancellation_date": _date(row["CancellationDate"]),
    }


# ---------------------------------------------------------------------------
# Upsert helpers
# ---------------------------------------------------------------------------

def upsert_batch(
    client,
    table: str,
    rows: list[dict],
    on_conflict: str,
    dry_run: bool,
) -> int:
    if dry_run or not rows:
        return len(rows)
    resp = (
        client.table(table)
        .upsert(rows, on_conflict=on_conflict)
        .execute()
    )
    if hasattr(resp, "error") and resp.error:
        raise RuntimeError(f"Upsert error on {table}: {resp.error}")
    return len(rows)


# ---------------------------------------------------------------------------
# Import routines
# ---------------------------------------------------------------------------

def import_licences(
    client,
    csv_path: Path,
    batch_size: int,
    dry_run: bool,
) -> None:
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Importing licences from {csv_path.name}…")
    batch: list[dict] = []
    total = skipped = 0
    t0 = time.time()

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = build_licence_row(raw)
            if not row["licence_number"]:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= batch_size:
                total += upsert_batch(client, "cslb_licences", batch, "licence_number", dry_run)
                batch = []
                if total % 10_000 == 0:
                    elapsed = time.time() - t0
                    print(f"  …{total:,} rows  ({elapsed:.0f}s)")

    if batch:
        total += upsert_batch(client, "cslb_licences", batch, "licence_number", dry_run)

    elapsed = time.time() - t0
    print(f"  Done: {total:,} licences upserted, {skipped} skipped  ({elapsed:.1f}s)")


def import_personnel(
    client,
    csv_path: Path,
    batch_size: int,
    dry_run: bool,
) -> None:
    print(f"\n{'[DRY RUN] ' if dry_run else ''}Importing personnel from {csv_path.name}…")
    batch: list[dict] = []
    total = skipped = 0
    t0 = time.time()

    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            row = build_personnel_row(raw)
            if not row["licence_number"] or not row["seq_no"]:
                skipped += 1
                continue
            batch.append(row)
            if len(batch) >= batch_size:
                total += upsert_batch(
                    client, "cslb_personnel", batch,
                    "licence_number,seq_no", dry_run,
                )
                batch = []
                if total % 10_000 == 0:
                    elapsed = time.time() - t0
                    print(f"  …{total:,} rows  ({elapsed:.0f}s)")

    if batch:
        total += upsert_batch(
            client, "cslb_personnel", batch,
            "licence_number,seq_no", dry_run,
        )

    elapsed = time.time() - t0
    print(f"  Done: {total:,} personnel upserted, {skipped} skipped  ({elapsed:.1f}s)")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--licences",  default=str(repo_root / "docs" / "MasterLicenseData.csv"))
    parser.add_argument("--personnel", default=str(repo_root / "docs" / "PersonnelData.csv"))
    parser.add_argument("--batch",     type=int, default=500, help="Rows per upsert call")
    parser.add_argument("--dry-run",   action="store_true", help="Parse only, no DB writes")
    args = parser.parse_args()

    licences_path  = Path(args.licences)
    personnel_path = Path(args.personnel)

    for p in (licences_path, personnel_path):
        if not p.exists():
            print(f"ERROR: file not found: {p}", file=sys.stderr)
            sys.exit(1)

    env = _load_env()
    url = env.get("SUPABASE_URL", "").strip()
    key = env.get("SUPABASE_SERVICE_KEY", "").strip()

    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in backend/.env or environment", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print("DRY RUN — no database writes will occur.")
        client = None
    else:
        try:
            from supabase import create_client
        except ImportError:
            print("ERROR: supabase package not installed. Run: pip install supabase", file=sys.stderr)
            sys.exit(1)
        client = create_client(url, key)
        print(f"Connected to {url}")

    import_licences(client,  licences_path,  args.batch, args.dry_run)
    import_personnel(client, personnel_path, args.batch, args.dry_run)

    print("\nImport complete.")


if __name__ == "__main__":
    main()
