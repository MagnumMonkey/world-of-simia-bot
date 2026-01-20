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
def get_collection(user_id: str):
    data = load_data()
    user = data.get(user_id, {"cards": []})

    cards = user.get("cards", [])

    return {
        "user_id": user_id,
        "count": len(cards),
        "cards": cards
    }
