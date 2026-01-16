import os
import json
import secrets
import random
from typing import Any, Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi import WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "wos_data.json")

app = FastAPI(title="World of Simia Server", version="0.1")
app.mount("/static", StaticFiles(directory="static"), name="static")

# Helpful later when you build a React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # later you can lock this down
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def load_data() -> Dict[str, Any]:
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}

def get_active_deck_cards(user_id: str):
    data = load_data()
    user = data.get(user_id)
    if not isinstance(user, dict):
        return []
    active = user.get("active_deck")
    decks = user.get("decks", {})
    if not active or active not in decks:
        return []
    cards = decks[active].get("cards", [])
    return list(cards) if isinstance(cards, list) else []

def save_data(data: dict) -> None:
    """Write wos_data.json safely (atomic write)."""
    tmp_path = DATA_FILE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, DATA_FILE)  # atomic on Windows


def ensure_user(data: dict, user_id: str) -> dict:
    """Return the user's record, creating it if missing."""
    if user_id not in data:
        data[user_id] = {
            "cards": [],
            "decks": {"default": {"cards": []}},
            "active_deck": "default",
        }
    # Safety: ensure required keys exist even for older saves
    user = data[user_id]
    user.setdefault("cards", [])
    user.setdefault("decks", {"default": {"cards": []}})
    user.setdefault("active_deck", "default")
    return user



@app.get("/health")
def health():
    return {"ok": True, "data_file": DATA_FILE}

@app.get("/me/deck")
def get_my_deck(user_id: str):
    """
    Example:
      /me/deck?user_id=1295073412980674682
    """
    data = load_data()

    user = data.get(user_id)
    if not isinstance(user, dict):
        raise HTTPException(status_code=404, detail="User not found in wos_data.json")

    active = user.get("active_deck")
    decks = user.get("decks", {})

    if not active or not isinstance(decks, dict) or active not in decks:
        raise HTTPException(status_code=400, detail="User has no active deck")

    deck = decks[active]
    deck_name = deck.get("name", active)
    cards = deck.get("cards", [])

    if not isinstance(cards, list):
        raise HTTPException(status_code=500, detail="Deck cards are not a list")

    return {
        "user_id": user_id,
        "active_deck_id": active,
        "deck_name": deck_name,
        "cards": cards,
        "count": len(cards),
    }

@app.get("/")
def root():
    return {"ok": True, "try": ["/health", "/me/deck?user_id=YOUR_DISCORD_ID"]}





# in-memory rooms (MVP)
ROOMS = {}  # room_id -> {"players": [user_id, ...]}

@app.post("/room/create")
def room_create(user_id: str):
    room_id = secrets.token_urlsafe(4)  # short-ish id
    ROOMS[room_id] = {
    "players": [user_id],
    "table": {}
}
    return {"room_id": room_id, "players": ROOMS[room_id]["players"]}

@app.post("/room/join")
def room_join(room_id: str, user_id: str):
    if room_id not in ROOMS:
        raise HTTPException(status_code=404, detail="Room not found")
    if user_id not in ROOMS[room_id]["players"]:
        ROOMS[room_id]["players"].append(user_id)
    return {"room_id": room_id, "players": ROOMS[room_id]["players"]}

@app.get("/room/state")
def room_state(room_id: str):
    if room_id not in ROOMS:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"room_id": room_id, **ROOMS[room_id]}


@app.post("/me/deck/add")
def deck_add(user_id: str, card_id: str):
    data = load_data()
    user = ensure_user(data, user_id)

    if card_id not in user.get("cards", []):
        raise HTTPException(status_code=400, detail="User does not own this card")

    active = user.get("active_deck", "default")
    decks = user.setdefault("decks", {})
    deck = decks.setdefault(active, {"cards": []})
    deck_cards = deck["cards"]

    if card_id in deck_cards:
        return {"ok": True, "message": "Already in deck", "deck": deck_cards}

    deck_cards.append(card_id)
    save_data(data)
    return {"ok": True, "message": "Added", "deck": deck_cards}



@app.post("/me/deck/remove")
def deck_remove(user_id: str, card_id: str):
    data = load_data()
    user = ensure_user(data, user_id)

    active = user.get("active_deck", "default")
    decks = user.setdefault("decks", {})
    deck = decks.setdefault(active, {"cards": []})
    deck_cards = deck["cards"]

    if card_id not in deck_cards:
        return {"ok": True, "message": "Not in deck", "deck": deck_cards}

    if card_id not in user.get("cards", []):
        return {"ok": True, "message": "Card not owned", "deck": deck_cards}

    deck_cards.remove(card_id)
    save_data(data)
    return {"ok": True, "message": "Removed", "deck": deck_cards}




ROOM_SOCKETS = {}  # room_id -> set[WebSocket]

