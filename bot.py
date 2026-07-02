import os
import json
import random
import time
import datetime
import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import timezone
import httpx
from pathlib import Path
import aiohttp

# Persistent storage directory (Railway volume mount)
WOS_DATA_DIR = Path(os.getenv("WOS_DATA_DIR", "/data"))
WOS_DATA_DIR.mkdir(parents=True, exist_ok=True)

WOS_DATA_PATH = WOS_DATA_DIR / "wos_data.json"

# ==========================
# CONFIG
# ==========================
# TODO: Put your real token here FOR NOW.
# Later, we will move it to an environment variable or .env file.
BOT_TOKEN = os.getenv("DISCORD_TOKEN")

# File to store player collections for now
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARDS_FILE = os.path.join(BASE_DIR, "Cards.json")
STARTER_CARD_POOL = ["rhythm_runner_rhed", "beat_boy_bloo", "waveform_walker_whytte"]
GUILD_ID = 1295075929806344234  # <- replace with your real server ID
OWNER_ID = 1295073412980674682  # replace with YOUR Discord ID
CATALOG_FORUM_CHANNEL_ID = 1454778038737834088
DEV_USER_IDS = {1295073412980674682}  # <-- replace/add your ID(s)


# ==========================
# DISCOVERY CONFIG
# ==========================

RARITY_WEIGHTS = {
    "common": 64,
    "uncommon": 30.75,
    "rare": 5.,
    "legendary": 0.25
}

SHOP_SIZE = 5  # always 5 cards in shop

PRICE_RANGES = {
    "common": (10, 20),
    "uncommon": (30, 40),
    "rare": (100, 150),
    "legendary": (300, 500)
}

DUP_SELL_VALUES = {
    "common": 5,
    "uncommon": 10,
    "rare": 30,
    "legendary": 100
}

IMAGE_BASE_URL = os.getenv(
    "IMAGE_BASE_URL",
    "https://magnummonkey.github.io/world-of-simia-bot"
).rstrip("/")

def resolve_card_image_url(card: dict) -> str | None:
    """
    Prefer image_url_web (stable). If it's relative (/static/...),
    prepend IMAGE_BASE_URL. Fall back to discord/url fields for legacy.
    """
    url = (
        card.get("image_url_web")
        or card.get("image_url_discord")
        or card.get("image_url")
        or ""
    )

    if not url:
        return None

    # ✅ strip whitespace ONCE, early
    url = url.strip()

    # If it's a relative path, it needs a public base URL
    if url.startswith("/"):
        if not IMAGE_BASE_URL:
            print(f"[WARN] IMAGE_BASE_URL not set; cannot resolve {url}")
            return None
        return f"{IMAGE_BASE_URL}{url}"

    return url



TRADE_COOLDOWN_SECONDS = 2 * 60 * 60  # 2 hours
TRADE_XP_REWARD = 10
TRADE_CHIPS_REWARD = 5

CATALOG_SUBMIT_XP_REWARD = 10
CATALOG_SUBMIT_CHIPS_REWARD = 10

DISCOVER_XP_REWARD = 10
DISCOVER_CHIPS_REWARD = 5

# ==========================
# LEVELING CONFIG
# ==========================
LEVEL_REWARD_CHIPS = 25
XP_CAP_PER_LEVEL = 100

def xp_needed_for_level(level: int) -> int:
    """
    XP required to go from level -> level+1.
    Grows slowly and caps at 100.
    """
    return min(20 + (level - 1) * 5, XP_CAP_PER_LEVEL)

def ensure_leveling_fields(user: dict):
    if "level" not in user:
        user["level"] = 1
    if "xp" not in user:
        user["xp"] = 0

def add_xp(user: dict, amount: int) -> list[str]:
    """
    Adds XP, handles level-ups, and awards Banana Chips.
    Returns a list of level-up messages (can be empty).
    """
    ensure_leveling_fields(user)

    messages = []
    user["xp"] += amount

    while True:
        needed = xp_needed_for_level(user["level"])
        if user["xp"] < needed:
            break

        user["xp"] -= needed
        user["level"] += 1
        user["banana_chips"] = int(user.get("banana_chips", 0)) + LEVEL_REWARD_CHIPS

        messages.append(
            f"🐒 **Level Up!** You reached **Level {user['level']}** and earned **{LEVEL_REWARD_CHIPS} Banana Chips** 🍌"
        )

    return messages

def award_rewards(data: dict, user_id: str, xp: int = 0, chips: int = 0) -> list[str]:
    """
    Awards XP and Banana Chips to a user.
    Returns level-up messages.
    """
    user = ensure_user_record(data, user_id)

    level_msgs = []
    if xp > 0:
        level_msgs = add_xp(user, xp)

    if chips > 0:
        user["banana_chips"] = int(user.get("banana_chips", 0)) + chips

    return level_msgs


