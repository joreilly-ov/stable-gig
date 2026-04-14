#!/usr/bin/env python3
"""
Seed data for stable-gig marketplace.

Populates test users (homeowners & contractors), jobs, bids, and reviews.
All test data uses clearly-identifiable "Test" names (Test McTest, Test O'Test, etc).

Prerequisites:
    Run scripts/seed_helper.sql once in the Supabase SQL Editor first.

Usage:
    export SUPABASE_URL=https://xxx.supabase.co
    export SUPABASE_SERVICE_KEY=eyJhbGci...
    python scripts/seed_data.py

The script is idempotent — running it again will skip existing users.
"""

import os
import sys
from datetime import datetime

try:
    from supabase import create_client, Client
except ImportError:
    print("ERROR: supabase not installed. Run: pip install supabase")
    sys.exit(1)


# ──────────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────────

TEST_PASSWORD = "TestPassword123!"
TEST_EMAIL_DOMAIN = "test.stable-gig.dev"


def get_client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY required")
        sys.exit(1)
    return create_client(url, key)


# ──────────────────────────────────────────────────────────────────────────
# User creation
# ──────────────────────────────────────────────────────────────────────────

def create_or_get_user(client: Client, email: str, full_name: str) -> str:
    """Create auth user or return existing UUID."""
    try:
        resp = client.auth.admin.create_user({
            "email": email,
            "password": TEST_PASSWORD,
            "email_confirm": True,
        })
        print(f"  ✓ Created user: {email}")
        return resp.user.id
    except Exception as e:
        if "already" in str(e).lower() or "exists" in str(e).lower():
            users = client.auth.admin.list_users()
            for user in users:
                if hasattr(user, 'email') and user.email == email:
                    print(f"  → Exists: {email}")
                    return user.id
        raise RuntimeError(f"Failed to create/find user {email}: {e}") from e


def setup_homeowners(client: Client) -> dict[str, str]:
    """Create homeowners. Returns {key: auth_user_id}."""
    print("\n🏠 Creating homeowners...")
    data = [
        ("test.mctest",       "Test McTest"),
        ("test.otest",        "Test O'Test"),
        ("tess.testington",   "Tess Testington"),
        ("t.esterly",         "T. Esterly"),
        ("testy.mctestface",  "Testy McTestface"),
    ]
    result = {}
    for key, full_name in data:
        email = f"{key}@{TEST_EMAIL_DOMAIN}"
        user_id = create_or_get_user(client, email, full_name)
        result[key] = user_id
        try:
            client.table("profiles").update({"full_name": full_name}).eq("id", user_id).execute()
        except Exception:
            pass  # profiles table may not exist or have different schema
    return result


