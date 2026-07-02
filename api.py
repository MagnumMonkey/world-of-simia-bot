from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
import os

# Force Railway deploy with profile route
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
ADMIN_KEY = os.getenv("WOS_ADMIN_KEY", "")

LEVEL_REWARD_CHIPS = 25
CATALOG_SUBMIT_XP_REWARD = 10
CATALOG_SUBMIT_CHIPS_REWARD = 10
LEVEL_REWARD_CHIPS = 25
SHOP_BUY_XP_REWARD = 5

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

def api_add_xp(user: dict, amount: int) -> list[str]:
    """
    Adds XP, handles level-ups, and awards Banana Chips.
    Returns level-up messages.
    """
    user["level"] = int(user.get("level", 1))
    user["xp"] = int(user.get("xp", 0))
    user["banana_chips"] = int(user.get("banana_chips", 0))

    messages = []
    user["xp"] += amount

    while True:
        needed = api_xp_required_for_level(user["level"])

        if user["xp"] < needed:
            break

        user["xp"] -= needed
        user["level"] += 1
        user["banana_chips"] += LEVEL_REWARD_CHIPS

        messages.append(
            f"🐒 **Level Up!** You reached **Level {user['level']}** and earned **{LEVEL_REWARD_CHIPS} Banana Chips** 🍌"
        )

    return messages

class RewardRequest(BaseModel):
    xp: int = 0
    banana_chips: int = 0


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

class SetProfileRequest(BaseModel):
    level: int
    xp: int
    banana_chips: int


class SetCatalogCountRequest(BaseModel):
    total: int

class CatalogSubmitRequest(BaseModel):
    card_id: str
    thread_id: int

class ShopBuyRequest(BaseModel):
    card_id: str
    price: int


def check_admin_key(x_wos_admin_key: str | None):
    if not ADMIN_KEY:
        raise HTTPException(status_code=500, detail="WOS_ADMIN_KEY is not set on the API.")
    if x_wos_admin_key != ADMIN_KEY:
        raise HTTPException(status_code=403, detail="Invalid admin key.")


@app.post("/api/profile/{user_id}/set")
def set_profile(
    user_id: str,
    payload: SetProfileRequest,
    x_wos_admin_key: str | None = Header(default=None)
):
    check_admin_key(x_wos_admin_key)

    if payload.level < 1:
        raise HTTPException(status_code=400, detail="Level must be at least 1.")
    if payload.xp < 0 or payload.banana_chips < 0:
        raise HTTPException(status_code=400, detail="XP and Banana Chips cannot be negative.")

    data = load_data()
    user = ensure_api_user_record(data, user_id)

    user["level"] = payload.level
    user["xp"] = payload.xp
    user["banana_chips"] = payload.banana_chips

    save_data(data)

    return {
        "ok": True,
        "user_id": user_id,
        "level": user["level"],
        "xp": user["xp"],
        "banana_chips": user["banana_chips"]
    }


@app.post("/api/profile/{user_id}/catalog_count/set")
def set_catalog_count(
    user_id: str,
    payload: SetCatalogCountRequest,
    x_wos_admin_key: str | None = Header(default=None)
):
    check_admin_key(x_wos_admin_key)

    if payload.total < 0:
        raise HTTPException(status_code=400, detail="Catalog count cannot be negative.")

    data = load_data()
    user = ensure_api_user_record(data, user_id)

    recorded = api_recorded_catalog_submission_count(data, user_id)
    user["catalog_submission_adjustment"] = payload.total - recorded

    save_data(data)

    return {
        "ok": True,
        "user_id": user_id,
        "recorded": recorded,
        "adjustment": user["catalog_submission_adjustment"],
        "displayed_total": api_displayed_catalog_submission_count(data, user_id)
    }


def ensure_api_community_catalog(data: dict) -> dict:
    if "community_catalog" not in data or not isinstance(data["community_catalog"], dict):
        data["community_catalog"] = {}
    return data["community_catalog"]


def api_add_xp(user: dict, amount: int) -> list[str]:
    """
    Adds XP, handles level-ups, and awards Banana Chips.
    Returns level-up messages.
    """
    user["level"] = int(user.get("level", 1))
    user["xp"] = int(user.get("xp", 0))
    user["banana_chips"] = int(user.get("banana_chips", 0))

    messages = []
    user["xp"] += amount

    while True:
        needed = api_xp_required_for_level(user["level"])

        if user["xp"] < needed:
            break

        user["xp"] -= needed
        user["level"] += 1
        user["banana_chips"] += LEVEL_REWARD_CHIPS

        messages.append(
            f"🐒 **Level Up!** You reached **Level {user['level']}** and earned **{LEVEL_REWARD_CHIPS} Banana Chips** 🍌"
        )

    return messages

