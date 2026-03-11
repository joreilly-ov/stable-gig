/**
 * Supabase Edge Function: analyse
 *
 * Accepts a multipart/form-data POST with a `file` field (video),
 * uploads it to the Gemini Files API, waits for processing, then
 * returns a structured JSON home-repair assessment.
 *
 * Required secret (set via Supabase dashboard → Project Settings → Edge Functions):
 *   GEMINI_API_KEY
 */

const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
const MODEL = "gemini-2.0-flash";
const GEMINI_BASE = "https://generativelanguage.googleapis.com";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers":
    "authorization, x-client-info, apikey, content-type",
};

const ANALYSIS_PROMPT = `You are a home repair assessment assistant. Analyse this video and extract the following as JSON:
- problem_type: (e.g. plumbing, electrical, structural, damp, general)
- description: a plain English summary of the issue visible
- location_in_home: best guess at where this is (e.g. bathroom, kitchen, external wall)
- urgency: low / medium / high / emergency
- materials_involved: list of materials or components visible
- clarifying_questions: list of 2-3 questions a tradesperson would want answered before quoting

Return only valid JSON, no markdown.`;

Deno.serve(async (req: Request) => {
  // Handle CORS preflight
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }

  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  if (!GEMINI_API_KEY) {
    return json(
      {
        error:
          "GEMINI_API_KEY is not configured. Add it in Supabase → Project Settings → Edge Functions → Secrets.",
      },
      500,
    );
  }

  try {
    const formData = await req.formData();
    const file = formData.get("file") as File | null;

    if (!file) {
      return json({ error: "No file field in request" }, 400);
    }
    if (!file.type.startsWith("video/")) {
      return json({ error: "Uploaded file must be a video" }, 400);
    }

    // ── Step 1: Upload video to Gemini Files API ──────────────────────────────
    const fileBytes = await file.arrayBuffer();
    const boundary = "boundary" + crypto.randomUUID().replace(/-/g, "");

    const metaJson = JSON.stringify({ file: { display_name: file.name } });
    const enc = new TextEncoder();

    const parts: Uint8Array[] = [
      enc.encode(`--${boundary}\r\nContent-Type: application/json; charset=utf-8\r\n\r\n${metaJson}\r\n`),
      enc.encode(`--${boundary}\r\nContent-Type: ${file.type}\r\n\r\n`),
      new Uint8Array(fileBytes),
      enc.encode(`\r\n--${boundary}--`),
    ];

    const totalLen = parts.reduce((s, p) => s + p.length, 0);
    const body = new Uint8Array(totalLen);
    let offset = 0;
    for (const part of parts) {
      body.set(part, offset);
      offset += part.length;
    }

    const uploadResp = await fetch(
      `${GEMINI_BASE}/upload/v1beta/files?key=${GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: {
          "X-Goog-Upload-Protocol": "multipart",
          "Content-Type": `multipart/related; boundary=${boundary}`,
        },
        body,
      },
    );

    if (!uploadResp.ok) {
      const detail = await uploadResp.text();
      throw new Error(
        `Gemini file upload failed (${uploadResp.status}): ${detail}`,
      );
    }

    const uploadData = await uploadResp.json();
    const geminiFile = uploadData.file;
    if (!geminiFile?.uri) {
      throw new Error("Gemini upload response missing file URI");
    }

    // ── Step 2: Poll until the file is ACTIVE ────────────────────────────────
    let state: string = geminiFile.state ?? "PROCESSING";
    let polls = 0;

    while (state === "PROCESSING" && polls < 30) {
      await sleep(2000);
      const statusResp = await fetch(
        `${GEMINI_BASE}/v1beta/${geminiFile.name}?key=${GEMINI_API_KEY}`,
      );
      if (statusResp.ok) {
        const statusData = await statusResp.json();
        state = statusData.state ?? state;
      }
      polls++;
    }

    if (state !== "ACTIVE") {
      throw new Error(`Gemini file ended in unexpected state: ${state}`);
    }

    // ── Step 3: Generate the assessment ──────────────────────────────────────
    const genResp = await fetch(
      `${GEMINI_BASE}/v1beta/models/${MODEL}:generateContent?key=${GEMINI_API_KEY}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contents: [
            {
              parts: [
                { text: ANALYSIS_PROMPT },
                {
                  file_data: {
                    mime_type: file.type,
                    file_uri: geminiFile.uri,
                  },
                },
              ],
            },
          ],
        }),
      },
    );

    if (!genResp.ok) {
      const detail = await genResp.text();
      if (genResp.status === 429) {
        throw new Error(
          `429: Gemini API quota exceeded. Check billing at https://aistudio.google.com/`,
        );
      }
      throw new Error(
        `Gemini content generation failed (${genResp.status}): ${detail}`,
      );
    }

    const genData = await genResp.json();
    let text: string =
      (genData.candidates?.[0]?.content?.parts?.[0]?.text ?? "").trim();

    // Strip optional markdown code fences
    if (text.startsWith("```")) {
      text = text.replace(/^```(?:json)?\s*/m, "").replace(/\s*```$/m, "").trim();
    }

    const analysis = JSON.parse(text);
    return json(analysis, 200);
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    const status =
      msg.includes("429") || msg.toLowerCase().includes("quota") ? 429 : 500;
    return json({ error: msg }, status);
  }
});

// ── Helpers ───────────────────────────────────────────────────────────────────

function json(data: unknown, status: number): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