def setup_contractors(client: Client) -> dict[str, dict]:
    """
    Create contractors via the seed_insert_contractor RPC function.
    Returns {key: {"auth_id": ..., "contractor_id": ...}}
      auth_id       — auth.users UUID (used for reviews)
      contractor_id — contractors.id auto-generated UUID (used for bids)
    """
    print("\n🔧 Creating contractors...")

    # (key, full_name, business_name, postcode, phone, expertise, license_number, insurance_details)
    data = [
        ("test.mcpipe",     "Test McPipe",     "Test & Sons Plumbing",          "SW1A 2AA", "07700 000011", ["plumbing", "heating_hvac"],           "TEST-PLM-001", "Public liability £2m"),
        ("test.owatts",     "Test O'Watts",    "Testington Electric Ltd",        "EC1A 1BB", "07700 000012", ["electrical"],                          "TEST-ELC-002", "NICEIC registered"),
        ("test.mcroof",     "Test McRoof",     "Test Roof & Build Co",           "W1D 1NN",  "07700 000013", ["roofing", "structural", "damp"],        "TEST-RFG-003", "Public liability £5m"),
        ("testy.mcjoinery", "Testy McJoinery", "Testy's Carpentry & Joinery",    "N1 9GU",   "07700 000014", ["carpentry", "flooring", "painting"],    "TEST-CRP-004", "Public liability £1m"),
        ("test.ogeneral",   "Test O'General",  "Test Brothers General Repairs",  "SE1 7PB",  "07700 000015", ["general", "painting", "tiling"],        None,           None),
    ]

    result = {}
    for key, full_name, business_name, postcode, phone, expertise, license_number, insurance_details in data:
        email = f"{key}@{TEST_EMAIL_DOMAIN}"
        auth_id = create_or_get_user(client, email, full_name)

        try:
            client.table("profiles").update({"full_name": full_name}).eq("id", auth_id).execute()
        except Exception:
            pass

        # Insert contractor via RPC (bypasses schema cache issues with ARRAY columns)
        try:
            resp = client.rpc("seed_insert_contractor", {
                "p_user_id":           auth_id,
                "p_business_name":     business_name,
                "p_postcode":          postcode,
                "p_phone":             phone,
                "p_expertise":         expertise,
                "p_license_number":    license_number,
                "p_insurance_details": insurance_details,
            }).execute()

            contractor_id = resp.data  # RPC returns the UUID
            print(f"    → Contractor: {business_name} (id={str(contractor_id)[:8]}...)")
        except Exception as e:
            if "duplicate" in str(e).lower() or "already" in str(e).lower():
                # Look up existing contractor_id by user_id
                existing = client.table("contractors").select("id").eq("user_id", auth_id).execute()
                contractor_id = existing.data[0]["id"] if existing.data else auth_id
                print(f"    → Exists: {business_name}")
            else:
                raise

        result[key] = {"auth_id": auth_id, "contractor_id": contractor_id}

    return result


# ──────────────────────────────────────────────────────────────────────────
# Jobs, bids, reviews
# ──────────────────────────────────────────────────────────────────────────

