import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from app.routers import analyse, auth, profiles, address

app = FastAPI(
    title="Home Repair Video Analyser",
    description="Upload a home repair video; get a structured Gemini 2.5 Flash assessment.",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(analyse.router)
app.include_router(auth.router)
app.include_router(profiles.router)
app.include_router(address.router)

# --- Frontend ---
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")


@app.get("/", include_in_schema=False)
def serve_frontend():
    return FileResponse(os.path.join(_STATIC_DIR, "index.html"))