@app.get("/api/catalog/{card_id}")
def get_catalog_entry(card_id: str):
    data = load_data()
    catalog = data.get("community_catalog", {})

    if not isinstance(catalog, dict) or card_id not in catalog:
        return {
            "exists": False,
            "card_id": card_id
        }

    entry = catalog.get(card_id, {})

    return {
        "exists": True,
        "card_id": card_id,
        "thread_id": entry.get("thread_id"),
        "submitted_by": entry.get("submitted_by")
    }


@app.post("/api/catalog/{user_id}/submit")
def submit_to_catalog(
    user_id: str,
    payload: CatalogSubmitRequest,
    x_wos_admin_key: str | None = Header(default=None)
):
    check_admin_key(x_wos_admin_key)

    data = load_data()
    user = ensure_api_user_record(data, user_id)
    catalog = ensure_api_community_catalog(data)

    cards = user.get("cards", []) or []

    if payload.card_id not in cards:
        raise HTTPException(
            status_code=400,
            detail="User does not own this card."
        )

    if payload.card_id in catalog:
        raise HTTPException(
            status_code=409,
            detail="That card is already in the Community Catalog."
        )

    catalog[payload.card_id] = {
        "thread_id": payload.thread_id,
        "submitted_by": str(user_id)
    }

    level_msgs = api_add_xp(user, CATALOG_SUBMIT_XP_REWARD)
    user["banana_chips"] = int(user.get("banana_chips", 0)) + CATALOG_SUBMIT_CHIPS_REWARD

    recorded_catalog = api_recorded_catalog_submission_count(data, user_id)
    displayed_catalog = api_displayed_catalog_submission_count(data, user_id)

    save_data(data)

    return {
        "ok": True,
        "user_id": user_id,
        "card_id": payload.card_id,
        "thread_id": payload.thread_id,
        "xp_reward": CATALOG_SUBMIT_XP_REWARD,
        "chips_reward": CATALOG_SUBMIT_CHIPS_REWARD,
        "level": user.get("level", 1),
        "xp": user.get("xp", 0),
        "banana_chips": user.get("banana_chips", 0),
        "catalog_submissions_recorded": recorded_catalog,
        "catalog_submissions": displayed_catalog,
        "level_messages": level_msgs
    }

@app.post("/api/profile/{user_id}/reward")
def reward_profile(
    user_id: str,
    payload: RewardRequest,
    x_wos_admin_key: str | None = Header(default=None)
):
    check_admin_key(x_wos_admin_key)

    if payload.xp < 0 or payload.banana_chips < 0:
        raise HTTPException(
            status_code=400,
            detail="XP and Banana Chips rewards cannot be negative."
        )

    data = load_data()
    user = ensure_api_user_record(data, user_id)

    level_messages = []

    if payload.xp > 0:
        level_messages = api_add_xp(user, payload.xp)

    if payload.banana_chips > 0:
        user["banana_chips"] = int(user.get("banana_chips", 0)) + payload.banana_chips

    save_data(data)

    level = int(user.get("level", 1))
    xp = int(user.get("xp", 0))
    banana_chips = int(user.get("banana_chips", 0))

    xp_required = api_xp_required_for_level(level)
    xp_in_level = max(0, min(xp, xp_required))
    xp_remaining = max(0, xp_required - xp_in_level)

    return {
        "ok": True,
        "user_id": user_id,
        "xp_reward": payload.xp,
        "chips_reward": payload.banana_chips,
        "level": level,
        "xp": xp,
        "xp_in_level": xp_in_level,
        "xp_required": xp_required,
        "xp_remaining": xp_remaining,
        "banana_chips": banana_chips,
        "level_messages": level_messages

@app.post("/api/shop/{user_id}/buy")
def buy_shop_card(
    user_id: str,
    payload: ShopBuyRequest,
    x_wos_admin_key: str | None = Header(default=None)
):
    check_admin_key(x_wos_admin_key)

    if payload.price <= 0:
        raise HTTPException(status_code=400, detail="Price must be greater than 0.")

    data = load_data()
    user = ensure_api_user_record(data, user_id)

    cards = user.setdefault("cards", [])
    chips = int(user.get("banana_chips", 0))

    if payload.card_id in cards:
        raise HTTPException(status_code=409, detail="User already owns this card.")

    if chips < payload.price:
        raise HTTPException(status_code=400, detail="Not enough Banana Chips.")

    user["banana_chips"] = chips - payload.price
    cards.append(payload.card_id)

    level_messages = api_add_xp(user, SHOP_BUY_XP_REWARD)

    save_data(data)

    return {
        "ok": True,
        "user_id": user_id,
        "card_id": payload.card_id,
        "price": payload.price,
        "xp_reward": SHOP_BUY_XP_REWARD,
        "banana_chips": int(user.get("banana_chips", 0)),
        "cards_owned": len(list(dict.fromkeys(cards))),
        "level": int(user.get("level", 1)),
        "xp": int(user.get("xp", 0)),
        "level_messages": level_messages
    }
    