def setup_jobs_and_bids(
    client: Client,
    homeowners: dict[str, str],
    contractors: dict[str, dict],
) -> None:
    """Create open, in-progress, draft and cancelled jobs with bids."""
    print("\n📋 Creating jobs and bids...")

    jobs_data = [
        # ── Open jobs (accepting bids) ──────────────────────────────────────
        {
            "title": "[TEST] Boiler Replacement",
            "desc": "Combi boiler needs replacing. No hot water. Worcester Bosch preferred.",
            "activity": "plumbing", "postcode": "SW1A 1AA", "owner": "test.mctest", "status": "open",
            "bids": [
                ("test.mcpipe",   85000, "Part P certified, same-day replacement available."),
                ("test.ogeneral", 110000, "Full service and flush included."),
            ],
        },
        {
            "title": "[TEST] Kitchen Rewire",
            "desc": "Kitchen rewire after extension. New consumer unit, sockets, underfloor heating.",
            "activity": "electrical", "postcode": "EC1A 1BB", "owner": "test.otest", "status": "open",
            "bids": [
                ("test.owatts",   220000, "NICEIC registered, EIC cert included."),
                ("test.ogeneral", 280000, "Includes redecoration of disturbed surfaces."),
            ],
        },
        {
            "title": "[TEST] Flat Roof Repair",
            "desc": "Flat roof over kitchen leaking at parapet wall. EPDM or felt replacement.",
            "activity": "roofing", "postcode": "W1D 1NN", "owner": "tess.testington", "status": "open",
            "bids": [
                ("test.mcroof", 350000, "EPDM with 10-year guarantee."),
            ],
        },
        {
            "title": "[TEST] Garden Fence Installation",
            "desc": "15m feather-edge fencing, 1.8m high. Concrete posts. Old fence to remove.",
            "activity": "carpentry", "postcode": "N1 9GU", "owner": "t.esterly", "status": "open",
            "bids": [
                ("testy.mcjoinery", 120000, "2-day job, includes old fence removal."),
                ("test.ogeneral",    95000, "Budget option, concrete posts included."),
            ],
        },
        {
            "title": "[TEST] Bathroom Retile",
            "desc": "Full bathroom retile — 12sqm walls, 4sqm floor. Customer supplying tiles.",
            "activity": "tiling", "postcode": "SE1 7PB", "owner": "testy.mctestface", "status": "open",
            "bids": [
                ("test.ogeneral",    180000, "1-week turnaround, waste removal included."),
                ("testy.mcjoinery",  210000, "Premium finish, grout colour matched."),
            ],
        },

        # ── In-progress jobs ────────────────────────────────────────────────
        {
            "title": "[TEST] Damp Proofing — Basement",
            "desc": "Rising damp in basement on two walls. Victorian terrace. Survey done.",
            "activity": "damp", "postcode": "SW1A 1AA", "owner": "test.mctest",
            "status": "in_progress", "awarded": "test.mcroof",
            "bids": [
                ("test.mcroof",   320000, "Full tanking system with 10-year guarantee."),
                ("test.ogeneral", 410000, "Chemical injection DPC plus tanking render."),
            ],
        },
        {
            "title": "[TEST] Loft Conversion — Rear Dormer",
            "desc": "Rear dormer loft conversion, master bedroom with en-suite. 7m × 4m footprint.",
            "activity": "structural", "postcode": "EC1A 1BB", "owner": "test.otest",
            "status": "in_progress", "awarded": "test.mcroof",
            "bids": [
                ("test.mcroof",     4800000, "Full dormer with Velux backup option. 12-week programme."),
                ("testy.mcjoinery", 5200000, "Full dormer including flooring and new staircase."),
            ],
        },

        # ── Draft jobs (not yet published) ──────────────────────────────────
        {
            "title": "[TEST] Loft Insulation Top-Up",
            "desc": "Top up loft insulation to 270mm. Approx 50sqm. Access via hatch only.",
            "activity": "general", "postcode": "SW1A 1AA", "owner": "test.mctest", "status": "draft",
        },
        {
            "title": "[TEST] Patio — Indian Sandstone",
            "desc": "Lay approximately 30sqm Indian sandstone. Existing concrete base in good condition.",
            "activity": "landscaping", "postcode": "EC1A 1BB", "owner": "test.otest", "status": "draft",
        },

        # ── Cancelled ───────────────────────────────────────────────────────
        {
            "title": "[TEST] Sash Window Replacement",
            "desc": "Replace 6 original sash windows. Grade II listed — planning approval needed.",
            "activity": "glazing", "postcode": "W1D 1NN", "owner": "tess.testington", "status": "cancelled",
        },
    ]

    for job_data in jobs_data:
        owner_id = homeowners[job_data["owner"]]
        escrow = "held" if job_data["status"] in ("in_progress", "awarded") else "pending"

        job_resp = client.table("jobs").insert({
            "user_id":      owner_id,
            "title":        job_data["title"],
            "description":  job_data["desc"],
            "activity":     job_data["activity"],
            "postcode":     job_data["postcode"],
            "status":       job_data["status"],
            "escrow_status": escrow,
        }).execute()

        job_id = job_resp.data[0]["id"]
        print(f"  ✓ {job_data['status'].upper():12} {job_data['title']}")

        for contractor_key, amount_pence, note in job_data.get("bids", []):
            contractor_id = contractors[contractor_key]["contractor_id"]
            awarded_key   = job_data.get("awarded")
            bid_status    = "accepted" if contractor_key == awarded_key else ("rejected" if awarded_key else "pending")

            client.table("bids").insert({
                "job_id":        job_id,
                "contractor_id": contractor_id,
                "amount_pence":  amount_pence,
                "note":          note,
                "status":        bid_status,
            }).execute()


