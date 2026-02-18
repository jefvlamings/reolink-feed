#!/usr/bin/env bash
set -euo pipefail

SOURCE_FILE="${3:-custom_components/reolink_feed/frontend/reolink-feed-card.js}"
TARGET_FILE="${4:-config/www/reolink-feed-card.js}"
FILE="${1:-config/.storage/lovelace_resources}"
CARD_PATH="${2:-/local/reolink-feed-card.js}"

if [ ! -f "$SOURCE_FILE" ]; then
  echo "Card source not found: $SOURCE_FILE" >&2
  exit 1
fi

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE" >&2
  exit 1
fi

mkdir -p "$(dirname "$TARGET_FILE")"
cp "$SOURCE_FILE" "$TARGET_FILE"
echo "Synced card: $SOURCE_FILE -> $TARGET_FILE"

node - "$FILE" "$CARD_PATH" <<'NODE'
const fs = require("fs");

const file = process.argv[2];
const cardPath = process.argv[3];
const raw = fs.readFileSync(file, "utf8");
const json = JSON.parse(raw);
const items = json?.data?.items;

if (!Array.isArray(items)) {
  console.error("Invalid lovelace_resources format");
  process.exit(1);
}

let updated = false;
for (const item of items) {
  if (!item || typeof item.url !== "string") continue;
  if (!item.url.startsWith(cardPath)) continue;

  const [base, query = ""] = item.url.split("?");
  const params = new URLSearchParams(query);
  const current = Number.parseInt(params.get("v") || "0", 10);
  const next = Number.isFinite(current) ? current + 1 : 1;
  params.set("v", String(next));
  item.url = `${base}?${params.toString()}`;
  console.log(`Updated resource URL to: ${item.url}`);
  updated = true;
}

if (!updated) {
  console.error(`No matching resource found for ${cardPath}`);
  process.exit(1);
}

fs.writeFileSync(file, `${JSON.stringify(json, null, 2)}\n`);
NODE

echo "Done. Reload Home Assistant UI."
echo "Restarting Home Assistant container..."
docker compose restart homeassistant
echo "Done. Home Assistant container restarted."
