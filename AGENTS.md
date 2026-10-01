# DarkStryder campaign workspace

Personal, local-first campaign knowledge site in French. User: dunkean.
Primary goal: information accessible in one click for preparation and play: scenario,
crew, NPCs, factions, events, planets, stellar maps, FarStar decks and assignments.

## Authoritative data and boundaries

- Originals: local Google Drive mirror, configured by config.local.json.
- Editable canonical site data: content/campaign.json (existing FarStar schema 2.1),
  content/entities.json (knowledge entities, pins and shift schedule).
- Stable IDs, not names/paths, connect entities and the future mastering project.
- catalog/sources.json and assets.json reference originals. Do not copy PDFs into Git.
- OCR outputs, images, models, logs and backups live outside this repository in runtime.
- Keep the legacy console for comparison; never silently delete original inputs.
- Extracted text is evidence, not editorial truth. Do not overwrite edited content when re-importing.
- During content production, use extracted Markdown first and open the original PDF
  only when an ambiguity, a statistic or a suspected OCR error requires it.
- Do not feed entire sourcebooks to the assistant. Avoid large tool outputs. OCR is local GPU;
  no paid/cloud OCR or LLM calls without an explicit user request.
- Preserve distinctions between source canon, personal additions, interpretation and generated stats.
- Do not invent coordinates, command relationships or campaign events. Empty fields remain explicit.

## Application

Astro builds the page shell; browser ES modules implement views. A minimal Node HTTP
server bound to 127.0.0.1 serves the site, selected Drive media and runtime outputs.
JSON writes use validation, optimistic revisions, an atomic replacement and a backup.
Content must be editable from the site and persisted to JSON. A browser-only download
or localStorage save does not satisfy persistence. Keep original inline-handler
compatibility contained in app.js while progressively modernizing modules.

Clock selects configurable shifts (initially 00:00 / 12:00), not precise simulated
movement. Extras are possible assignments; alert disables them. Coordinates on maps
and decks are calibrated manually and stored as percentages, never guessed.

No production infrastructure, containers or elaborate CI/CD for the web app. Minimal
CI runs npm ci and npm run check. No automatic deployment. Future mastering owns
live campaign state; this project exports reference knowledge via a versioned JSON.

## Commands

- npm start: build and serve http://localhost:4321.
- npm run check: build + persistence and DOM integration tests.
- npm run inventory: identify sourcebooks and deduplicate by SHA-256.
- npm run ocr: local CUDA extraction, resumable by source/page.
- npm run export: knowledge-v1.json under runtime/exports.

Check persistence, conflict rejection, path confinement and navigation after substantive
changes. Preserve all unknown fields when editing the existing campaign schema.
Never print, copy into Git, or embed the GitHub token in remote URLs; its configured
location is outside the project. Keep local paths/config and binaries out of Git.
