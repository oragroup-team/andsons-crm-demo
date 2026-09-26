"""Regenerates hero_image_catalog/ (one folder, real images + real descriptions)
from image_bank.py's HERO_BANK - the single source of truth the Copywriter
actually prompts from. Run this any time HERO_BANK changes so the human-facing
catalog never drifts from what the agent really sees.

Usage: python3 build_hero_catalog.py
"""

import shutil
from pathlib import Path

from image_bank import HERO_BANK

HERE = Path(__file__).parent
SOURCE_DIR = HERE / "static" / "hero_images"
CATALOG_DIR = HERE / "hero_image_catalog"


def build():
    CATALOG_DIR.mkdir(exist_ok=True)

    # Clear out any stale images from a previous bank so removed entries
    # don't linger and confuse whoever's browsing the catalog.
    for existing in CATALOG_DIR.glob("*.jpg"):
        existing.unlink()

    rows = []
    for key, entry in HERO_BANK.items():
        filename = f"{key}.jpg"
        src = SOURCE_DIR / filename
        if not src.exists():
            raise FileNotFoundError(f"HERO_BANK entry '{key}' has no file at {src}")
        shutil.copy(src, CATALOG_DIR / filename)

        categories = entry["category"]
        if entry.get("exclude_categories"):
            categories += f" (not used for: {', '.join(entry['exclude_categories'])})"

        rows.append(
            f"## {filename}\n\n"
            f"**Used for category:** {categories}\n\n"
            f"**What the photo actually shows:** {entry['description']}\n\n"
            f"**When the Copywriter picks it:** {entry['moment']}\n"
        )

    readme = (
        "# OVA hero image catalog\n\n"
        "Every image the OvaEmail agent is allowed to pick as a hero photo, in one "
        "folder, next to the real description the agent is actually shown when "
        "choosing. This file is generated from `image_bank.py`'s `HERO_BANK` - the "
        "same dict the Copywriter prompts from - so it can never drift out of sync "
        "with what the agent really sees. Do not hand-edit the descriptions here; "
        "edit `image_bank.py` and rerun `python3 build_hero_catalog.py`.\n\n"
        "There is also always a `none` option (a deliberate text-only email, no "
        "hero) that has no image file since it renders nothing.\n\n"
        + "\n---\n\n".join(rows)
    )
    (CATALOG_DIR / "README.md").write_text(readme)

    print(f"Wrote {len(rows)} images + README.md to {CATALOG_DIR}")


if __name__ == "__main__":
    build()
