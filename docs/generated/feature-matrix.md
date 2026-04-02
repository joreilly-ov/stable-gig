# API Feature Matrix (Generated)

Source of truth: FastAPI router table plus discovered pytest call sites.

| Method | Path | Handler | Matching test files |
|---|---|---|---|
| GET | `/address/autocomplete` | `address_autocomplete` | test_address_router.py |
| GET | `/address/zip` | `zip_lookup` | test_address_router.py |
| POST | `/analyse` | `analyse_video` | test_analyse_router.py |
| POST | `/analyse/photos` | `analyse_photos` | test_photo_analysis_router.py |
| GET | `/auth/config` | `auth_config` | test_auth_router.py |
| POST | `/auth/forgot-password` | `forgot_password` | test_auth_router.py |
| POST | `/auth/login/password` | `login_with_password` | test_auth_router.py |
| POST | `/auth/magic-link` | `send_magic_link` | test_auth_router.py |
| POST | `/auth/register` | `register_with_password` | test_auth_router.py |
| POST | `/auth/reset-password` | `reset_password` | test_auth_router.py |
| POST | `/auth/verify` | `verify_otp` | test_auth_router.py |
| GET | `/config/feature-flags` | `feature_flags` | (none found) |
| GET | `/escrow/config` | `escrow_config` | test_escrow_router.py |
| GET | `/jobs/{job_id}/contractors/matches` | `match_contractors_for_job` | (none found) |
| GET | `/jobs/{job_id}/escrow` | `get_escrow_status` | (none found) |
| POST | `/jobs/{job_id}/escrow/initiate` | `initiate_escrow` | (none found) |
| POST | `/jobs/{job_id}/escrow/refund` | `refund_escrow` | (none found) |
| POST | `/jobs/{job_id}/escrow/release` | `release_escrow` | (none found) |
| GET | `/jobs/{job_id}/milestones` | `list_milestones` | (none found) |
| POST | `/jobs/{job_id}/milestones` | `create_milestones` | (none found) |
| PATCH | `/jobs/{job_id}/milestones/{milestone_id}` | `action_milestone` | (none found) |
| POST | `/jobs/{job_id}/milestones/{milestone_id}/photos` | `submit_photo` | (none found) |
| GET | `/jobs/{job_id}/questions` | `list_questions` | (none found) |
| POST | `/jobs/{job_id}/questions` | `ask_question` | (none found) |
| PATCH | `/jobs/{job_id}/questions/{question_id}` | `answer_question` | (none found) |
| POST | `/jobs/{job_id}/rfp` | `generate_rfp` | (none found) |
| POST | `/me/contractor/connect-onboard` | `connect_onboard` | test_contractor_connect_router.py |
| GET | `/me/contractor/connect-status` | `connect_status` | test_contractor_connect_router.py |
| POST | `/me/contractor/embed-profile` | `embed_my_profile` | test_contractor_matching_router.py |
| GET | `/me/metadata` | `get_metadata` | test_user_metadata_router.py |
| PATCH | `/me/metadata` | `update_metadata` | test_user_metadata_router.py |
| GET | `/me/profile` | `get_profile` | test_profiles_router.py |
| PATCH | `/me/profile` | `update_profile` | test_profiles_router.py |
| DELETE | `/notifications/subscribe` | `unsubscribe` | (none found) |
| POST | `/notifications/subscribe` | `subscribe` | test_notifications_router.py |
| GET | `/notifications/vapid-public-key` | `vapid_public_key` | test_notifications_router.py |
| POST | `/reviews` | `submit_review` | test_reviews_router.py |
| GET | `/reviews/contractor/{contractor_id}` | `list_contractor_reviews` | (none found) |
| GET | `/reviews/summary/{contractor_id}` | `contractor_review_summary` | (none found) |
| DELETE | `/reviews/{review_id}` | `delete_review` | (none found) |
