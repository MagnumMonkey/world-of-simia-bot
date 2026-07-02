from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
from pathlib import Path

API_VERSION = "profile-route-1"
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://magnummonkey.github.io",
        "http://localhost:8000",
        "http://localhost:5500",
        "http://127.0.0.1:5500",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DATA_FILE = Path("/data/wos_data.json")

def load_data():
    if not DATA_FILE.exists():
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)
        
def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

class AddCardRequest(BaseModel):
    card_id: str


@app.post("/api/collection/{user_id}/add")
def add_card(user_id: str, payload: AddCardRequest):
    data = load_data()

    user = data.setdefault(user_id, {})
    cards = user.setdefault("cards", [])

    if payload.card_id not in cards:
        cards.append(payload.card_id)

    save_data(data)

    return {
        "ok": True,
        "user_id": user_id,
        "card_id": payload.card_id,
        "cards": cards
    }

@app.get("/api/collection/{user_id}")
def get_collection(
    user_id: str, 
    page: int = 1, 
    limit: int = 18
):
    data = load_data()
    user = data.get(user_id, {"cards": []})
    cards = user.get("cards", [])

    total = len(cards)
    total_pages = (total + limit - 1) // limit

    # Cap the limit at 18
    if limit > 18:
        limit = 18

    if page < 1:
        page = 1

    start = (page - 1) * limit
    end = start + limit
    paginated_cards = cards[start:end]

    return {
        "user_id": user_id,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "cards": paginated_cards
    }

def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)


def api_xp_required_for_level(level: int) -> int:
    return min(20 + (level - 1) * 5, 100)


def ensure_api_user_record(data: dict, user_id: str) -> dict:
    if user_id not in data or not isinstance(data[user_id], dict):
        data[user_id] = {}

    user = data[user_id]
    user.setdefault("cards", [])
    user.setdefault("level", 1)
    user.setdefault("xp", 0)
    user.setdefault("banana_chips", 0)
    user.setdefault("catalog_submission_adjustment", 0)

    return user


def api_recorded_catalog_submission_count(data: dict, user_id: str) -> int:
    catalog = data.get("community_catalog", {})
    if not isinstance(catalog, dict):
        return 0

    return sum(
        1 for entry in catalog.values()
        if isinstance(entry, dict) and str(entry.get("submitted_by")) == str(user_id)
    )


def api_displayed_catalog_submission_count(data: dict, user_id: str) -> int:
    user = ensure_api_user_record(data, user_id)
    recorded = api_recorded_catalog_submission_count(data, user_id)
    adjustment = int(user.get("catalog_submission_adjustment", 0))
    return max(0, recorded + adjustment)


@app.get("/api/profile/{user_id}")
def get_profile(user_id: str):
    data = load_data()
    user = ensure_api_user_record(data, user_id)

    cards = user.get("cards", []) or []
    unique_cards = list(dict.fromkeys(cards))

    level = int(user.get("level", 1))
    xp = int(user.get("xp", 0))
    banana_chips = int(user.get("banana_chips", 0))

    xp_required = api_xp_required_for_level(level)
    xp_in_level = max(0, min(xp, xp_required))
    xp_remaining = max(0, xp_required - xp_in_level)

    recorded_catalog = api_recorded_catalog_submission_count(data, user_id)
    displayed_catalog = api_displayed_catalog_submission_count(data, user_id)

    save_data(data)

    return {
        "user_id": user_id,
        "level": level,
        "xp": xp,
        "xp_in_level": xp_in_level,
        "xp_required": xp_required,
        "xp_remaining": xp_remaining,
        "banana_chips": banana_chips,
        "cards_owned": len(unique_cards),
        "catalog_submissions": displayed_catalog,
        "catalog_submissions_recorded": recorded_catalog,
        "catalog_submission_adjustment": int(user.get("catalog_submission_adjustment", 0))
    }
