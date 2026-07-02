from pathlib import Path
from PIL import Image, ImageOps
import json
import shutil

REPO_ROOT = Path(__file__).resolve().parents[1]

INPUT_DIR = REPO_ROOT / "static" / "cards"
OUTPUT_DIR = REPO_ROOT / "static" / "cards_web"
CARDS_JSON = REPO_ROOT / "Cards.json"

MAX_HEIGHT = 900
QUALITY = 78

SUPPORTED = {".png", ".jpg", ".jpeg"}

OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

converted = {}

print(f"Reading images from: {INPUT_DIR}")
print(f"Writing web images to: {OUTPUT_DIR}")

for img_path in INPUT_DIR.iterdir():
    if img_path.suffix.lower() not in SUPPORTED:
        print(f"Skipping unsupported file: {img_path.name}")
        continue

    out_path = OUTPUT_DIR / f"{img_path.stem}.webp"

    try:
        with Image.open(img_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")

            if img.height > MAX_HEIGHT:
                ratio = MAX_HEIGHT / img.height
                new_width = int(img.width * ratio)
                img = img.resize((new_width, MAX_HEIGHT), Image.LANCZOS)

            img.save(out_path, "WEBP", quality=QUALITY, method=6)

        converted[img_path.name] = out_path.name
        print(f"Converted: {img_path.name} -> {out_path.name}")

    except Exception as e:
        print(f"FAILED: {img_path.name} | {e}")

# Backup Cards.json
backup_path = CARDS_JSON.with_suffix(".json.bak")
shutil.copy2(CARDS_JSON, backup_path)
print(f"Backed up Cards.json to: {backup_path}")

with open(CARDS_JSON, "r", encoding="utf-8") as f:
    cards = json.load(f)

updated = 0

for card_id, card in cards.items():
    old_url = card.get("image_url_web") or ""

    # Example:
    # /static/cards/juan_the_weenie_waver.png
    if old_url.startswith("/static/cards/"):
        old_filename = old_url.split("/")[-1]
        old_stem = Path(old_filename).stem
        new_filename = f"{old_stem}.webp"
        new_path = OUTPUT_DIR / new_filename

        if new_path.exists():
            card["image_url_web"] = f"/static/cards_web/{new_filename}"
            updated += 1

    # If image_url_web is missing, try card_id.png/jpg/jpeg
    elif not old_url:
        for ext in [".png", ".jpg", ".jpeg"]:
            possible = OUTPUT_DIR / f"{card_id}.webp"
            if possible.exists():
                card["image_url_web"] = f"/static/cards_web/{card_id}.webp"
                updated += 1
                break

with open(CARDS_JSON, "w", encoding="utf-8") as f:
    json.dump(cards, f, indent=2, ensure_ascii=False)

print(f"Updated Cards.json entries: {updated}")
print("Done. Tiny image goblin compressed.")