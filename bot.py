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

def build_shop_embed(chips: int, owned_ids: set[str], cards_db: dict, offers: list[dict]) -> discord.Embed:
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
        if cid in owned_ids:
            flags.append("OWNED")
        if not sold and cid not in owned_ids and chips < price:
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
    result = []
    page = 1

    async with aiohttp.ClientSession() as session:
        while True:
            url = f"{API_BASE}/api/collection/{user_id}?page={page}&limit=18"

            async with session.get(url) as resp:
                if resp.status != 200:
                    return result

                payload = await resp.json()
                cards = payload.get("cards", []) or []

                for c in cards:
                    if isinstance(c, str):
                        result.append(c)
                    elif isinstance(c, dict) and c.get("card_id"):
                        result.append(c["card_id"])

                total_pages = int(payload.get("total_pages", 1))

                if page >= total_pages:
                    break

                page += 1

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


async def get_catalog_entry_from_api(card_id: str) -> dict | None:
    url = f"{API_BASE}/api/catalog/{card_id}"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                return None

            return await resp.json()


async def submit_catalog_to_api(user_id: str, card_id: str, thread_id: int) -> dict:
    url = f"{API_BASE}/api/catalog/{user_id}/submit"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            json={
                "card_id": card_id,
                "thread_id": int(thread_id)
            },
            headers=api_admin_headers()
        )

    if r.status_code != 200:
        raise RuntimeError(f"API error {r.status_code}: {r.text[:500]}")

    return r.json()

async def reward_profile_via_api(user_id: str, xp: int = 0, banana_chips: int = 0) -> dict:
    url = f"{API_BASE}/api/profile/{user_id}/reward"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            json={
                "xp": xp,
                "banana_chips": banana_chips
            },
            headers=api_admin_headers()
        )

    if r.status_code != 200:
        raise RuntimeError(f"API reward error {r.status_code}: {r.text[:500]}")

    return r.json()

