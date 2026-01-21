import os
import json
from typing import Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi import Request
from fastapi.responses import FileResponse
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware
from fastapi import Body
from fastapi.responses import Response


# -----------------------------
# App + paths
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = "/data/wos_data.json"
CARDS_FILE = os.path.join(BASE_DIR, "Cards.json")
WEB_DIR = Path(BASE_DIR) / "web"


app = FastAPI(title="World of Simia — Collection Server")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://magnummonkey.github.io",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.options("/{path:path}")
def options_handler(path: str):
    return Response(status_code=204)



# Serve /static/*
app.mount("/static", StaticFiles(directory="static"), name="static")

# -----------------------------
# Helpers
# -----------------------------
def load_cards_db() -> Dict[str, Any]:
    if not os.path.exists(CARDS_FILE):
        return {}
    try:
        with open(CARDS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}

def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}

def load_user_data() -> Dict[str, Any]:
    return load_json(DATA_FILE)

def save_user_data(data: Dict[str, Any]) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

# -----------------------------
# Routes
# -----------------------------
@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "World of Simia collection server running"
    }

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/cards")
def get_cards_db():
    return load_cards_db()

@app.get("/me/collection")
def get_my_collection(user_id: str):
    data = load_user_data()
    user = data.get(user_id)

    if not isinstance(user, dict):
        raise HTTPException(status_code=404, detail="User not found")

    cards = user.get("cards", [])
    if not isinstance(cards, list):
        cards = []

    return {
        "user_id": user_id,
        "count": len(cards),
        "cards": cards
    }


@app.get("/api/collection/{user_id}")
def api_collection(user_id: str, request: Request):
    data = load_user_data()
    user = data.get(user_id)

    if not isinstance(user, dict):
        raise HTTPException(status_code=404, detail="User not found in wos_data.json")

    owned = user.get("cards", [])
    if not isinstance(owned, list):
        owned = []

    cards_db = load_cards_db()

    # Build absolute base like: http://127.0.0.1:8000
    base = str(request.base_url).rstrip("/")

    cards_out = []
    for card_id in owned:
        info = cards_db.get(card_id, {}) if isinstance(cards_db, dict) else {}
        name = info.get("name", card_id)

        # Prefer image_url_web if present (your bot uses this too)
        img = (
            info.get("image_url_web")
            or info.get("image_url_discord")
            or info.get("image_url")
            or ""
        )

        img = (img or "").strip()

        # If it’s a relative /static/... path, make it absolute for the browser
        if img.startswith("/"):
            img = f"{base}{img}"

        cards_out.append({
            "card_id": card_id,
            "name": name,
            "image_url": img or None,
            "rarity": info.get("rarity"),
        })

    return {
        "user_id": user_id,
        "count": len(cards_out),
        "cards": cards_out,
    }

WEB_DIR = Path(__file__).parent / "web"
CARDS_FILE = Path(__file__).parent / "Cards.json"

@app.get("/collection")
def collection_page():
    return FileResponse(WEB_DIR / "collection.html")

@app.get("/cards")
def cards_db():
    # Returns the full Cards.json so collection.html can look up names/images
    return load_cards_db()
@app.get("/collection")
def collection_page():
    return FileResponse(WEB_DIR / "collection.html")

@app.get("/collection/{user_id}")
def collection_page_user(user_id: str):
    # user_id is used by the frontend JS (URL), so we just serve the same page.
    return FileResponse(WEB_DIR / "collection.html")

@app.post("/api/collection/{user_id}/add")
def api_add_card(user_id: str, payload: Dict[str, Any] = Body(...)):
    card_id = payload.get("card_id")
    if not card_id or not isinstance(card_id, str):
        raise HTTPException(status_code=400, detail="Missing or invalid card_id")

    data = load_user_data()
    user = data.get(user_id)
    if not isinstance(user, dict):
        user = {"cards": []}
        data[user_id] = user

    cards = user.get("cards")
    if not isinstance(cards, list):
        cards = []
        user["cards"] = cards

    # Prevent duplicates (no duplicates allowed yet)
    if card_id in cards:
        return {
            "status": "exists",
            "user_id": user_id,
            "card_id": card_id,
            "count": len(cards),
        }

    cards.append(card_id)
    save_user_data(data)

    return {"status": "ok", "user_id": user_id, "added": card_id, "count": len(cards)}


@app.get("/debug/cors")
def debug_cors():
    return {"ok": True}

@app.get("/")
def root():
    return {"running": "SERVER.PY", "cors": "ON"}

@app.post("/api/collection/{user_id}/dedupe")
def api_dedupe(user_id: str):
    data = load_user_data()
    user = data.get(user_id)

    if not isinstance(user, dict):
        user = {"cards": []}
        data[user_id] = user

    cards = user.get("cards", [])
    if not isinstance(cards, list):
        cards = []
        user["cards"] = cards

    seen = set()
    deduped = []
    for cid in cards:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)

    removed = len(cards) - len(deduped)
    user["cards"] = deduped
    save_user_data(data)

    return {"status": "ok", "user_id": user_id, "removed": removed, "count": len(deduped), "cards": deduped}


    

    

