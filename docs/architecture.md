# Architecture

Drive originals -> GPU OCR -> runtime Markdown/images -> edited JSON -> site + export.

The repository has apps/knowledge-web, content, catalog, packages/knowledge-model,
tools/{inventory,extract,media,export}, legacy and docs. Editorial categories have
reserved folders; currently JSON is canonical so browser edits are immediate and
unambiguous. Markdown bodies live in entity records. Separate Markdown files can
be exported later, without creating a second editable authority.

`config.local.json` defines sourceRoot and runtimeRoot; .env.example documents the
equivalent environment overrides for the server. Models may be cached on another
local volume, independently of source documents and the Git repository.
On WSL, sourceRoot points to the Windows Drive mount; runtimeRoot points to the
Linux filesystem. pythonExecutable selects the existing OCR environment, and
ocrEngine selects the conversion backend. The web app uses Node 24. The optional
portable Node installation in runtime can be loaded with tools/environment.sh.

OCR runs independently of the web server, one page at a time with a durable
checkpoint. Docling uses the existing Biosense environment and local layout,
RapidOCR and accurate TableFormer models. Changing engines requires separate
outputs or an archive, keeping previous extractions and edited entities intact.
The site polls runtime/ocr-status.json only while Sources is active.
After conversion, tools/extract/publish-transcripts.mjs copies completed Markdown
into content/transcriptions by stable source ID, with provenance and quality
manifests. These project-local inputs support database authoring; edited JSON
remains authoritative. Runtime images are linked locally, never copied into Git.

The legacy data contains 120 crew members, 74 rooms, 21 droid catalog entries,
73 posts and 57 historical groups. Historical groups are not automatically factions.
The hierarchy view uses sections/groups, not invented reporting lines.

The site reads source text per PDF page and searches completed extraction pages.
Every source link retains a stable source ID and a one-based PDF page number.
Book pagination can differ from printed pagination; references explicitly use PDF pages.

Stellar maps are Drive assets; pins associate an entity with normalized coordinates.
FarStar decks use native SVG geometry in campaign.ship.deckPlans, manually traced
from the six Drive reference plans. Regions associate stable room IDs with calibrated
percentage polygons; uncertain associations stay null. Editing polygon vertices,
labels and associations uses the same revisioned campaign persistence. Room media
references and provenance live in rooms[].media; uploads stay in runtime/media/rooms.
Occupancy is derived from shift assignments, preserving
shared and possible-extra semantics. Configure shift starts from the header.

Writes check the revision of the file last read, reject stale writes with HTTP 409,
validate incoming JSON, serialize writes, save the previous version in runtime/backups
and atomically replace the authoritative JSON. No credentials or telemetry are sent
to third parties by the app. Loopback hosting is for personal use; visibility metadata
does not implement player authentication or filtering in this version.

The future mastering app consumes knowledge-v1.json with stable crew/entity IDs.
Live positions, wounds, discovery state and session events belong to that app.