async def buy_shop_card_via_api(user_id: str, card_id: str, price: int) -> dict:
    url = f"{API_BASE}/api/shop/{user_id}/buy"

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.post(
            url,
            json={
                "card_id": card_id,
                "price": int(price)
            },
            headers=api_admin_headers()
        )

    if r.status_code != 200:
        raise RuntimeError(f"API shop error {r.status_code}: {r.text[:500]}")

    return r.json()


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
            await interaction.response.send_message(
                "❌ This discovery isn’t yours.",
                ephemeral=True
            )
            return

        data = load_data()
        cards_db = load_cards()
        user_id = str(interaction.user.id)
        today = today_ymd()

        user = ensure_user_record(data, user_id)

        # If they already claimed today, block
        if user.get("last_discover_date") == today:
            await interaction.response.send_message(
                "⏳ You already discovered a card today. Come back tomorrow!",
                ephemeral=True
            )
            return

        # Must have a pending discovery to claim
        pending = user.get("pending_discover")
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

        card = cards_db.get(chosen_id, {})
        if not card:
            await interaction.response.send_message(
                f"❌ Card `{chosen_id}` was not found in Cards.json.",
                ephemeral=True
            )
            return

        # ✅ API is source of truth for ownership
        owned_ids = await get_collection_from_api(user_id)
        is_new = chosen_id not in owned_ids

        duplicate_payout = 0

        try:
            if is_new:
                await add_card_via_api(user_id, chosen_id)
                title_line = "🎉 New discovery!"
                desc_line = "Added to your collection."
            else:
                rarity = normalize_rarity(card.get("rarity", "common"))
                duplicate_payout = int(DUP_SELL_VALUES.get(rarity, 0))
                title_line = "🐒 Duplicate discovered"
                desc_line = f"Sold for **{duplicate_payout} Banana Chips**."

            total_chips_reward = DISCOVER_CHIPS_REWARD + duplicate_payout

            reward_result = await reward_profile_via_api(
                user_id,
                xp=DISCOVER_XP_REWARD,
                banana_chips=total_chips_reward
            )

        except Exception as e:
            await interaction.response.send_message(
                f"❌ Discovery reward failed through the API: `{e}`",
                ephemeral=True
            )
            return

        # Lock the day only after API success
        user["last_discover_date"] = today
        user.pop("pending_discover", None)

        # Optional local mirror for old commands; API remains source of truth
        if is_new and chosen_id not in user.get("cards", []):
            user.setdefault("cards", []).append(chosen_id)

        save_data(data)

        name = card.get("name", chosen_id)
        personality = card.get("personality", "Unknown")
        status = card.get("status", card.get("Status", "None"))
        banana = card.get("banana_size", "?")
        charm = card.get("charm", "?")
        mischief = card.get("mischief", "?")
        total = card.get("total", "?")
        image_url = resolve_card_image_url(card)

        level_msgs = reward_result.get("level_messages", []) or []

        reward_text = (
            f"+{DISCOVER_XP_REWARD} XP\n"
            f"+{DISCOVER_CHIPS_REWARD} Banana Chips 🍌"
        )

        if duplicate_payout:
            reward_text += f"\n+{duplicate_payout} Duplicate Banana Chips 🍌"

        level_text = "\n\n".join(level_msgs) if level_msgs else ""

        embed = discord.Embed(
            title=name,
            description=(
                f"{title_line}\n"
                f"{desc_line}\n"
                f"{reward_text}"
                f"{'\n\n' + level_text if level_text else ''}\n\n"
                f"Personality: **{personality}**\n"
                f"Status: **{status}**"
            )
        )

        embed.add_field(name="Banana Size", value=str(banana), inline=True)
        embed.add_field(name="Charm", value=str(charm), inline=True)
        embed.add_field(name="Mischief", value=str(mischief), inline=True)
        embed.add_field(name="Total", value=str(total), inline=True)

        if image_url:
            embed.set_image(url=image_url)

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
            await interaction.response.send_message(
                "❌ This shop isn’t yours.",
                ephemeral=True
            )
            return

        user_id = view.owner_id
        today = today_ymd()

        data = load_data()
        cards_db = load_cards()
        user = ensure_user_record(data, user_id)

        if user.get("shop_date") != today or not user.get("shop_offers"):
            await interaction.response.send_message(
                "⚠️ Your shop expired. Run `/wos_shop` again.",
                ephemeral=True
            )
            return

        offers = user.get("shop_offers", [])

        if self.slot_index < 0 or self.slot_index >= len(offers):
            await interaction.response.send_message(
                "⚠️ Invalid shop selection.",
                ephemeral=True
            )
            return

        offer = offers[self.slot_index]

        if offer.get("sold", False):
            await interaction.response.send_message(
                "⚠️ That item is already SOLD.",
                ephemeral=True
            )
            return

        cid = offer.get("card_id")
        price = int(offer.get("price", 0))

        if cid not in cards_db:
            await interaction.response.send_message(
                "❌ That shop card no longer exists in Cards.json.",
                ephemeral=True
            )
            return

        try:
            result = await buy_shop_card_via_api(user_id, cid, price)
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Purchase failed through the API: `{e}`",
                ephemeral=True
            )
            return

        offer["sold"] = True
        save_data(data)

        chips = int(result.get("banana_chips", 0))
        owned_ids = set(await get_collection_from_api(user_id))

        new_embed = build_shop_embed(chips, owned_ids, cards_db, offers)

        level_msgs = result.get("level_messages", []) or []
        xp_reward = int(result.get("xp_reward", 0))

        card_name = cards_db.get(cid, {}).get("name", cid)

        new_embed.description += (
            f"\n\n✅ Purchased **{card_name}** for **{price} Banana Chips** 🍌"
            f"\n+{xp_reward} XP"
        )

        if level_msgs:
            new_embed.description += "\n\n" + "\n".join(level_msgs)

        view.refresh_buttons(offers, chips, owned_ids)

        await interaction.response.edit_message(
            embed=new_embed,
            view=view
        )


class ShopView(discord.ui.View):
    def __init__(self, owner_id: str, offers: list[dict], chips: int, owned_ids: set[str]):
        super().__init__(timeout=180)
        self.owner_id = owner_id

        for i in range(SHOP_SIZE):
            self.add_item(ShopBuyButton(i))

        self.refresh_buttons(offers, chips, owned_ids)

    def refresh_buttons(self, offers: list[dict], chips: int, owned_ids: set[str]):
        for item in self.children:
            if not isinstance(item, ShopBuyButton):
                continue

            idx = item.slot_index

            if idx >= len(offers):
                item.disabled = True
                item.label = f"Buy #{idx + 1}"
                continue

            offer = offers[idx]
            cid = offer.get("card_id")
            price = int(offer.get("price", 0))
            sold = bool(offer.get("sold", False))

            if sold:
                item.disabled = True
                item.label = f"SOLD #{idx + 1}"
            elif cid in owned_ids:
                item.disabled = True
                item.label = f"OWNED #{idx + 1}"
            elif chips < price:
                item.disabled = True
                item.label = f"{price} chips"
            else:
                item.disabled = False
                item.label = f"Buy {price}"