# ==========================
# DATA HELPER FUNCTIONS
# ==========================
def load_data() -> dict:
    if not WOS_DATA_PATH.exists():
        return {}

    try:
        with open(WOS_DATA_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
            if not raw:
                return {}
            return json.loads(raw)
    except json.JSONDecodeError:
        print("⚠️ wos_data.json is corrupted/invalid JSON. Using empty data instead.")
        return {}
    except Exception as e:
        print(f"⚠️ Failed to load data: {e}")
        return {}


def save_data(data):
    with open(WOS_DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)



def load_cards():
    path = os.path.abspath(CARDS_FILE)
    print("📦 Loading Cards.json from:", path)
    if not os.path.exists(CARDS_FILE):
        print("❌ Cards file not found:", path)
        return {}
    with open(CARDS_FILE, "r", encoding="utf-8") as f:
        db = json.load(f)
    has_any_starter = any(cid in db for cid in STARTER_CARD_POOL)
    print("✅ Loaded cards:", len(db), "| Has starter(s)?", has_any_starter)
    return db


def today_ymd() -> str:
    # Uses server time; if you want New York–aligned later, we can adjust.
    return datetime.date.today().isoformat()


def normalize_rarity(value: str) -> str:
    if not value:
        return "common"
    return str(value).strip().lower()


def build_rarity_index(cards_db: dict) -> dict:
    """Map rarity -> list of card_ids."""
    idx = {"common": [], "uncommon": [], "rare": [], "legendary": []}
    for cid, card in cards_db.items():
        r = normalize_rarity(card.get("rarity", "common"))
        if r not in idx:
            # Unknown rarity? Treat as common so it still appears.
            r = "common"
        idx[r].append(cid)
    return idx


def weighted_random_rarity() -> str:
    rarities = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    return random.choices(rarities, weights=weights, k=1)[0]


def roll_card_id(cards_db: dict, rarity_index: dict) -> str:
    """Pick a rarity by weight, then pick a random card from that rarity."""
    rarity = weighted_random_rarity()
    pool = rarity_index.get(rarity, [])
    if pool:
        return random.choice(pool)

    # Fallback: if that rarity has no cards, pick from any cards
    all_ids = list(cards_db.keys())
    return random.choice(all_ids) if all_ids else None


def normalize_rarity(value: str) -> str:
    if not value:
        return "common"
    r = str(value).strip().lower()
    if r not in ("common", "uncommon", "rare", "legendary"):
        return "common"
    return r

def build_rarity_index(cards_db: dict) -> dict:
    idx = {"common": [], "uncommon": [], "rare": [], "legendary": []}
    for cid, card in cards_db.items():
        r = normalize_rarity(card.get("rarity", "common"))
        idx[r].append(cid)
    return idx

def weighted_random_rarity() -> str:
    rarities = list(RARITY_WEIGHTS.keys())
    weights = list(RARITY_WEIGHTS.values())
    return random.choices(rarities, weights=weights, k=1)[0]

def roll_card_id_by_rarity(cards_db: dict, rarity_index: dict) -> str | None:
    """Pick a rarity by weight, then pick a random card from that rarity.
       If that rarity has no cards, fallback to any card."""
    if not cards_db:
        return None

    r = weighted_random_rarity()
    pool = rarity_index.get(r, [])

    if pool:
        return random.choice(pool)

    all_ids = list(cards_db.keys())
    return random.choice(all_ids) if all_ids else None

def price_for_rarity(rarity: str) -> int:
    lo, hi = PRICE_RANGES.get(rarity, (10, 20))
    return random.randint(lo, hi)

def ensure_user_record(data: dict, user_id: str) -> dict:
    """Guarantee user exists and has required fields (Behavior B: deck does NOT auto-fill from collection)."""
    if user_id not in data or not isinstance(data[user_id], dict):
        data[user_id] = {}

    u = data[user_id]

    # collection + currency
    u.setdefault("cards", [])
    u.setdefault("banana_chips", 0)

    # leveling
    u.setdefault("level", 1)
    u.setdefault("xp", 0)

    # trading
    u.setdefault("last_trade_at", None)

    # decks
    if "decks" not in u or not isinstance(u["decks"], dict):
        u["decks"] = {}

    # Always ensure a default deck exists (can be empty!)
    u["decks"].setdefault("default", {"name": "Default Deck", "cards": []})

    # Always ensure an active deck is set
    if not u.get("active_deck") or u["active_deck"] not in u["decks"]:
        u["active_deck"] = "default"

    return u

def get_profile_record(data: dict, user_id: str) -> dict:
    """
    Returns the user's local profile record.

    Also tries to migrate old/accidental nested data from:
    data["users"][user_id]
    into:
    data[user_id]
    """
    user = ensure_user_record(data, user_id)

    legacy_users = data.get("users", {})
    legacy = None

    if isinstance(legacy_users, dict):
        possible = legacy_users.get(user_id)
        if isinstance(possible, dict):
            legacy = possible

    if legacy:
        # Keep the higher/better values if old data exists somewhere else
        user["level"] = max(int(user.get("level", 1)), int(legacy.get("level", 1)))
        user["xp"] = max(int(user.get("xp", 0)), int(legacy.get("xp", 0)))
        user["banana_chips"] = max(
            int(user.get("banana_chips", 0)),
            int(legacy.get("banana_chips", 0))
        )

    return user


def generate_daily_shop_offers(cards_db: dict) -> list[dict]:
    """Generate SHOP_SIZE unique offers, weighted by rarity."""
    rarity_index = build_rarity_index(cards_db)
    offers = []
    used_ids = set()

    # Keep rolling until we have SHOP_SIZE unique card_ids (or we exhaust attempts)
    attempts = 0
    max_attempts = 500

    while len(offers) < SHOP_SIZE and attempts < max_attempts:
        attempts += 1
        cid = roll_card_id_by_rarity(cards_db, rarity_index)
        if cid is None:
            break
        if cid in used_ids:
            continue

        card = cards_db.get(cid, {})
        r = normalize_rarity(card.get("rarity", "common"))
        offers.append({
            "card_id": cid,
            "rarity": r,
            "price": price_for_rarity(r),
            "sold": False
        })
        used_ids.add(cid)

    # If card pool is tiny and we couldn't fill, just return what we have
    return offers

def build_shop_embed(user: dict, cards_db: dict, offers: list[dict]) -> discord.Embed:
    chips = int(user.get("banana_chips", 0))
    owned = set(user.get("cards", []))

    embed = discord.Embed(
        title="🍌 Banana Chip Shop",
        description=f"**Banana Chips:** `{chips}`\nChoose a card to buy (5 offers daily)."
    )

    for i, offer in enumerate(offers, start=1):
        cid = offer.get("card_id")
        price = int(offer.get("price", 0))
        rarity = normalize_rarity(offer.get("rarity", "common"))
        sold = bool(offer.get("sold", False))

        c = cards_db.get(cid, {})
        name = c.get("name", cid)
        rarity_label = rarity.title()

        flags = []
        if sold:
            flags.append("SOLD")
        if cid in owned:
            flags.append("OWNED")
        if not sold and cid not in owned and chips < price:
            flags.append("CAN'T AFFORD")

        flag_text = f" — {' | '.join(flags)}" if flags else ""

        embed.add_field(
            name=f"{i}. {name}",
            value=f"Rarity: **{rarity_label}**\nPrice: **{price}** chips{flag_text}",
            inline=False
        )

    return embed

def ensure_community_catalog(data: dict) -> dict:
    """Top-level registry: data['community_catalog'][card_id] = {...}"""
    if "community_catalog" not in data or not isinstance(data["community_catalog"], dict):
        data["community_catalog"] = {}
    return data["community_catalog"]

def xp_required_for_level(level: int) -> int:
    # Smooth curve that caps at 100
    return min(100, 15 + level * 5)

def ensure_pending_trades(data: dict) -> dict:
    if "pending_trades" not in data or not isinstance(data["pending_trades"], dict):
        data["pending_trades"] = {}
    return data["pending_trades"]

def can_trade(user: dict) -> bool:
    last = user.get("last_trade_at")
    if not last:
        return True

    try:
        last_dt = datetime.datetime.fromisoformat(last)
    except Exception:
        return True

    now = datetime.datetime.now(timezone.utc)
    return (now - last_dt).total_seconds() >= TRADE_COOLDOWN_SECONDS

def mark_trade(user: dict):
    user["last_trade_at"] = datetime.datetime.now(timezone.utc).isoformat()

def recorded_catalog_submission_count(data: dict, user_id: str) -> int:
    catalog = data.get("community_catalog", {})
    if not isinstance(catalog, dict):
        return 0

    return sum(
        1 for v in catalog.values()
        if isinstance(v, dict) and str(v.get("submitted_by")) == user_id
    )


def displayed_catalog_submission_count(data: dict, user_id: str) -> int:
    user = ensure_user_record(data, user_id)
    recorded = recorded_catalog_submission_count(data, user_id)
    adjustment = int(user.get("catalog_submission_adjustment", 0))
    return max(0, recorded + adjustment)


async def grant_card(user_id: str, card_id: str, data: dict):
    ensure_user_record(data, user_id)
    data[user_id]["cards"].append(card_id)
    save_data(data)
    await add_card_via_api(user_id, card_id)


async def get_collection_count_from_api(user_id: str) -> int:
    url = f"{API_BASE}/api/collection/{user_id}?page=1&limit=18"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return 0

            payload = await resp.json()

            # New API shape has "total"
            if "total" in payload:
                try:
                    return int(payload.get("total", 0))
                except Exception:
                    return 0

            # Fallback for older API shape
            cards = payload.get("cards", []) or []
            normalized = []

            for c in cards:
                if isinstance(c, str):
                    normalized.append(c)
                elif isinstance(c, dict) and c.get("card_id"):
                    normalized.append(c["card_id"])

            return len(set(normalized))


async def get_collection_from_api(user_id: str) -> list[str]:
    url = f"{API_BASE}/api/collection/{user_id}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return []

            payload = await resp.json()
            cards = payload.get("cards", []) or []

            result = []
            for c in cards:
                if isinstance(c, str):
                    result.append(c)
                elif isinstance(c, dict) and c.get("card_id"):
                    result.append(c["card_id"])

            return result

async def get_profile_from_api(user_id: str) -> dict | None:
    url = f"{API_BASE}/api/profile/{user_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None

            return await resp.json()


WOS_ADMIN_KEY = os.getenv("WOS_ADMIN_KEY", "")

def api_admin_headers() -> dict:
    return {"X-WOS-ADMIN-KEY": WOS_ADMIN_KEY}


# ================================
# CLASS
# ================================
class DiscoverView(discord.ui.View):
    def __init__(self, owner_id: str, options: list[str]):
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.options = options  # list of 3 card_ids

        # Create one button per option
        for i, cid in enumerate(options):
            self.add_item(DiscoverButton(index=i, card_id=cid))


class DiscoverButton(discord.ui.Button):
    def __init__(self, index: int, card_id: str):
        super().__init__(style=discord.ButtonStyle.primary, label=f"Choose #{index+1}")
        self.card_id = card_id

    async def callback(self, interaction: discord.Interaction):
        view: DiscoverView = self.view  # type: ignore
        if str(interaction.user.id) != view.owner_id:
            await interaction.response.send_message("❌ This discovery isn’t yours.", ephemeral=True)
            return

        data = load_data()
        cards_db = load_cards()
        user_id = str(interaction.user.id)
        today = today_ymd()

        # Ensure user record exists
        if user_id not in data:
            data[user_id] = {"cards": [], "banana_chips": 0}

        # Ensure banana chips exist (for older saves)
        if "banana_chips" not in data[user_id]:
            data[user_id]["banana_chips"] = 0

        # If they already claimed today, block
        if data[user_id].get("last_discover_date") == today:
            await interaction.response.send_message(
                "⏳ You already discovered a card today. Come back tomorrow!",
                ephemeral=True
            )
            return

        # Must have a pending discovery to claim (prevents reroll abuse)
        pending = data[user_id].get("pending_discover")
        if not pending or pending.get("date") != today or not pending.get("options"):
            await interaction.response.send_message(
                "⚠️ Your discovery expired. Run `/wos_discover` again.",
                ephemeral=True
            )
            return

        chosen_id = self.card_id

        # Validate choice is one of today's options
        if chosen_id not in pending["options"]:
            await interaction.response.send_message(
                "⚠️ That card isn’t one of today’s options.",
                ephemeral=True
            )
            return

        # Lock the day now
        data[user_id]["last_discover_date"] = today
        data[user_id].pop("pending_discover", None)

        owned = data[user_id].get("cards", [])
        is_new = chosen_id not in owned

        # Apply outcome
        if is_new:
            owned.append(chosen_id)
            data[user_id]["cards"] = owned
            await add_card_via_api(user_id, chosen_id)
            title_line = "🎉 New discovery!"
            desc_line = "Added to your collection."
        else:
            # Duplicate → sell for banana chips
            card = cards_db.get(chosen_id, {})
            rarity = normalize_rarity(card.get("rarity", "common"))
            payout = int(DUP_SELL_VALUES.get(rarity, 0))
            data[user_id]["banana_chips"] += payout

            title_line = "🐒 Duplicate discovered"
            desc_line = f"Sold for **{payout} Banana Chips**."

        level_msgs = award_rewards(
            data,
            user_id,
            xp=DISCOVER_XP_REWARD,
            chips=DISCOVER_CHIPS_REWARD
        )
        save_data(data)


        # Build result embed (your existing full card embed)
        card = cards_db.get(chosen_id, {})
        name = card.get("name", chosen_id)
        personality = card.get("personality", "Unknown")
        status = card.get("status", card.get("Status", "None"))
        banana = card.get("banana_size", "?")
        charm = card.get("charm", "?")
        mischief = card.get("mischief", "?")
        total = card.get("total", "?")
        image_url = resolve_card_image_url(card)

        # Level-up text
        level_text = "\n\n".join(level_msgs) if level_msgs else ""
        reward_text = (
            f"+{DISCOVER_XP_REWARD} XP\n"
            f"+{DISCOVER_CHIPS_REWARD} Banana Chips 🍌"
)

        # Now build the embed (AFTER name/personality/status exist)
        embed = discord.Embed(
            title=name,
            description=(
                f"{title_line}\n{desc_line}\n"
                f"{reward_text}"
                f"{'\n\n' + level_text if level_text else ''}\n\n"
                f"Personality: **{personality}**\n"
                f"Status: **{status}**"
            )
        )


        embed = discord.Embed(
            title=name,
            description=(
                f"{title_line}\n{desc_line}"
                f"{'\n\n' + level_text if level_text else ''}\n\n"
                f"Personality: **{personality}**\n"
                f"Status: **{status}**"
            )

        )
        embed.add_field(name="Banana Size", value=str(banana), inline=True)
        embed.add_field(name="Charm", value=str(charm), inline=True)
        embed.add_field(name="Mischief", value=str(mischief), inline=True)
        embed.add_field(name="Total", value=str(total), inline=True)

        image_url = resolve_card_image_url(card)

        if image_url:
            embed.set_image(url=image_url)


        # Disable buttons after selection
        for item in view.children:
            item.disabled = True

        await interaction.response.edit_message(
            content="✅ Choice locked for today.",
            embed=embed,
            view=view
        )


class ShopBuyButton(discord.ui.Button):
    def __init__(self, slot_index: int):
        super().__init__(style=discord.ButtonStyle.success, label=f"Buy #{slot_index+1}")
        self.slot_index = slot_index  # 0-based

    async def callback(self, interaction: discord.Interaction):
        view: ShopView = self.view  # type: ignore
        if str(interaction.user.id) != view.owner_id:
            await interaction.response.send_message("❌ This shop isn’t yours.", ephemeral=True)
            return

        user_id = view.owner_id
        today = today_ymd()

        data = load_data()
        cards_db = load_cards()
        user = ensure_user_record(data, user_id)

        # Validate shop is still today's and offers exist
        if user.get("shop_date") != today or not user.get("shop_offers"):
            await interaction.response.send_message("⚠️ Your shop expired. Run `/wos_shop` again.", ephemeral=True)
            return

        offers = user.get("shop_offers", [])
        if self.slot_index < 0 or self.slot_index >= len(offers):
            await interaction.response.send_message("⚠️ Invalid shop selection.", ephemeral=True)
            return

        offer = offers[self.slot_index]
        if offer.get("sold", False):
            await interaction.response.send_message("⚠️ That item is already SOLD.", ephemeral=True)
            return

        cid = offer.get("card_id")
        price = int(offer.get("price", 0))

        # Block purchasing owned cards (your rule)
        owned = set(user.get("cards", []))
        if cid in owned:
            await interaction.response.send_message("⚠️ You already own this card (cannot buy duplicates).", ephemeral=True)
            return

        chips = int(user.get("banana_chips", 0))
        if chips < price:
            await interaction.response.send_message("⚠️ Not enough Banana Chips.", ephemeral=True)
            return

        # Purchase success
        user["banana_chips"] = chips - price
        user["cards"].append(cid)
        offer["sold"] = True

        await add_card_via_api(user_id, cid)

        # XP for shop purchase
        level_msgs = add_xp(user, 5)


        save_data(data)

        # Rebuild UI (disable buttons appropriately)
        new_embed = build_shop_embed(user, cards_db, offers)
        if level_msgs:
            new_embed.description += "\n\n" + "\n".join(level_msgs)
        
        view.refresh_buttons(user)

        await interaction.response.edit_message(
            embed=new_embed, 
            view=view
        )


class ShopView(discord.ui.View):
    def __init__(self, owner_id: str, user: dict):
        super().__init__(timeout=180)
        self.owner_id = owner_id

        # Add 5 buttons
        for i in range(SHOP_SIZE):
            self.add_item(ShopBuyButton(i))

        self.refresh_buttons(user)

    def refresh_buttons(self, user: dict):
        """Enable/disable buttons based on current user state + offer state."""
        chips = int(user.get("banana_chips", 0))
        owned = set(user.get("cards", []))
        offers = user.get("shop_offers", [])

        for item in self.children:
            if not isinstance(item, ShopBuyButton):
                continue

            idx = item.slot_index
            if idx >= len(offers):
                item.disabled = True
                item.label = f"Buy #{idx+1}"
                continue

            offer = offers[idx]
            cid = offer.get("card_id")
            price = int(offer.get("price", 0))
            sold = bool(offer.get("sold", False))

            if sold:
                item.disabled = True
                item.label = f"SOLD #{idx+1}"
            elif cid in owned:
                item.disabled = True
                item.label = f"OWNED #{idx+1}"
            elif chips < price:
                item.disabled = True
                item.label = f"{price} chips"
            else:
                item.disabled = False
                item.label = f"Buy {price}"

# ==========================
# TRADING UI (Step 2)
# ==========================

def build_trade_embed(trade: dict, cards_db: dict, status_line: str = "") -> discord.Embed:
    """Build a nice embed for the trade message."""
    from_id = str(trade["from"])
    to_id = str(trade["to"])
    give_id = trade["give"]
    want_id = trade["want"]

    give_name = cards_db.get(give_id, {}).get("name", give_id)
    want_name = cards_db.get(want_id, {}).get("name", want_id)

    desc = (
        f"**From:** <@{from_id}>\n"
        f"**To:** <@{to_id}>\n\n"
        f"**Offer:** **{give_name}** (`{give_id}`)\n"
        f"**For:** **{want_name}** (`{want_id}`)\n"
    )
    if status_line:
        desc += f"\n{status_line}"

    embed = discord.Embed(title="🔁 World of Simia Trade", description=desc)
    return embed


class TradeView(discord.ui.View):
    """
    Phase 1: target user Accept/Decline
    Phase 2: BOTH users Confirm (two separate buttons)
    """
    def __init__(self, trade_id: str, from_id: str, to_id: str, give_id: str, want_id: str):
        super().__init__(timeout=15 * 60)  # 15 min timeout
        self.trade_id = trade_id
        self.from_id = str(from_id)
        self.to_id = str(to_id)
        self.give_id = give_id
        self.want_id = want_id

        # Phase 1 buttons
        self.add_item(TradeAcceptButton())
        self.add_item(TradeDeclineButton())

        # Phase 2 buttons (disabled until accepted)
        self.confirm_from = TradeConfirmButton(who="from")
        self.confirm_to = TradeConfirmButton(who="to")
        self.confirm_from.disabled = True
        self.confirm_to.disabled = True
        self.add_item(self.confirm_from)
        self.add_item(self.confirm_to)

    async def on_timeout(self):
        # Mark trade as expired (remove from pending_trades)
        data = load_data()
        pending = ensure_pending_trades(data)
        if self.trade_id in pending:
            del pending[self.trade_id]
            save_data(data)

        # Disable buttons (best effort)
        for item in self.children:
            item.disabled = True

    def _is_from(self, user_id: str) -> bool:
        return str(user_id) == self.from_id

    def _is_to(self, user_id: str) -> bool:
        return str(user_id) == self.to_id

    def _status_line(self, trade: dict) -> str:
        phase = trade.get("phase", "offer")  # "offer" or "confirm"
        if phase == "offer":
            return "🟦 Waiting for the target player to **Accept** or **Decline**."
        else:
            conf = trade.get("confirmed", {})
            a = "✅" if conf.get("from") else "⬜"
            b = "✅" if conf.get("to") else "⬜"
            return f"🟨 Confirmations: <@{self.from_id}> {a}  |  <@{self.to_id}> {b}"

    async def refresh_message(self, interaction: discord.Interaction, trade: dict):
        cards_db = load_cards()
        embed = build_trade_embed(trade, cards_db, status_line=self._status_line(trade))
        await interaction.response.edit_message(embed=embed, view=self)

    async def finalize_trade(self, interaction: discord.Interaction):
        """
        Called when BOTH confirm. Re-check ownership, do swap, give XP, apply cooldown.
        """
        data = load_data()
        pending = ensure_pending_trades(data)

        trade = pending.get(self.trade_id)
        if not trade:
            # trade gone / expired
            for item in self.children:
                item.disabled = True
            await interaction.response.edit_message(content="❌ Trade expired.", view=self)
            return

        from_id = str(trade["from"])
        to_id = str(trade["to"])
        give_id = trade["give"]
        want_id = trade["want"]

        # Ensure users exist
        u_from = ensure_user_record(data, from_id)
        u_to = ensure_user_record(data, to_id)

        # Cooldown check at finalize (prevents abuse)
        if not can_trade(u_from):
            await interaction.response.send_message("❌ The trade proposer is on trade cooldown.", ephemeral=True)
            return
        if not can_trade(u_to):
            await interaction.response.send_message("❌ The other player is on trade cooldown.", ephemeral=True)
            return

        # Verify both still own the cards right now
        if give_id not in u_from.get("cards", []):
            for item in self.children:
                item.disabled = True
            del pending[self.trade_id]
            save_data(data)
            await interaction.response.edit_message(content="❌ Trade failed: proposer no longer owns the offered card.", view=self)
            return

        if want_id not in u_to.get("cards", []):
            for item in self.children:
                item.disabled = True
            del pending[self.trade_id]
            save_data(data)
            await interaction.response.edit_message(content="❌ Trade failed: target no longer owns the requested card.", view=self)
            return

        # Swap (1-to-1)
        u_from["cards"].remove(give_id)
        u_to["cards"].remove(want_id)

        u_from["cards"].append(want_id)
        u_to["cards"].append(give_id)

        await add_card_via_api(from_id, want_id)
        await add_card_via_api(to_id, give_id) 

        # XP reward (both players)
        # Rewards for successful trade
        msgs_from = award_rewards(
            data,
            from_id,
            xp=TRADE_XP_REWARD,
            chips=TRADE_CHIPS_REWARD
        )

        msgs_to = award_rewards(
            data,
            to_id,
            xp=TRADE_XP_REWARD,
            chips=TRADE_CHIPS_REWARD
        )

        # Apply cooldown timestamps
        mark_trade(u_from)
        mark_trade(u_to)

        # Remove pending trade
        del pending[self.trade_id]
        save_data(data)

        # Disable buttons
        for item in self.children:
            item.disabled = True

        cards_db = load_cards()
        give_name = cards_db.get(give_id, {}).get("name", give_id)
        want_name = cards_db.get(want_id, {}).get("name", want_id)

        done_text = (
            f"✅ Trade completed!\n"
            f"<@{from_id}> traded **{give_name}** for **{want_name}** with <@{to_id}>.\n"
            f"+{TRADE_XP_REWARD} XP and +{TRADE_CHIPS_REWARD} Banana Chips 🍌 to both players."
        )

        # Include level-up messages (if any)
        extra = []
        if msgs_from:
            extra.append(f"\n**<@{from_id}>**:\n" + "\n".join(msgs_from))
        if msgs_to:
            extra.append(f"\n**<@{to_id}>**:\n" + "\n".join(msgs_to))
        if extra:
            done_text += "\n\n" + "\n\n".join(extra)

        await interaction.response.edit_message(content=done_text, view=self, embed=None)


class TradeAcceptButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.success, label="Accept")

    async def callback(self, interaction: discord.Interaction):
        view: TradeView = self.view  # type: ignore

        # Only the target can accept
        if str(interaction.user.id) != view.to_id:
            await interaction.response.send_message("❌ Only the invited player can accept.", ephemeral=True)
            return

        data = load_data()
        pending = ensure_pending_trades(data)
        trade = pending.get(view.trade_id)

        if not trade:
            await interaction.response.send_message("❌ This trade expired.", ephemeral=True)
            return

        # Move to confirm phase
        trade["phase"] = "confirm"
        trade["confirmed"] = {"from": False, "to": False}
        save_data(data)

        # Disable accept/decline, enable confirm buttons
        for item in view.children:
            if isinstance(item, (TradeAcceptButton, TradeDeclineButton)):
                item.disabled = True
        view.confirm_from.disabled = False
        view.confirm_to.disabled = False

        await view.refresh_message(interaction, trade)


