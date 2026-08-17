#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

echo "Capturing the canonical dashboard reveal with Playwright."
echo "The tracked fallback will be written under workshop/fallback/."
CDOT_CAPTURE_WORKSHOP_VIDEO=1 npm --prefix frontend run test:e2e -- --grep "workshop fallback recording"

VIDEO_SOURCE="$(find frontend/test-results/workshop-video -type f -name video.webm -print -quit)"
if [[ -z "$VIDEO_SOURCE" || ! -s "$VIDEO_SOURCE" ]]; then
  echo "Playwright completed but no non-empty video.webm was found." >&2
  exit 1
fi
VIDEO_DESTINATION="workshop/fallback/CDOT_UPF_Closed_Loop_Dashboard_Reveal.webm"
cp "$VIDEO_SOURCE" "$VIDEO_DESTINATION"
echo "$VIDEO_DESTINATION"
