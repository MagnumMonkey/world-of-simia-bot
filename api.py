from fastapi import FastAPI
import json
from pathlib import Path

app = FastAPI()

DATA_FILE = Path("/data/wos_data.json")

def load_data():
    if not DATA_FILE.exists():
        return {}
    with open(DATA_FILE, "r") as f:
        return json.load(f)

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