async def broadcast_room(room_id: str):
    """Send the latest room state to all connected sockets in the room."""
    if room_id not in ROOMS:
        return
    payload = {"type": "room_state", "room_id": room_id, **ROOMS[room_id]}
    dead = []
    for ws in ROOM_SOCKETS.get(room_id, set()):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        ROOM_SOCKETS.get(room_id, set()).discard(ws)

@app.websocket("/ws/room/{room_id}")
async def ws_room(websocket: WebSocket, room_id: str):
    await websocket.accept()

    ROOM_SOCKETS.setdefault(room_id, set()).add(websocket)

    # Ensure room exists + has table
    if room_id not in ROOMS:
        ROOMS[room_id] = {"players": [], "table": {}, "card_stats": {}, "card_statuses": {}}
    else:
        ROOMS[room_id].setdefault("players", [])
        ROOMS[room_id].setdefault("table", {})
        ROOMS[room_id].setdefault("card_stats", {})
        ROOMS[room_id].setdefault("card_statuses", {})

    # Send initial state
    await websocket.send_json({"type": "room_state", "room_id": room_id, **ROOMS[room_id]})

    try:
        while True:
            msg = await websocket.receive_json()
            msg_type = msg.get("type")

            # ---- JOIN ----
            if msg_type == "join":
                user_id = str(msg.get("user_id", "")).strip()
                if not user_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id"})
                    continue

                if user_id not in ROOMS[room_id]["players"]:
                    ROOMS[room_id]["players"].append(user_id)

                # Ensure player table state exists
                table = ROOMS[room_id]["table"]
                table.setdefault(user_id, {"deck": [], "hand": [], "field": [], "discard": []})

                await broadcast_room(room_id)

            # ---- SHUFFLE (load active deck -> shuffled deck, clear hand/field) ----
            elif msg_type == "shuffle":
                user_id = str(msg.get("user_id", "")).strip()
                if not user_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id"})
                    continue

                # Make sure room/table player exists
                if user_id not in ROOMS[room_id]["players"]:
                    ROOMS[room_id]["players"].append(user_id)

                table = ROOMS[room_id]["table"]
                table.setdefault(user_id, {"deck": [], "hand": [], "field": [], "discard": []})

                cards = get_active_deck_cards(user_id)
                random.shuffle(cards)

                table[user_id]["deck"] = cards
                table[user_id]["hand"] = []
                table[user_id]["field"] = []
                table[user_id]["discard"] = []


                await broadcast_room(room_id)

            # ---- DRAW (top of deck -> hand) ----
            elif msg_type == "draw":
                user_id = str(msg.get("user_id", "")).strip()
                if not user_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)

                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                if not pile["deck"]:
                    await websocket.send_json({"type": "error", "detail": "Deck empty. Send shuffle first."})
                    continue

                card = pile["deck"].pop(0)
                pile["hand"].append(card)

                await broadcast_room(room_id)

                           # ---- PLAY (move a card from hand -> field) ----
            elif msg_type == "play":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id or card_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                hand = pile.get("hand", [])
                field = pile.get("field", [])

                if card_id not in hand:
                    await websocket.send_json({"type": "error", "detail": "Card not in hand"})
                    continue

                # remove ONE copy from hand
                hand.remove(card_id)
                field.append(card_id)

                await broadcast_room(room_id)

            elif msg_type == "unplay":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id or card_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                field = pile.get("field", [])
                hand = pile.get("hand", [])

                if card_id not in field:
                    await websocket.send_json({"type": "error", "detail": "Card not in field"})
                    continue

                field.remove(card_id)     # remove ONE copy
                hand.append(card_id)      # back to hand

                await broadcast_room(room_id)

            # ---- DISCARD (move a card to discard from a zone) ----
            elif msg_type == "discard":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()

                # Accept both snake_case and camelCase from the client
                from_zone = msg.get("from_zone", None)
                if from_zone is None:
                    from_zone = msg.get("fromZone", None)
                from_zone = str(from_zone or "hand").strip().lower()

                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id/card_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                # Ensure lists exist
                pile.setdefault("deck", [])
                pile.setdefault("hand", [])
                pile.setdefault("field", [])
                pile.setdefault("discard", [])

                valid_zones = {"deck", "hand", "field"}

                # If zone is invalid, try to find the card automatically
                removed = False

                if from_zone in valid_zones:
                    zone_list = pile[from_zone]
                    if card_id in zone_list:
                        zone_list.remove(card_id)
                        removed = True
                else:
                    # fallback search
                    for z in ("hand", "field", "deck"):
                        if card_id in pile[z]:
                            pile[z].remove(card_id)
                            removed = True
                            break

                if not removed:
                    await websocket.send_json({
                        "type": "error",
                        "detail": f"Card not found in {from_zone}. (card_id={card_id})"
                    })
                    continue

                pile["discard"].append(card_id)
                await broadcast_room(room_id)


                #===============================================================
                # SEND FROM DISCARD TO HAND
                #===============================================================

            elif msg_type == "undiscard":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id or card_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                pile.setdefault("discard", [])
                if card_id not in pile["discard"]:
                    await websocket.send_json({"type": "error", "detail": "Card not in discard"})
                    continue

                pile["discard"].remove(card_id)
                pile.setdefault("hand", []).append(card_id)

                await broadcast_room(room_id)


                #===========================================================
                # SEND FROM DISCARD TO DECK
                #===========================================================

            elif msg_type == "redeck":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id or card_id"})
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                pile.setdefault("discard", [])
                if card_id not in pile["discard"]:
                    await websocket.send_json({"type": "error", "detail": "Card not in discard"})
                    continue

                pile["discard"].remove(card_id)
                pile.setdefault("deck", []).append(card_id)  # bottom of deck

                await broadcast_room(room_id)

            # ---- MOVE (drag/drop between zones) ----
            elif msg_type == "move":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                from_zone = str(msg.get("from_zone", "")).strip()
                to_zone = str(msg.get("to_zone", "")).strip()

                if not user_id or not card_id or not from_zone or not to_zone:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id/card_id/from_zone/to_zone"})
                    continue

                if from_zone == to_zone:
                    # nothing to do
                    await broadcast_room(room_id)
                    continue

                table = ROOMS[room_id].setdefault("table", {})
                pile = table.get(user_id)
                if not pile:
                    await websocket.send_json({"type": "error", "detail": "No table state for this user. Send join first."})
                    continue

                allowed = {"hand", "field", "discard", "deck"}
                if from_zone not in allowed or to_zone not in allowed:
                    await websocket.send_json({"type": "error", "detail": f"Invalid zone. from={from_zone} to={to_zone}"})
                    continue

                # Make sure zones exist
                for z in allowed:
                    pile.setdefault(z, [])

                # Remove ONE copy from from_zone
                if card_id not in pile[from_zone]:
                    await websocket.send_json({"type": "error", "detail": f"Card not found in {from_zone}. (card_id={card_id})"})
                    continue

                pile[from_zone].remove(card_id)

                # Add to destination
                if to_zone == "deck":
                    # choose a rule: top or bottom. We'll put on TOP so you can draw it next.
                    pile["deck"].insert(0, card_id)
                else:
                    pile[to_zone].append(card_id)

                await broadcast_room(room_id)

                            # ---- SET CARD STATS (shared / authoritative) ----
            elif msg_type == "set_card_stats":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                stats = msg.get("stats")

                # If stats is null, that means "reset to base" (remove override)
                if stats is None:
                    ROOMS[room_id].setdefault("card_stats", {})
                    ROOMS[room_id]["card_stats"].pop(card_id, None)
                    await broadcast_room(room_id)
                    continue


                if not user_id or not card_id or not isinstance(stats, dict):
                    await websocket.send_json({"type": "error", "detail": "Missing user_id/card_id/stats"})
                    continue

                ROOMS[room_id].setdefault("card_stats", {})

                try:
                    banana = int(stats.get("banana_size", 0))
                    charm = int(stats.get("charm", 0))
                    mischief = int(stats.get("mischief", 0))
                    total = int(stats.get("total", banana + charm + mischief))
                except Exception:
                    await websocket.send_json({"type": "error", "detail": "Stats must be numbers"})
                    continue

                ROOMS[room_id]["card_stats"][card_id] = {
                    "banana_size": banana,
                    "charm": charm,
                    "mischief": mischief,
                    "total": total,
                }
                await broadcast_room(room_id)


                        # ---- SET CARD STATUSES (shared / authoritative) ----
            elif msg_type == "set_card_statuses":
                user_id = str(msg.get("user_id", "")).strip()
                card_id = str(msg.get("card_id", "")).strip()
                statuses = msg.get("statuses")  # expected list[str] OR None

                if not user_id or not card_id:
                    await websocket.send_json({"type": "error", "detail": "Missing user_id or card_id"})
                    continue

                ROOMS[room_id].setdefault("card_statuses", {})

                # If statuses is null, treat as "reset to base" (remove override)
                if statuses is None:
                    ROOMS[room_id]["card_statuses"].pop(card_id, None)
                    await broadcast_room(room_id)
                    continue

                if not isinstance(statuses, list):
                    await websocket.send_json({"type": "error", "detail": "statuses must be a list or null"})
                    continue

                ALLOWED = {"briefs", "nude", "ejaculating", "heat", "erect"}

                cleaned = []
                for s in statuses:
                    s2 = str(s).strip().lower()
                    if s2 in ALLOWED and s2 not in cleaned:
                        cleaned.append(s2)

                ROOMS[room_id]["card_statuses"][card_id] = cleaned
                await broadcast_room(room_id)



            else:
                await websocket.send_json({"type": "error", "detail": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        pass
    finally:
        ROOM_SOCKETS.get(room_id, set()).discard(websocket)
