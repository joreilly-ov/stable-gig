/**
 * Supabase Edge Function: contractors
 *
 * CRUD operations for contractor onboarding.
 *
 * Routes (method + path suffix):
 *   POST   /contractors          → register (upsert) a contractor
 *   GET    /contractors          → fetch the calling contractor's record
 *   PATCH  /contractors          → update contractor fields
 *   POST   /contractors/profile  → upsert extended profile
 *   GET    /contractors/profile  → fetch extended profile
 *
 * All routes require a valid Supabase JWT (Authorization: Bearer <token>).
 *
 * Required secrets (Supabase dashboard → Edge Functions → Secrets):
 *   SUPABASE_URL
 *   SUPABASE_ANON_KEY
 *   SUPABASE_SERVICE_ROLE_KEY
 */

import { createClient } from "https://esm.sh/@supabase/supabase-js@2";
import { z } from "https://deno.land/x/zod@v3.23.8/mod.ts";

// ── Canonical activity list ───────────────────────────────────────────────────

export const ACTIVITIES = [
  "plumbing",
  "electrical",
  "structural",
  "damp",
  "roofing",
  "carpentry",
  "painting",
  "tiling",
  "flooring",
  "heating_hvac",
  "glazing",
  "landscaping",
  "general",
] as const;

export type Activity = (typeof ACTIVITIES)[number];

// ── Zod schemas ───────────────────────────────────────────────────────────────

/** Schema for registering or updating a contractor's core record. */
export const ContractorUpsertSchema = z.object({
  business_name: z
    .string()
    .min(1, "Business name is required")
    .max(200, "Business name must be 200 characters or fewer"),
  postcode: z
    .string()
    .min(1, "Postcode is required")
    .max(20, "Postcode must be 20 characters or fewer"),
  phone: z
    .string()
    .regex(
      /^\+?[\d\s\-().]{7,25}$/,
      "Phone must be a valid number (7–25 digits, optional +, spaces, hyphens, parentheses)",
    ),
  activities: z
    .array(z.enum(ACTIVITIES))
    .min(1, "Select at least one activity")
    .max(ACTIVITIES.length, `Cannot exceed ${ACTIVITIES.length} activities`),
});

export type ContractorUpsert = z.infer<typeof ContractorUpsertSchema>;

/** Schema for upserting the extended contractor profile. */
export const ContractorProfileUpsertSchema = z.object({
  license_number: z.string().max(100).optional().nullable(),
  insurance_verified: z.boolean().optional(),
  years_experience: z
    .number()
    .int("Must be a whole number")
    .min(0, "Cannot be negative")
    .max(99, "Must be 99 or fewer")
    .optional()
    .nullable(),
});

export type ContractorProfileUpsert = z.infer<
  typeof ContractorProfileUpsertSchema
>;

/** Full contractor response shape returned to callers. */
export const ContractorResponseSchema = ContractorUpsertSchema.extend({
  id: z.string().uuid(),
  user_id: z.string().uuid(),
  created_at: z.string().datetime(),
});

export type ContractorResponse = z.infer<typeof ContractorResponseSchema>;

/** Full contractor profile response shape. */
export const ContractorProfileResponseSchema =
  ContractorProfileUpsertSchema.extend({
    id: z.string().uuid(),
    updated_at: z.string().datetime(),
  });

export type ContractorProfileResponse = z.infer<
  typeof ContractorProfileResponseSchema
>;

// ── CORS headers ──────────────────────────────────────────────────────────────

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function errorResponse(message: string, status: number): Response {
  return jsonResponse({ error: message }, status);
}

// ── ContractorService ─────────────────────────────────────────────────────────

/**
 * Thin service layer wrapping Supabase queries for contractors.
 * Constructed with a user-scoped client (RLS applies) so each method
 * operates only on rows the authenticated user is allowed to access.
 */
export class ContractorService {
  constructor(
    private readonly db: ReturnType<typeof createClient>,
    private readonly userId: string,
  ) {}

  /** Register a new contractor or replace an existing one for this user. */
  async upsert(payload: ContractorUpsert): Promise<ContractorResponse> {
    const { data, error } = await this.db
      .from("contractors")
      .upsert(
        { ...payload, user_id: this.userId },
        { onConflict: "user_id", ignoreDuplicates: false },
      )
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorResponseSchema.parse(data);
  }