class TradeDeclineButton(discord.ui.Button):
    def __init__(self):
        super().__init__(style=discord.ButtonStyle.danger, label="Decline")

    async def callback(self, interaction: discord.Interaction):
        view: TradeView = self.view  # type: ignore

        # Only the target can decline
        if str(interaction.user.id) != view.to_id:
            await interaction.response.send_message("❌ Only the invited player can decline.", ephemeral=True)
            return

        data = load_data()
        pending = ensure_pending_trades(data)
        trade = pending.get(view.trade_id)

        if trade and view.trade_id in pending:
            del pending[view.trade_id]
            save_data(data)

        for item in view.children:
            item.disabled = True

        await interaction.response.edit_message(content="❌ Trade declined.", view=view, embed=None)


class TradeConfirmButton(discord.ui.Button):
    def __init__(self, who: str):
        self.who = who  # "from" or "to"
        label = "Confirm (Proposer)" if who == "from" else "Confirm (Target)"
        super().__init__(style=discord.ButtonStyle.primary, label=label)

    async def callback(self, interaction: discord.Interaction):
        view: TradeView = self.view  # type: ignore
        user_id = str(interaction.user.id)

        # Only the correct person can click their confirm button
        if self.who == "from" and user_id != view.from_id:
            await interaction.response.send_message("❌ Only the proposer can click this.", ephemeral=True)
            return
        if self.who == "to" and user_id != view.to_id:
            await interaction.response.send_message("❌ Only the invited player can click this.", ephemeral=True)
            return

        data = load_data()
        pending = ensure_pending_trades(data)
        trade = pending.get(view.trade_id)

        if not trade:
            await interaction.response.send_message("❌ This trade expired.", ephemeral=True)
            return

        # Must be in confirm phase
        if trade.get("phase") != "confirm":
            await interaction.response.send_message("❌ This trade is not ready to confirm yet.", ephemeral=True)
            return

        conf = trade.get("confirmed") or {}
        conf[self.who] = True
        trade["confirmed"] = conf
        save_data(data)

        # If both confirmed -> finalize
        if conf.get("from") and conf.get("to"):
            await view.finalize_trade(interaction)
            return

        # Otherwise update embed status
        await view.refresh_message(interaction, trade)

