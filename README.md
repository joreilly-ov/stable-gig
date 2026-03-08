# Home Repair Video Analyser

A minimal proof-of-concept web app that lets you upload a short video of a home repair issue and receive an AI-generated structured assessment powered by **Google Gemini 2.0 Flash**.

## Project structure

```
/backend
  main.py           # FastAPI app — POST /analyse endpoint
  requirements.txt
/frontend
  index.html        # Single-file vanilla JS frontend
.env.example
README.md
```

## Prerequisites

- Python 3.11+
- A [Google AI Studio](https://aistudio.google.com/) API key with Gemini access

## Setup

1. **Clone and enter the repo**

   ```bash
   git clone <repo-url>
   cd <repo>
   ```

2. **Create your `.env` file**

   ```bash
   cp .env.example .env
   # Edit .env and set your key:
   # GEMINI_API_KEY=your_key_here
   ```

3. **Install backend dependencies**

   ```bash
   cd backend
   pip install -r requirements.txt
   ```

4. **Run the backend**

   ```bash
   uvicorn main:app --reload --port 8000
   ```

5. **Open the frontend**

   Open `frontend/index.html` directly in your browser **or** serve it via any static file server. The backend already allows all CORS origins for local dev, so no proxy is required.

## API

### `POST /analyse`

Accepts a `multipart/form-data` upload with a single field named `file` containing a video.

**Response** (JSON):

```json
{
  "problem_type": "plumbing",
  "description": "A dripping tap in the kitchen sink …",
  "location_in_home": "kitchen",
  "urgency": "low",
  "materials_involved": ["copper pipe", "tap washer"],
  "clarifying_questions": [
    "How long has the tap been dripping?",
    "Is the drip from the hot or cold side?",
    "Have you noticed any water damage under the sink?"
  ]
}
```

## Deploying to Railway

1. Push this repo to GitHub.
2. Create a new Railway project → **Deploy from GitHub repo**.
3. Add a service pointing to the `/backend` directory.
4. Set the start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Add the `GEMINI_API_KEY` environment variable in Railway's settings.
6. Serve `/frontend/index.html` via Railway's static file hosting or a separate Nginx service.
