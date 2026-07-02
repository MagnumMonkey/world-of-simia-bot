from pathlib import Path
import json
import shutil
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
CARDS_JSON = REPO_ROOT / "Cards.json"

def clean_base_url(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.startswith("https://"):
        raise ValueError("Public R2 URL must start with https://")
    return url

def update_card_image_url(old_url: str, public_base_url: str) -> str:
    if not old_url:
        return old_url

    old_url = old_url.strip()

    # Already hosted somewhere online, leave it alone.
    if old_url.startswith("http://") or old_url.startswith("https://"):
        return old_url

    # Convert:
    # /static/cards/card_name.png
    # to:
    # https://pub-whatever.r2.dev/cards/card_name.png
    if old_url.startswith("/static/cards/"):
        filename = old_url.split("/")[-1]
        return f"{public_base_url}/cards/{filename}"

    # Convert old WebP paths too, just in case:
    # /static/cards_web/card_name.webp
    if old_url.startswith("/static/cards_web/"):
        filename = old_url.split("/")[-1]
        return f"{public_base_url}/cards/{filename}"

    return old_url

def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print('python tools/update_cards_json_to_r2.py "https://YOUR_PUBLIC_R2_URL"')
        sys.exit(1)

    public_base_url = clean_base_url(sys.argv[1])

    if not CARDS_JSON.exists():
        print(f"Could not find Cards.json at: {CARDS_JSON}")
        sys.exit(1)

    backup_path = CARDS_JSON.with_name("Cards.before_r2_update.json")
    shutil.copy2(CARDS_JSON, backup_path)
    print(f"Backup created: {backup_path}")

    with open(CARDS_JSON, "r", encoding="utf-8") as f:
        cards = json.load(f)

    updated = 0
    skipped = 0

    for card_id, card in cards.items():
        if not isinstance(card, dict):
            skipped += 1
            continue

        old_url = card.get("image_url_web", "")

        new_url = update_card_image_url(old_url, public_base_url)

        if new_url != old_url:
            card["image_url_web"] = new_url
            updated += 1
        else:
            skipped += 1

    with open(CARDS_JSON, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2, ensure_ascii=False)

    print(f"Updated image_url_web entries: {updated}")
    print(f"Skipped unchanged entries: {skipped}")
    print("Done. Cards.json now points to the R2 banana vault.")

if __name__ == "__main__":
    main()