API_BASE = "https://wos-api-production.up.railway.app".rstrip("/")


async def add_card_via_api(user_id: str, card_id: str) -> None:
    url = f"{API_BASE}/api/collection/{user_id}/add"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(url, json={"card_id": card_id})
        r.raise_for_status()



# ==========================
# BOT SETUP
# ==========================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

async def load_cogs():
    await bot.load_extension("cogs.forest")


@bot.event
async def on_ready():
    # Load cogs/extensions first
    try:
        await load_cogs()
        print("✅ Cogs loaded.")
    except Exception as e:
        print("❌ Failed to load cogs:", e)

    # Sync slash commands (guild sync for fast updates)
    guild = discord.Object(id=GUILD_ID)
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"✅ Synced {len(synced)} command(s) to guild {GUILD_ID}.")
    except Exception as e:
        print(f"❌ Error syncing commands: {e}")

    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("💾 Using wos_data.json at:", WOS_DATA_PATH)
    print("------ World of Simia Bot is ready! ------")



# ==========================
# SIMPLE /wos COMMAND
# ==========================
@bot.tree.command(name="wos_starter", description="Get your first World of Simia card!")
async def wos_starter(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    cards_db = load_cards()

    # If they already have any cards, block starter claim (prevents repeats)
    existing_cards = await get_collection_from_api(user_id)
    if existing_cards:
        await interaction.response.send_message(
            "You already claimed your starter card! 🐒\nUse `/wos_collection` to see your cards.",
            ephemeral=True
        )
        return


    # Pick a starter at random from the pool
    starter_card_id = random.choice(STARTER_CARD_POOL)

    # Starter must exist in Cards.json
    starter = cards_db.get(starter_card_id)
    if starter is None:
        await interaction.response.send_message(
            f"⚠️ Starter card `{starter_card_id}` not found in Cards.json.",
            ephemeral=True
        )
        return

    # Create user record and give starter (store only the id)
    await add_card_via_api(user_id, starter_card_id)

    # Read fields exactly as your JSON uses them
    name = starter.get("name", starter_card_id)
    personality = starter.get("personality", "Unknown")
    status = starter.get("status", starter.get("Status", "None"))  # supports either key
    banana = starter.get("banana_size", "?")
    charm = starter.get("charm", "?")
    mischief = starter.get("mischief", "?")
    total = starter.get("total", "?")
    image_url = resolve_card_image_url(starter)

    # Build embed (same style as /wos_card)
    embed = discord.Embed(
        title=name,
        description=f"Personality: **{personality}**\nStatus: **{status}**"
    )

    embed.add_field(name="Banana Size", value=str(banana), inline=True)
    embed.add_field(name="Charm", value=str(charm), inline=True)
    embed.add_field(name="Mischief", value=str(mischief), inline=True)
    embed.add_field(name="Total", value=str(total), inline=True)

    if image_url:
        embed.set_image(url=image_url)

    await interaction.response.send_message(
        "🎉 You received your starter card!",
        embed=embed,
        ephemeral=True
    )



# ==========================
# VIEW COLLECTION
# ==========================
@bot.tree.command(name="wos_collection", description="View your World of Simia card collection.")
async def wos_collection(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    user_cards = await get_collection_from_api(user_id)

    if not user_cards:
        await interaction.response.send_message(
            "You don't have any cards yet. Try `/wos_starter` to get your first card!",
            ephemeral=True
        )
        return

    # Remove duplicates while preserving order
    seen = set()
    unique_cards = [cid for cid in user_cards if not (cid in seen or seen.add(cid))]

    collection_url = (
        "https://magnummonkey.github.io/world-of-simia-bot/"
        f"?user_id={user_id}"
    )

    await interaction.response.send_message(
        f"📜 **Your World of Simia Collection** (`{len(unique_cards)}` cards)\n\n"
        f"🖥️ **View your full collection here:**\n{collection_url}",
        ephemeral=True
    )



async def all_card_id_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete any card from Cards.json by card name or card_id."""

    if interaction.user.id != OWNER_ID:
        return []

    cards_db = load_cards()
    cur = (current or "").strip().lower()

    choices = []

    for cid, card in cards_db.items():
        name = str(card.get("name", cid))
        rarity = str(card.get("rarity", "Unknown")).title()

        searchable = f"{cid} {name}".lower()

        if cur and cur not in searchable:
            continue

        label = f"{name} ({rarity}) — {cid}"

        choices.append(
            app_commands.Choice(
                name=label[:100],
                value=cid
            )
        )

        if len(choices) >= 25:
            break

    print(f"🔎 /wos_card autocomplete user={interaction.user.id} current={current!r} choices={len(choices)}")
    return choices




@bot.tree.command(name="wos_card", description="Show details for a World of Simia card.")
@app_commands.describe(card_id="Start typing a card name or ID")
@app_commands.autocomplete(card_id=all_card_id_autocomplete)
async def wos_card(interaction: discord.Interaction, card_id: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message(
            "❌ You don’t have permission to use this command.",
            ephemeral=True
        )
        return

    # Load card database
    cards_db = load_cards()
    card = cards_db.get(card_id)

    if card is None:
        await interaction.response.send_message(
            f"❌ No card found with ID `{card_id}`.",
            ephemeral=True
        )
        return

    # Read stats from the card data
    name = card.get("name", card_id)
    personality = card.get("personality", "Unknown")
    status = card.get("status", "None") 
    banana = card.get("banana_size", "?")
    charm = card.get("charm", "?")
    mischief = card.get("mischief", "?")
    total = card.get("total", "?")

    # Build an embed
    embed = discord.Embed(
    title=name,
    description=(
        f"Personality: **{personality}**\n"
        f"Status: **{status}**"
    )
)


    # Row 1: 3 columns
    embed.add_field(name="Banana Size", value=str(banana), inline=True)
    embed.add_field(name="Charm", value=str(charm), inline=True)
    embed.add_field(name="Mischief", value=str(mischief), inline=True)
    embed.add_field(name="Total", value=str(total), inline=True)

    # Image
    image_url = resolve_card_image_url(card)

    if image_url:
        embed.set_image(url=image_url)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ======================
# DISCOVER
# ======================
@bot.tree.command(name="wos_discover", description="Discover 1 new card per day (choose 1 of 3).")
async def wos_discover(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    today = today_ymd()

    data = load_data()
    cards_db = load_cards()

    # Ensure user record exists
    if user_id not in data:
        data[user_id] = {"cards": []}

    # If they already completed a discovery today, block
    if data[user_id].get("last_discover_date") == today:
        await interaction.response.send_message(
            "⏳ You already discovered a card today. Come back tomorrow!",
            ephemeral=True
        )
        return

    # If they already have a pending discovery today, re-show the SAME options (prevents reroll abuse)
    pending = data[user_id].get("pending_discover")
    if pending and pending.get("date") == today and pending.get("options"):
        options = pending["options"]
    else:
        # Roll 3 options (duplicates allowed)
        rarity_index = build_rarity_index(cards_db)
        options = [roll_card_id(cards_db, rarity_index) for _ in range(3)]

        # Save pending options for today
        data[user_id]["pending_discover"] = {"date": today, "options": options}
        save_data(data)

    # Build a simple preview embed (names + rarity only)
    lines = []
    for i, cid in enumerate(options, start=1):
        c = cards_db.get(cid, {})
        nm = c.get("name", cid)
        rr = normalize_rarity(c.get("rarity", "common")).title()
        lines.append(f"**{i}. {nm}** — {rr}")

    embed = discord.Embed(
        title="🧭 Today’s Discoveries",
        description="Choose **1 of 3**. This locks your discovery for today.\n\n" + "\n".join(lines)
    )

    view = DiscoverView(owner_id=user_id, options=options)
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    #==============================
    #SHOP
    #==============================


@bot.tree.command(name="wos_shop", description="View today’s Banana Chip Shop (5 offers).")
async def wos_shop(interaction: discord.Interaction):
    user_id = str(interaction.user.id)
    today = today_ymd()

    data = load_data()
    cards_db = load_cards()
    user = ensure_user_record(data, user_id)

    # Ensure today's shop exists (and is stable—no rerolls)
    if user.get("shop_date") != today or not user.get("shop_offers"):
        user["shop_date"] = today
        user["shop_offers"] = generate_daily_shop_offers(cards_db)
        save_data(data)

    offers = user.get("shop_offers", [])

    embed = build_shop_embed(user, cards_db, offers)
    view = ShopView(owner_id=user_id, user=user)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)



    #========================
    # SUBMIT TO COMMUNITY CATALOG
    #========================
async def owned_card_id_autocomplete(interaction: discord.Interaction, current: str):
    """Autocomplete card_id to ONLY show cards the user owns (from API)."""
    user_id = str(interaction.user.id)

    # Pull owned cards from the API (source of truth)
    owned_ids = await get_collection_from_api(user_id)  # list[str]

    # Load card names/rarity for pretty labels
    cards_db = load_cards()

    cur = (current or "").lower()
    choices = []

    # Optional: de-dupe while preserving order
    seen = set()
    for cid in owned_ids:
        if not cid or cid in seen:
            continue
        seen.add(cid)

        if cur and cur not in cid.lower() and cur not in str(cards_db.get(cid, {}).get("name", "")).lower():
            continue

        c = cards_db.get(cid, {})
        nm = c.get("name", cid)
        rarity = str(c.get("rarity", "Unknown")).title()
        label = f"{nm} ({rarity}) — {cid}"

        choices.append(app_commands.Choice(name=label[:100], value=cid))
        if len(choices) >= 25:
            break

    return choices


@bot.tree.command(name="wos_submit", description="Submit a card to the Community Catalog (bot posts it in the forum).")
@app_commands.describe(card_id="Choose a card you own to add to the Community Catalog")
@app_commands.autocomplete(card_id=owned_card_id_autocomplete)
async def wos_submit(interaction: discord.Interaction, card_id: str):
    # Must be used in a server
    if interaction.guild_id is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    user_id = str(interaction.user.id)

    data = load_data()
    cards_db = load_cards()

    # Must own the card (check the API, not local JSON)
    owned_ids = await get_collection_from_api(user_id)  # this should return list[str] card_ids
    if card_id not in owned_ids:
        await interaction.response.send_message("❌ You can only submit cards you own.", ephemeral=True)
        return



    # Must exist in Cards.json
    card = cards_db.get(card_id)
    if not card:
        await interaction.response.send_message("❌ That card_id doesn’t exist in Cards.json.", ephemeral=True)
        return

    # Community catalog registry
    catalog = ensure_community_catalog(data)

    # If already cataloged, verify whether the saved thread still exists.
    # If it was deleted, remove the stale registry entry and allow resubmit.
    if card_id in catalog:
        thread_id = catalog[card_id].get("thread_id")

        if thread_id:
            try:
                thread = bot.get_channel(int(thread_id))
                if thread is None:
                    thread = await bot.fetch_channel(int(thread_id))

                # If fetch works, thread still exists -> block duplicate
                await interaction.response.send_message(
                    "🐒 That card is already in the Community Catalog.",
                    ephemeral=True
                )
                return

            except discord.NotFound:
                # Thread was deleted -> remove stale entry and continue
                del catalog[card_id]
                save_data(data)

            except discord.Forbidden:
                await interaction.response.send_message(
                    "❌ I can’t verify whether the existing catalog thread still exists (missing permissions).",
                    ephemeral=True
                )
                return

            except Exception:
                await interaction.response.send_message(
                    "❌ Something went wrong while checking the existing catalog entry.",
                    ephemeral=True
                )
                return
        else:
            # No thread_id stored -> stale entry, remove and continue
            del catalog[card_id]
            save_data(data)

    # Fetch the forum channel
    try:
        chan = bot.get_channel(CATALOG_FORUM_CHANNEL_ID)
        if chan is None:
            chan = await bot.fetch_channel(CATALOG_FORUM_CHANNEL_ID)
    except Exception:
        await interaction.response.send_message("❌ I couldn’t access the catalog forum channel.", ephemeral=True)
        return

    # Must be a ForumChannel
    if not isinstance(chan, discord.ForumChannel):
        await interaction.response.send_message("❌ The catalog channel is not a Forum Channel.", ephemeral=True)
        return

    # Build embed from card fields
    name = card.get("name", card_id)
    rarity = normalize_rarity(card.get("rarity", "common")).title()
    personality = card.get("personality", "Unknown")
    status = card.get("status", card.get("Status", "None"))
    banana = card.get("banana_size", "?")
    charm = card.get("charm", "?")
    mischief = card.get("mischief", "?")
    total = card.get("total", "?")
    image_url = resolve_card_image_url(card)


    embed = discord.Embed(
        title=f"{name} — {card_id}",
        description=f"Rarity: **{rarity}**\nPersonality: **{personality}**\nStatus: **{status}**"
    )
    embed.add_field(name="Banana Size", value=str(banana), inline=True)
    embed.add_field(name="Charm", value=str(charm), inline=True)
    embed.add_field(name="Mischief", value=str(mischief), inline=True)
    embed.add_field(name="Total", value=str(total), inline=True)
    if image_url:
        embed.set_image(url=image_url)

    # Create the forum thread
    thread = None
    try:
        # discord.py supports this signature on many versions
        thread, first_message = await chan.create_thread(
            name=f"{name} — {card_id}"[:100],
            content=f"Submitted by <@{interaction.user.id}>",
            embed=embed
        )
    except TypeError:
        # Fallback: create thread with content, then send embed into it
        thread = await chan.create_thread(
            name=f"{name} — {card_id}"[:100],
            content=f"Submitted by <@{interaction.user.id}>"
        )
        await thread.send(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ I don’t have permission to create posts in that forum channel.",
            ephemeral=True
        )
        return
    except Exception:
        await interaction.response.send_message(
            "❌ Failed to create the forum post (unexpected error).",
            ephemeral=True
        )
        return

    # Record in registry (prevents duplicates)
    catalog = ensure_community_catalog(data)  # re-get to be safe
    catalog[card_id] = {
        "thread_id": thread.id,
        "submitted_by": user_id
    }

    # Rewards for successful submit
    level_msgs = award_rewards(
        data,
        user_id,
        xp=CATALOG_SUBMIT_XP_REWARD,
        chips=CATALOG_SUBMIT_CHIPS_REWARD
    )

    save_data(data)

    msg = (
        f"✅ Added **{name}** to the Community Catalog!\n"
        f"+{CATALOG_SUBMIT_XP_REWARD} XP\n"
        f"+{CATALOG_SUBMIT_CHIPS_REWARD} Banana Chips 🍌"
    )

    if level_msgs:
        msg += "\n\n" + "\n".join(level_msgs)

    await interaction.response.send_message(
        msg,
        ephemeral=True
    )

#===============
#PROFILE
#===============

@bot.tree.command(name="wos_profile", description="View your World of Simia profile (Level, XP, Banana Chips, Collection).")
async def wos_profile(interaction: discord.Interaction):
    user_id = str(interaction.user.id)

    profile = await get_profile_from_api(user_id)

    if not profile:
        await interaction.response.send_message(
            "❌ I couldn’t load your profile from the API.",
            ephemeral=True
        )
        return

    level = int(profile.get("level", 1))
    xp_in_level = int(profile.get("xp_in_level", 0))
    req = int(profile.get("xp_required", 20))
    remaining = int(profile.get("xp_remaining", max(0, req - xp_in_level)))
    chips = int(profile.get("banana_chips", 0))
    owned_count = int(profile.get("cards_owned", 0))
    submitted_count = int(profile.get("catalog_submissions", 0))

    embed = discord.Embed(
        title="🐒 World of Simia Profile",
        description=f"**{interaction.user.display_name}**"
    )

    embed.add_field(name="Level", value=str(level), inline=True)
    embed.add_field(name="XP", value=f"{xp_in_level}/{req}  (**{remaining}** to level up)", inline=True)
    embed.add_field(name="Banana Chips", value=str(chips), inline=True)
    embed.add_field(name="Cards Owned", value=str(owned_count), inline=True)
    embed.add_field(name="Catalog Submissions", value=str(submitted_count), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


#==========================
#MANUAL PROFILE STAT EDITS
#==========================
@bot.tree.command(name="wos_dev_setprofile", description="DEV ONLY: Set a player's profile values.")
@app_commands.describe(
    member="Player whose profile you want to edit",
    level="New level",
    xp="Current XP within this level",
    banana_chips="New Banana Chip amount"
)
async def wos_dev_setprofile(
    interaction: discord.Interaction,
    member: discord.Member,
    level: int,
    xp: int,
    banana_chips: int
):
    if interaction.user.id not in DEV_USER_IDS:
        await interaction.response.send_message(
            "❌ You don’t have permission to use this command.",
            ephemeral=True
        )
        return

    if level < 1:
        await interaction.response.send_message(
            "❌ Level must be at least 1.",
            ephemeral=True
        )
        return

    if xp < 0 or banana_chips < 0:
        await interaction.response.send_message(
            "❌ XP and Banana Chips cannot be negative.",
            ephemeral=True
        )
        return

    user_id = str(member.id)

    data = load_data()
    user = get_profile_record(data, user_id)

    user["level"] = level
    user["xp"] = xp
    user["banana_chips"] = banana_chips

    save_data(data)

    await interaction.response.send_message(
        f"✅ Updated <@{user_id}>'s profile:\n"
        f"Level: **{level}**\n"
        f"XP: **{xp}**\n"
        f"Banana Chips: **{banana_chips}** 🍌",
        ephemeral=True
    )

#===================
#ammend catalog count
#=====================
@bot.tree.command(name="wos_dev_setcatalogcount", description="DEV ONLY: Set a player's displayed catalog submission count.")
@app_commands.describe(
    member="Player whose catalog count you want to correct",
    total="Correct total catalog submissions"
)
async def wos_dev_setcatalogcount(
    interaction: discord.Interaction,
    member: discord.Member,
    total: int
):
    if interaction.user.id not in DEV_USER_IDS:
        await interaction.response.send_message(
            "❌ You don’t have permission to use this command.",
            ephemeral=True
        )
        return

    if total < 0:
        await interaction.response.send_message(
            "❌ Catalog submissions cannot be negative.",
            ephemeral=True
        )
        return

    user_id = str(member.id)

    data = load_data()
    user = ensure_user_record(data, user_id)

    recorded = recorded_catalog_submission_count(data, user_id)

    # Example:
    # recorded = 1, desired total = 8
    # adjustment = 7
    user["catalog_submission_adjustment"] = total - recorded

    save_data(data)

    displayed = displayed_catalog_submission_count(data, user_id)

    await interaction.response.send_message(
        f"✅ Updated <@{user_id}>'s catalog submissions.\n"
        f"Recorded by bot: **{recorded}**\n"
        f"Manual adjustment: **{user['catalog_submission_adjustment']}**\n"
        f"Displayed total: **{displayed}**",
        ephemeral=True
    )


#====================
#BANANA CHIPS TO ALL
#====================    
@bot.tree.command(name="wos_dev_givechips_all", description="DEV ONLY: Give Banana Chips to every server member.")
@app_commands.describe(
    amount="How many Banana Chips to give each member",
    include_bots="Whether bots should also receive Banana Chips"
)
async def wos_dev_givechips_all(
    interaction: discord.Interaction,
    amount: int,
    include_bots: bool = False
):
    # 🔒 DEV lock
    if interaction.user.id not in DEV_USER_IDS:
        await interaction.response.send_message(
            "❌ You don’t have permission to use this command.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    if amount <= 0:
        await interaction.response.send_message(
            "❌ Amount must be greater than 0.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    data = load_data()

    granted_count = 0
    skipped_bots = 0

    for member in interaction.guild.members:
        if member.bot and not include_bots:
            skipped_bots += 1
            continue

        user_id = str(member.id)
        user = ensure_user_record(data, user_id)

        user["banana_chips"] = int(user.get("banana_chips", 0)) + amount
        granted_count += 1

    save_data(data)

    await interaction.followup.send(
        f"✅ Granted **{amount} Banana Chips** 🍌 to **{granted_count}** member(s).\n"
        f"Skipped bots: **{skipped_bots}**",
        ephemeral=True
    )


#===================
#  TRADE
#===================


@bot.tree.command(name="wos_trade", description="Propose a 1-to-1 card trade with another player (cards only).")
@app_commands.describe(
    member="The player you want to trade with",
    your_card_id="Card ID you will give",
    their_card_id="Card ID you want from them"
)
async def wos_trade(
    interaction: discord.Interaction,
    member: discord.Member,
    your_card_id: str,
    their_card_id: str
):
    # Must be used in a server
    if interaction.guild_id is None:
        await interaction.response.send_message("❌ This command can only be used in a server.", ephemeral=True)
        return

    from_id = str(interaction.user.id)
    to_id = str(member.id)

    # Can't trade yourself
    if from_id == to_id:
        await interaction.response.send_message("❌ You can’t trade with yourself.", ephemeral=True)
        return

    data = load_data()
    cards_db = load_cards()

    u_from = ensure_user_record(data, from_id)
    u_to = ensure_user_record(data, to_id)

    # Cooldown check (both users)
    if not can_trade(u_from):
        await interaction.response.send_message("⏳ You’re on trade cooldown (1 trade per 2 hours).", ephemeral=True)
        return
    if not can_trade(u_to):
        await interaction.response.send_message("⏳ That player is on trade cooldown (1 trade per 2 hours).", ephemeral=True)
        return

    # Validate IDs exist in Cards.json (optional but recommended)
    if your_card_id not in cards_db:
        await interaction.response.send_message("❌ Your card_id doesn’t exist in Cards.json.", ephemeral=True)
        return
    if their_card_id not in cards_db:
        await interaction.response.send_message("❌ Their card_id doesn’t exist in Cards.json.", ephemeral=True)
        return

    # Ownership checks
    if your_card_id not in u_from.get("cards", []):
        await interaction.response.send_message("❌ You don’t own that card.", ephemeral=True)
        return
    if their_card_id not in u_to.get("cards", []):
        await interaction.response.send_message("❌ That player doesn’t own the requested card.", ephemeral=True)
        return

    # Create pending trade record
    pending = ensure_pending_trades(data)

    # Unique trade id
    trade_id = f"{int(time.time())}_{from_id}_{to_id}"

    trade = {
        "from": from_id,
        "to": to_id,
        "give": your_card_id,
        "want": their_card_id,
        "phase": "offer",        # offer -> confirm
        "confirmed": {},         # used in confirm phase
    }

    pending[trade_id] = trade
    save_data(data)

    # Build embed + view
    embed = build_trade_embed(trade, cards_db, status_line="🟦 Waiting for the target player to **Accept** or **Decline**.")
    view = TradeView(trade_id=trade_id, from_id=from_id, to_id=to_id, give_id=your_card_id, want_id=their_card_id)

    # Send NON-ephemeral so both can click
    await interaction.response.send_message(
        content=f"🔔 <@{to_id}> trade request from <@{from_id}>",
        embed=embed,
        view=view
    )


#================================================
# SHOW DECK
#================================================

@bot.tree.command(name="wos_dev_addcard", description="DEV ONLY: Add a single card to your collection (no duplicates).")
@app_commands.describe(card_id="Exact card_id from Cards.json")
async def wos_dev_addcard(interaction: discord.Interaction, card_id: str):
    # 🔒 DEV lock
    if interaction.user.id not in DEV_USER_IDS:
        await interaction.response.send_message("❌ You don’t have permission to use this command.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    # 📘 Validate card exists
    cards_db = load_cards()
    if card_id not in cards_db:
        await interaction.followup.send(
            f"❌ Unknown card_id `{card_id}` (not found in Cards.json).",
            ephemeral=True
        )
        return

    user_id = str(interaction.user.id)
    data = load_data()

    # 🧍 Ensure user exists
    users = data.setdefault("users", {})
    user = users.setdefault(user_id, {})
    cards = user.setdefault("cards", [])

    # 🚫 Prevent duplicates
    if card_id in cards:
        await interaction.followup.send(
            f"⚠️ You already own `{card_id}`. No duplicate added.",
            ephemeral=True
        )
        return

    # ✅ Grant exactly ONE card (uses your existing logic + API update)
    await grant_card(user_id, card_id, data)

    await interaction.followup.send(
        f"✅ Added `{card_id}` to your collection.",
        ephemeral=True
    )

# ==========================
# RUN BOT
# ==========================
if __name__ == "__main__":
    bot.run(BOT_TOKEN)