def setup_completed_jobs_with_reviews(
    client: Client,
    homeowners: dict[str, str],
    contractors: dict[str, dict],
) -> None:
    """Create completed jobs with visible reviews from both sides."""
    print("\n⭐ Creating completed jobs with reviews...")

    jobs_data = [
        {
            "title":    "[TEST] Kitchen Cabinet Installation",
            "desc":     "Install 12-unit kitchen. Worktops, plinth and cornice.",
            "activity": "carpentry", "postcode": "W1D 1NN",
            "owner": "tess.testington", "contractor": "testy.mcjoinery",
            "bid_amount": 185000,
            "client_rating":      (5, 5, 4),
            "client_review":      "[TEST REVIEW] Testy arrived on time both days and the finish is excellent. Would use again.",
            "contractor_rating":  (5, 4, 5),
            "contractor_review":  "[TEST REVIEW] Great customer, had everything ready on arrival. Easy job to work on.",
        },
        {
            "title":    "[TEST] Full House Rewire",
            "desc":     "Complete rewire, 3-bed Victorian terrace. New consumer unit, all first and second fix.",
            "activity": "electrical", "postcode": "N1 9GU",
            "owner": "t.esterly", "contractor": "test.owatts",
            "bid_amount": 650000,
            "client_rating":      (4, 5, 5),
            "client_review":      "[TEST REVIEW] Excellent work. Test O'Watts kept us updated every step. Highly recommended.",
            "contractor_rating":  (4, 5, 4),
            "contractor_review":  "[TEST REVIEW] T. Esterly was flexible and accommodating. Property well prepared.",
        },
        {
            "title":    "[TEST] Bathroom Suite Replacement",
            "desc":     "Full bathroom suite swap-out. Bath, basin, WC and shower tray.",
            "activity": "plumbing", "postcode": "SE1 7PB",
            "owner": "testy.mctestface", "contractor": "test.mcpipe",
            "bid_amount": 95000,
            "client_rating":      (5, 4, 5),
            "client_review":      "[TEST REVIEW] Test McPipe was brilliant — in and out in a day, zero mess. Perfect installation.",
            "contractor_rating":  (5, 3, 5),
            "contractor_review":  "[TEST REVIEW] All sanitaryware was ready. Slight delay reaching client day before but no issues on site.",
        },
    ]

    for job_data in jobs_data:
        owner_id      = homeowners[job_data["owner"]]
        contractor    = contractors[job_data["contractor"]]
        contractor_id = contractor["contractor_id"]

        job_resp = client.table("jobs").insert({
            "user_id":       owner_id,
            "title":         job_data["title"],
            "description":   job_data["desc"],
            "activity":      job_data["activity"],
            "postcode":      job_data["postcode"],
            "status":        "completed",
            "escrow_status": "funds_released",
        }).execute()

        job_id = job_resp.data[0]["id"]
        print(f"  ✓ COMPLETED     {job_data['title']}")

        # Accepted bid
        client.table("bids").insert({
            "job_id":        job_id,
            "contractor_id": contractor_id,
            "amount_pence":  job_data["bid_amount"],
            "note":          "[TEST] Winning bid",
            "status":        "accepted",
        }).execute()

        # Client → Contractor review (via RPC to bypass schema cache issues)
        client.rpc("seed_insert_review", {
            "p_contractor_id":        contractor_id,
            "p_job_id":               str(job_id),
            "p_reviewer_id":          owner_id,
            "p_rating_quality":       job_data["client_rating"][2],
            "p_rating_communication": job_data["client_rating"][1],
            "p_rating_cleanliness":   job_data["client_rating"][0],
            "p_comment":              job_data["client_review"],
        }).execute()


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("🌱 Seeding stable-gig database...\n")

    client = get_client()
    homeowners  = setup_homeowners(client)
    contractors = setup_contractors(client)
    setup_jobs_and_bids(client, homeowners, contractors)
    setup_completed_jobs_with_reviews(client, homeowners, contractors)

    print("\n✅ Seed complete!\n")
    print("Test accounts (password: TestPassword123!)")
    print("  Homeowners :")
    print("    test.mctest@test.stable-gig.dev        — Test McTest")
    print("    test.otest@test.stable-gig.dev         — Test O'Test")
    print("    tess.testington@test.stable-gig.dev    — Tess Testington")
    print("    t.esterly@test.stable-gig.dev          — T. Esterly")
    print("    testy.mctestface@test.stable-gig.dev   — Testy McTestface")
    print("  Contractors:")
    print("    test.mcpipe@test.stable-gig.dev        — Test & Sons Plumbing")
    print("    test.owatts@test.stable-gig.dev        — Testington Electric Ltd")
    print("    test.mcroof@test.stable-gig.dev        — Test Roof & Build Co")
    print("    testy.mcjoinery@test.stable-gig.dev   — Testy's Carpentry & Joinery")
    print("    test.ogeneral@test.stable-gig.dev      — Test Brothers General Repairs")


if __name__ == "__main__":
    main()
