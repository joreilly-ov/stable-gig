#!/usr/bin/env python3
"""
Seed data for stable-gig marketplace.

Populates test users (homeowners & contractors), jobs, bids, and reviews.
All test data uses clearly-identifiable "Test" names (Test McTest, Test O'Test, etc).

Usage:
    export SUPABASE_URL=https://xxx.supabase.co
    export SUPABASE_SERVICE_KEY=eyJhbGci...
    python scripts/seed_data.py

The script is idempotent — running it again will skip existing users.
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Optional

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
    """Create auth user or return existing. Returns UUID."""
    try:
        resp = client.auth.admin.create_user({
            "email": email,
            "password": TEST_PASSWORD,
            "email_confirm": True,
        })
        user_id = resp.user.id
        print(f"  ✓ Created user: {email}")
        return user_id
    except Exception as e:
        if "already" in str(e).lower() or "exists" in str(e).lower():
            try:
                users = client.auth.admin.list_users()
                for user in users:
                    if hasattr(user, 'email') and user.email == email:
                        print(f"  → Exists: {email}")
                        return user.id
            except Exception:
                pass
        raise RuntimeError(f"Failed to create/find user {email}: {e}") from e


def setup_homeowners(client: Client) -> dict[str, str]:
    """Create 5 homeowners. Returns {key: user_id}."""
    print("\n🏠 Creating homeowners...")
    homeowners_data = [
        ("test.mctest", "Test McTest"),
        ("test.otest", "Test O'Test"),
        ("tess.testington", "Tess Testington"),
        ("t.esterly", "T. Esterly"),
        ("testy.mctestface", "Testy McTestface"),
    ]

    result = {}
    for key, full_name in homeowners_data:
        email = f"{key}@{TEST_EMAIL_DOMAIN}"
        user_id = create_or_get_user(client, email, full_name)
        result[key] = user_id
        # Update profile with full_name
        client.table("profiles").update({"full_name": full_name}).eq("id", user_id).execute()

    return result


def setup_contractors(client: Client) -> dict[str, str]:
    """Create 5 contractors. Returns {key: user_id}."""
    print("\n🔧 Creating contractors...")
    contractors_data = [
        ("test.mcpipe", "Test McPipe", "Test & Sons Plumbing", "SW1A 2AA", "07700 000011", ["plumbing", "heating_hvac"]),
        ("test.owatts", "Test O'Watts", "Testington Electric Ltd", "EC1A 1BB", "07700 000012", ["electrical"]),
        ("test.mcroof", "Test McRoof", "Test Roof & Build Co", "W1D 1NN", "07700 000013", ["roofing", "structural", "damp"]),
        ("testy.mcjoinery", "Testy McJoinery", "Testy's Carpentry & Joinery", "N1 9GU", "07700 000014", ["carpentry", "flooring", "painting"]),
        ("test.ogeneral", "Test O'General", "Test Brothers General Repairs", "SE1 7PB", "07700 000015", ["general", "painting", "tiling"]),
    ]

    result = {}
    for key, full_name, business_name, postcode, phone, activities in contractors_data:
        email = f"{key}@{TEST_EMAIL_DOMAIN}"
        user_id = create_or_get_user(client, email, full_name)
        result[key] = user_id

        # Update profile
        client.table("profiles").update({"full_name": full_name}).eq("id", user_id).execute()

        # Create contractor record (contractor_details auto-created by trigger)
        # activities is a TEXT[] column — pass as a PostgreSQL array literal
        # to avoid PostgREST schema cache issues with array types.
        activities_literal = "{" + ",".join(activities) + "}"
        try:
            client.table("contractors").insert({
                "id": user_id,
                "business_name": business_name,
                "postcode": postcode,
                "phone": phone,
                "activities": activities_literal,
            }).execute()
            print(f"    → Created contractor: {business_name}")
        except Exception as e:
            if "duplicate" in str(e).lower():
                print(f"    → Contractor exists: {business_name}")
            else:
                raise

    return result


# ──────────────────────────────────────────────────────────────────────────
# Jobs, bids, reviews
# ──────────────────────────────────────────────────────────────────────────

def setup_jobs_and_bids(client: Client, homeowners: dict[str, str], contractors: dict[str, str]) -> None:
    """Create jobs and bids."""
    print("\n📋 Creating jobs and bids...")

    jobs_data = [
        # Open jobs (accepting bids)
        {"title": "[TEST] Boiler Replacement", "desc": "Combi boiler needs replacing. No hot water.", "activity": "plumbing", "postcode": "SW1A 1AA", "owner": "test.mctest", "status": "open", "bids": [("test.mcpipe", 85000, "Part P certified"), ("test.ogeneral", 110000, "Full service included")]},
        {"title": "[TEST] Kitchen Rewire", "desc": "Kitchen rewire after extension. New consumer unit.", "activity": "electrical", "postcode": "EC1A 1BB", "owner": "test.otest", "status": "open", "bids": [("test.owatts", 220000, "NICEIC registered"), ("test.ogeneral", 280000, "Includes decoration")]},
        {"title": "[TEST] Roof Leak Repair", "desc": "Flat roof over kitchen leaking at parapet. EPDM replacement.", "activity": "roofing", "postcode": "W1D 1NN", "owner": "tess.testington", "status": "open", "bids": [("test.mcroof", 350000, "10-year guarantee")]},
        {"title": "[TEST] Fence Installation", "desc": "15m feather-edge fencing, 1.8m high. Concrete posts.", "activity": "carpentry", "postcode": "N1 9GU", "owner": "t.esterly", "status": "open", "bids": [("testy.mcjoinery", 120000, "2-day job"), ("test.ogeneral", 95000, "Budget option")]},
        {"title": "[TEST] Bathroom Retile", "desc": "Full bathroom retile. 12sqm walls, 4sqm floor.", "activity": "tiling", "postcode": "SE1 7PB", "owner": "testy.mctestface", "status": "open", "bids": [("test.ogeneral", 180000, "1-week turnaround"), ("testy.mcjoinery", 210000, "Premium finish")]},

        # In-progress jobs
        {"title": "[TEST] Damp Proofing", "desc": "Rising damp in basement. Two walls affected.", "activity": "damp", "postcode": "SW1A 1AA", "owner": "test.mctest", "status": "in_progress", "awarded": "test.mcroof", "winning_bid": 320000, "bids": [("test.mcroof", 320000, "Full tanking system"), ("test.ogeneral", 410000, "Chemical injection DPC")]},
        {"title": "[TEST] Loft Conversion", "desc": "Rear dormer loft conversion. 7m × 4m footprint.", "activity": "structural", "postcode": "EC1A 1BB", "owner": "test.otest", "status": "in_progress", "awarded": "test.mcroof", "winning_bid": 4800000, "bids": [("test.mcroof", 4800000, "Full dormer, 12 weeks"), ("testy.mcjoinery", 5200000, "Includes flooring")]},

        # Draft jobs (not published)
        {"title": "[TEST] Loft Insulation", "desc": "Top up loft insulation to 270mm. 50sqm.", "activity": "general", "postcode": "SW1A 1AA", "owner": "test.mctest", "status": "draft"},
        {"title": "[TEST] Patio Installation", "desc": "Lay 30sqm Indian sandstone patio.", "activity": "landscaping", "postcode": "EC1A 1BB", "owner": "test.otest", "status": "draft"},

        # Cancelled
        {"title": "[TEST] Window Replacement", "desc": "6 sash windows. Grade II listed property.", "activity": "glazing", "postcode": "W1D 1NN", "owner": "tess.testington", "status": "cancelled"},
    ]

    for job_data in jobs_data:
        owner_id = homeowners[job_data["owner"]]

        # Create job
        job = client.table("jobs").insert({
            "user_id": owner_id,
            "title": job_data["title"],
            "description": job_data["desc"],
            "activity": job_data["activity"],
            "postcode": job_data["postcode"],
            "status": job_data["status"],
            "escrow_status": "held" if job_data["status"] in ["in_progress", "awarded"] else ("funds_released" if job_data["status"] == "completed" else "pending"),
        }).execute()

        job_id = job.data[0]["id"]
        print(f"  ✓ Job: {job_data['title']}")

        # Add bids if present
        if "bids" in job_data:
            for contractor_key, amount_pence, note in job_data["bids"]:
                contractor_id = contractors[contractor_key]
                status = "pending"

                # If job has awarded contractor, set status accordingly
                if "awarded" in job_data:
                    status = "accepted" if contractor_key == job_data["awarded"] else "rejected"

                client.table("bids").insert({
                    "job_id": job_id,
                    "contractor_id": contractor_id,
                    "amount_pence": amount_pence,
                    "note": note,
                    "status": status,
                }).execute()


def setup_completed_jobs_with_reviews(client: Client, homeowners: dict[str, str], contractors: dict[str, str]) -> None:
    """Create 3 completed jobs with reviews from both sides."""
    print("\n⭐ Creating completed jobs with reviews...")

    jobs_data = [
        {
            "title": "[TEST] Kitchen Cabinet Installation",
            "desc": "Install 12-unit kitchen. Worktops, plinth and cornice.",
            "activity": "carpentry",
            "postcode": "W1D 1NN",
            "owner": "tess.testington",
            "contractor": "testy.mcjoinery",
            "bid_amount": 185000,
            "client_rating": (5, 5, 4),  # cleanliness, communication, quality
            "client_review": "[TEST REVIEW] Testy arrived on time and the finish is excellent. Would use again.",
            "contractor_rating": (5, 4, 5),
            "contractor_review": "[TEST REVIEW] Great customer, had everything ready. Easy job.",
        },
        {
            "title": "[TEST] Full House Rewire",
            "desc": "Complete rewire of 3-bed Victorian. New consumer unit, all first and second fix.",
            "activity": "electrical",
            "postcode": "N1 9GU",
            "owner": "t.esterly",
            "contractor": "test.owatts",
            "bid_amount": 650000,
            "client_rating": (4, 5, 5),
            "client_review": "[TEST REVIEW] Excellent work. Test O'Watts kept us updated every step. Highly recommended.",
            "contractor_rating": (4, 5, 4),
            "contractor_review": "[TEST REVIEW] T. Esterly was flexible and accommodating. Well prepared.",
        },
        {
            "title": "[TEST] Bathroom Suite Replacement",
            "desc": "Full bathroom suite swap-out. Bath, basin, WC, shower tray.",
            "activity": "plumbing",
            "postcode": "SE1 7PB",
            "owner": "testy.mctestface",
            "contractor": "test.mcpipe",
            "bid_amount": 95000,
            "client_rating": (5, 4, 5),
            "client_review": "[TEST REVIEW] Test McPipe was brilliant — in and out in a day with zero mess.",
            "contractor_rating": (5, 3, 5),
            "contractor_review": "[TEST REVIEW] All sanitaryware was ready. Professional install.",
        },
    ]

    past_date = (datetime.now() - timedelta(days=30)).isoformat()

    for job_data in jobs_data:
        owner_id = homeowners[job_data["owner"]]
        contractor_id = contractors[job_data["contractor"]]

        # Create job
        job = client.table("jobs").insert({
            "user_id": owner_id,
            "title": job_data["title"],
            "description": job_data["desc"],
            "activity": job_data["activity"],
            "postcode": job_data["postcode"],
            "status": "completed",
            "escrow_status": "funds_released",
        }).execute()

        job_id = job.data[0]["id"]
        print(f"  ✓ Completed job: {job_data['title']}")

        # Create bid (accepted)
        client.table("bids").insert({
            "job_id": job_id,
            "contractor_id": contractor_id,
            "amount_pence": job_data["bid_amount"],
            "note": "[TEST] Winning bid",
            "status": "accepted",
        }).execute()

        # Client → Contractor review
        client.table("reviews").insert({
            "job_id": job_id,
            "reviewer_id": owner_id,
            "reviewee_id": contractor_id,
            "reviewer_role": "client",
            "reviewee_role": "contractor",
            "rating_cleanliness": job_data["client_rating"][0],
            "rating_communication": job_data["client_rating"][1],
            "rating_quality": job_data["client_rating"][2],
            "body": job_data["client_review"],
            "content_visible": True,
            "reveal_at": past_date,
        }).execute()

        # Contractor → Client review
        client.table("reviews").insert({
            "job_id": job_id,
            "reviewer_id": contractor_id,
            "reviewee_id": owner_id,
            "reviewer_role": "contractor",
            "reviewee_role": "client",
            "rating_cleanliness": job_data["contractor_rating"][0],
            "rating_communication": job_data["contractor_rating"][1],
            "rating_quality": job_data["contractor_rating"][2],
            "body": job_data["contractor_review"],
            "content_visible": True,
            "reveal_at": past_date,
        }).execute()


# ──────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("🌱 Seeding stable-gig database...\n")

    client = get_client()
    homeowners = setup_homeowners(client)
    contractors = setup_contractors(client)
    setup_jobs_and_bids(client, homeowners, contractors)
    setup_completed_jobs_with_reviews(client, homeowners, contractors)

    print("\n✅ Seed complete!\n")
    print("Test users:")
    print("  Homeowners: test.mctest, test.otest, tess.testington, t.esterly, testy.mctestface")
    print("  Contractors: test.mcpipe, test.owatts, test.mcroof, testy.mcjoinery, test.ogeneral")
    print(f"  Password: {TEST_PASSWORD}")
    print(f"  Email domain: @{TEST_EMAIL_DOMAIN}")


if __name__ == "__main__":
    main()