  /** Fetch the contractor record for the current user. */
  async get(): Promise<ContractorResponse | null> {
    const { data, error } = await this.db
      .from("contractors")
      .select("*")
      .eq("user_id", this.userId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return null;
    return ContractorResponseSchema.parse(data);
  }

  /** Partially update the contractor record for the current user. */
  async update(
    payload: Partial<ContractorUpsert>,
  ): Promise<ContractorResponse> {
    const { data, error } = await this.db
      .from("contractors")
      .update(payload)
      .eq("user_id", this.userId)
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorResponseSchema.parse(data);
  }

  /** Upsert the extended profile (keyed to the contractor row id). */
  async upsertProfile(
    contractorId: string,
    payload: ContractorProfileUpsert,
  ): Promise<ContractorProfileResponse> {
    const { data, error } = await this.db
      .from("contractor_profiles")
      .upsert(
        { id: contractorId, ...payload },
        { onConflict: "id", ignoreDuplicates: false },
      )
      .select()
      .single();

    if (error) throw new Error(error.message);
    return ContractorProfileResponseSchema.parse(data);
  }

  /** Fetch the extended profile for this user's contractor. */
  async getProfile(
    contractorId: string,
  ): Promise<ContractorProfileResponse | null> {
    const { data, error } = await this.db
      .from("contractor_profiles")
      .select("*")
      .eq("id", contractorId)
      .maybeSingle();

    if (error) throw new Error(error.message);
    if (!data) return null;
    return ContractorProfileResponseSchema.parse(data);
  }
}

// ── Edge Function handler ─────────────────────────────────────────────────────

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  // ── Auth ──────────────────────────────────────────────────────────────────
  const authHeader = req.headers.get("Authorization");
  if (!authHeader?.startsWith("Bearer ")) {
    return errorResponse("Missing or malformed Authorization header", 401);
  }
  const jwt = authHeader.slice(7);

  const supabaseUrl = Deno.env.get("SUPABASE_URL");
  const anonKey = Deno.env.get("SUPABASE_ANON_KEY");
  if (!supabaseUrl || !anonKey) {
    return errorResponse("Server misconfiguration: missing Supabase env vars", 500);
  }

  // User-scoped client — RLS applies automatically
  const db = createClient(supabaseUrl, anonKey, {
    global: { headers: { Authorization: `Bearer ${jwt}` } },
  });

  const {
    data: { user },
    error: authError,
  } = await db.auth.getUser();

  if (authError || !user) {
    return errorResponse("Invalid or expired token", 401);
  }

  const service = new ContractorService(db, user.id);
  const url = new URL(req.url);

  // ── Route: /contractors/profile ───────────────────────────────────────────
  if (url.pathname.endsWith("/contractors/profile")) {
    try {
      const contractor = await service.get();
      if (!contractor) {
        return errorResponse(
          "Contractor record not found. Register first via POST /contractors.",
          404,
        );
      }

      if (req.method === "GET") {
        const profile = await service.getProfile(contractor.id);
        return jsonResponse(profile ?? { id: contractor.id });
      }

      if (req.method === "POST") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorProfileUpsertSchema.safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const profile = await service.upsertProfile(
          contractor.id,
          parsed.data,
        );
        return jsonResponse(profile, 200);
      }

      return errorResponse("Method not allowed", 405);
    } catch (err) {
      return errorResponse(
        err instanceof Error ? err.message : "Internal error",
        500,
      );
    }
  }

  // ── Route: /contractors ───────────────────────────────────────────────────
  if (url.pathname.endsWith("/contractors")) {
    try {
      if (req.method === "GET") {
        const contractor = await service.get();
        if (!contractor) return jsonResponse(null, 404);
        return jsonResponse(contractor);
      }

      if (req.method === "POST") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorUpsertSchema.safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const contractor = await service.upsert(parsed.data);
        return jsonResponse(contractor, 201);
      }

      if (req.method === "PATCH") {
        const raw = await req.json().catch(() => ({}));
        const parsed = ContractorUpsertSchema.partial().safeParse(raw);
        if (!parsed.success) {
          return jsonResponse({ errors: parsed.error.flatten() }, 422);
        }
        const contractor = await service.update(parsed.data);
        return jsonResponse(contractor);
      }

      return errorResponse("Method not allowed", 405);
    } catch (err) {
      return errorResponse(
        err instanceof Error ? err.message : "Internal error",
        500,
      );
    }
  }

  return errorResponse("Not found", 404);
});
