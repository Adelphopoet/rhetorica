#!/bin/sh
# Собирает rhetorica-kit в apps/web/public/install: архив плагина и marketplace.json для Claude Code.
# Запускается как prebuild web (pnpm --filter @rhetorica/web build) и вручную: sh tools/agent-kit/build.sh
# KIT_BASE_URL — публичный адрес папки install (по умолчанию прод), нужен для абсолютной ссылки на архив.
set -eu
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PLUGIN="$ROOT/tools/agent-kit/plugin"
OUT="$ROOT/apps/web/public/install"
BASE_URL="${KIT_BASE_URL:-https://rhetorica.example.com/install}"
VERSION=$(sed -n 's/.*"version": *"\([^"]*\)".*/\1/p' "$PLUGIN/.claude-plugin/plugin.json")

rm -rf "$OUT"
mkdir -p "$OUT"
(cd "$PLUGIN" && zip -qrX "$OUT/rhetorica-kit.zip" . -x '*/.DS_Store' -x '.DS_Store')
if command -v sha256sum >/dev/null 2>&1; then
  SHA=$(sha256sum "$OUT/rhetorica-kit.zip" | cut -d' ' -f1)
else
  SHA=$(shasum -a 256 "$OUT/rhetorica-kit.zip" | cut -d' ' -f1)
fi

cat > "$OUT/marketplace.json" <<JSON
{
  "name": "rhetorica",
  "owner": { "name": "Rhetorica" },
  "metadata": { "description": "Наборы для Claude Code поверх Rhetorica MCP", "version": "${VERSION}" },
  "plugins": [
    {
      "name": "rhetorica-kit",
      "description": "Данные через Rhetorica MCP плюс скилл аналитика с базой знаний и отчётами.",
      "version": "${VERSION}",
      "source": { "source": "archive", "url": "${BASE_URL}/rhetorica-kit.zip", "sha256": "${SHA}" }
    }
  ]
}
JSON
echo "rhetorica-kit ${VERSION} -> ${OUT} (sha256 ${SHA})"