API_BASE = "https://wos-api-production.up.railway.app".rstrip("/")

WOS_ADMIN_KEY = os.getenv("WOS_ADMIN_KEY", "")

def api_admin_headers() -> dict:
    return {"X-WOS-ADMIN-KEY": WOS_ADMIN_KEY}


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
    user = ensure_user_record(data, user_id)

    # If they already completed a discovery today, block
    if user.get("last_discover_date") == today:
        await interaction.response.send_message(
            "⏳ You already discovered a card today. Come back tomorrow!",
            ephemeral=True
        )
        return

    # If they already have a pending discovery today, re-show the SAME options (prevents reroll abuse)
    pending = user.get("pending_discover")
    if pending and pending.get("date") == today and pending.get("options"):
        options = pending["options"]
    else:
        # Roll 3 options (duplicates allowed)
        rarity_index = build_rarity_index(cards_db)
        options = [roll_card_id(cards_db, rarity_index) for _ in range(3)]

        # Save pending options for today
        user["pending_discover"] = {"date": today, "options": options}
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

    if user.get("shop_date") != today or not user.get("shop_offers"):
        user["shop_date"] = today
        user["shop_offers"] = generate_daily_shop_offers(cards_db)
        save_data(data)

    offers = user.get("shop_offers", [])

    profile = await get_profile_from_api(user_id)
    if not profile:
        await interaction.response.send_message(
            "❌ I couldn’t load your profile from the API.",
            ephemeral=True
        )
        return

    chips = int(profile.get("banana_chips", 0))
    owned_ids = set(await get_collection_from_api(user_id))

    embed = build_shop_embed(chips, owned_ids, cards_db, offers)
    view = ShopView(
        owner_id=user_id,
        offers=offers,
        chips=chips,
        owned_ids=owned_ids
    )

    await interaction.response.send_message(
        embed=embed,
        view=view,
        ephemeral=True
    )



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
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "❌ This command can only be used in a server.",
            ephemeral=True
        )
        return

    if not WOS_ADMIN_KEY:
        await interaction.response.send_message(
            "❌ WOS_ADMIN_KEY is not set on the bot service.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    user_id = str(interaction.user.id)

    cards_db = load_cards()

    owned_ids = await get_collection_from_api(user_id)
    if card_id not in owned_ids:
        await interaction.followup.send(
            "❌ You can only submit cards you own.",
            ephemeral=True
        )
        return

    card = cards_db.get(card_id)
    if not card:
        await interaction.followup.send(
            "❌ That card_id doesn’t exist in Cards.json.",
            ephemeral=True
        )
        return

    catalog_entry = await get_catalog_entry_from_api(card_id)

    if catalog_entry and catalog_entry.get("exists"):
        await interaction.followup.send(
            "🐒 That card is already in the Community Catalog.",
            ephemeral=True
        )
        return

    try:
        chan = bot.get_channel(CATALOG_FORUM_CHANNEL_ID)
        if chan is None:
            chan = await bot.fetch_channel(CATALOG_FORUM_CHANNEL_ID)
    except Exception:
        await interaction.followup.send(
            "❌ I couldn’t access the catalog forum channel.",
            ephemeral=True
        )
        return

    if not isinstance(chan, discord.ForumChannel):
        await interaction.followup.send(
            "❌ The catalog channel is not a Forum Channel.",
            ephemeral=True
        )
        return

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
        description=(
            f"Rarity: **{rarity}**\n"
            f"Personality: **{personality}**\n"
            f"Status: **{status}**"
        )
    )

    embed.add_field(name="Banana Size", value=str(banana), inline=True)
    embed.add_field(name="Charm", value=str(charm), inline=True)
    embed.add_field(name="Mischief", value=str(mischief), inline=True)
    embed.add_field(name="Total", value=str(total), inline=True)

    if image_url:
        embed.set_image(url=image_url)

    thread = None

    try:
        try:
            thread, first_message = await chan.create_thread(
                name=f"{name} — {card_id}"[:100],
                content=f"Submitted by <@{interaction.user.id}>",
                embed=embed
            )
        except TypeError:
            thread = await chan.create_thread(
                name=f"{name} — {card_id}"[:100],
                content=f"Submitted by <@{interaction.user.id}>"
            )
            await thread.send(embed=embed)

    except discord.Forbidden:
        await interaction.followup.send(
            "❌ I don’t have permission to create posts in that forum channel.",
            ephemeral=True
        )
        return

    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to create the forum post: `{e}`",
            ephemeral=True
        )
        return

    try:
        result = await submit_catalog_to_api(
            user_id=user_id,
            card_id=card_id,
            thread_id=int(thread.id)
        )

    except Exception as e:
        await interaction.followup.send(
            "⚠️ The forum post was created, but the API failed to record the catalog submission.\n"
            f"Error: `{e}`\n\n"
            "The banana accountant tripped. Check the API logs before submitting again.",
            ephemeral=True
        )
        return

    xp_reward = int(result.get("xp_reward", CATALOG_SUBMIT_XP_REWARD))
    chips_reward = int(result.get("chips_reward", CATALOG_SUBMIT_CHIPS_REWARD))
    catalog_total = int(result.get("catalog_submissions", 0))
    level_msgs = result.get("level_messages", []) or []

    msg = (
        f"✅ Added **{name}** to the Community Catalog!\n"
        f"+{xp_reward} XP\n"
        f"+{chips_reward} Banana Chips 🍌\n"
        f"Catalog Submissions: **{catalog_total}**"
    )

    if level_msgs:
        msg += "\n\n" + "\n".join(level_msgs)

    await interaction.followup.send(
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
@bot.tree.command(name="wos_dev_setprofile", description="DEV ONLY: Set a player's API profile values.")
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

    if not WOS_ADMIN_KEY:
        await interaction.response.send_message(
            "❌ WOS_ADMIN_KEY is not set on the bot service.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    user_id = str(member.id)
    url = f"{API_BASE}/api/profile/{user_id}/set"

    payload = {
        "level": level,
        "xp": xp,
        "banana_chips": banana_chips
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                url,
                json=payload,
                headers=api_admin_headers()
            )

        if r.status_code != 200:
            await interaction.followup.send(
                f"❌ API rejected the profile update.\n"
                f"Status: `{r.status_code}`\n"
                f"Response: `{r.text[:500]}`",
                ephemeral=True
            )
            return

        result = r.json()

    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to update profile through the API: `{e}`",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        f"✅ Updated <@{user_id}>'s API profile:\n"
        f"Level: **{result.get('level', level)}**\n"
        f"XP: **{result.get('xp', xp)}**\n"
        f"Banana Chips: **{result.get('banana_chips', banana_chips)}** 🍌",
        ephemeral=True
    )

#===================
#ammend catalog count
#=====================
@bot.tree.command(name="wos_dev_setcatalogcount", description="DEV ONLY: Set a player's API catalog submission count.")
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

    if not WOS_ADMIN_KEY:
        await interaction.response.send_message(
            "❌ WOS_ADMIN_KEY is not set on the bot service.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    user_id = str(member.id)
    url = f"{API_BASE}/api/profile/{user_id}/catalog_count/set"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                url,
                json={"total": total},
                headers=api_admin_headers()
            )

        if r.status_code != 200:
            await interaction.followup.send(
                f"❌ API rejected the catalog update.\n"
                f"Status: `{r.status_code}`\n"
                f"Response: `{r.text[:500]}`",
                ephemeral=True
            )
            return

        result = r.json()

    except Exception as e:
        await interaction.followup.send(
            f"❌ Failed to update catalog count through the API: `{e}`",
            ephemeral=True
        )
        return

    await interaction.followup.send(
        f"✅ Updated <@{user_id}>'s API catalog submissions.\n"
        f"Recorded by API: **{result.get('recorded', 0)}**\n"
        f"Manual adjustment: **{result.get('adjustment', 0)}**\n"
        f"Displayed total: **{result.get('displayed_total', total)}**",
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


#================================================
# ADD CARD
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


