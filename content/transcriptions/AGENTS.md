# Repository Guidelines

## Project Structure & Module Organization

This directory contains published transcription evidence. Each stable `src-<hex>` directory contains an assembled `book.md`, page-level Markdown under `pages/`, and provenance in `manifest.json` and `transcription.json`. `index.json` catalogs source status; `_resources/resources.json` catalogs independent illustrations. Generated extraction state belongs in `_extraction/`. The adjacent Python tools, `extract_entities.py` and `build_kbase.py`, process the corpus. Repository-wide application code is under `../../apps/` and `../../packages/`, automation under `../../tools/`, and tests under `../../tests/`.

Do not treat transcripts as editorial truth. Preserve source IDs, PDF page markers, hashes, warnings, and distinctions between canon, add-ons, and external references. Never overwrite a locally corrected transcript or commit original PDFs, page scans, models, logs, or runtime checkpoints.

## Build, Test, and Development Commands

Run repository commands from the project root (`../..`):

- `npm ci` installs the locked Node dependencies (Node 24+).
- `npm run check` builds the Astro application and runs all Node tests.
- `npm test` runs `node:test` suites in `tests/*.test.mjs`.
- `npm run corpus:test` checks the Python document converters.
- `npm run transcripts:publish` publishes completed runtime OCR while protecting local edits.
- `python content/transcriptions/extract_entities.py all --collections darkstryder,addons,sourcebooks,guides-rules` resumes local entity extraction; it requires the configured local OpenAI-compatible server.

## Coding Style & Naming Conventions

Follow existing style: four spaces and type hints in Python; two spaces, semicolons, and ES modules in JavaScript. Use `snake_case` for Python functions, `camelCase` for JavaScript, zero-padded page names such as `pages/0001.md`, and immutable `src-` identifiers. Keep JSON UTF-8, valid, and consistently formatted. No formatter or linter is configured, so match neighboring code and keep diffs focused.

## Testing Guidelines

Add Node regression tests as `tests/<feature>.test.mjs` using `node:test` and strict assertions. Prefer temporary directories and always clean them in `finally`. For publishing changes, test provenance, path confinement, resumability, and refusal to replace edited Markdown. Run `npm run check` before submitting.

## Commit & Pull Request Guidelines

Git history is unavailable in this checkout, so no verified commit convention can be inferred. Use short, imperative, scoped subjects, for example `transcripts: preserve local page edits`. Pull requests should summarize intent, identify affected source IDs, list validation commands, and call out regenerated or excluded artifacts. Include screenshots only for visible web changes; link relevant issues when applicable.
