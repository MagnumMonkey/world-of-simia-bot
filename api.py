from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
from pathlib import Path

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
