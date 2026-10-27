#!/usr/bin/env python3
"""
extract_entities.py

Per-document, category-driven extraction of the Star Wars D6 corpus (per-page OCR Markdown)
with a local OpenAI-compatible server. Everything happens inside ONE document; merging
across documents (phase 4) and conversion to JSON (phase 5) are later steps, described in
EXTRACTION.md and not implemented here.

Phase 1 (enumerate, no thinking)
    Pages go --chunk-size at a time with --overlap pages of overlap, one call per
    category. The model sees the whole category taxonomy (definitions + boundaries) and
    answers one line per entity:  name | aliases | hint | profile pages | role pages.
    "profile" = the entity's own entry (description, statistics, background);
    "role" = page where it acts or where something new is learned about it.
    Near-empty pages of long documents are skipped; short documents (<= --min-doc-pages)
    are packed together into shared chunks.
        <out>/phase1/<category>/<source-id>.json

Phase 2 (deduplicate inside the document)
    Identical names (with or without titles) merge in code; close names and shared aliases
    are confirmed by the LLM judge; multi-name clusters get a canonical name arbitrated by
    the LLM (OCR errors corrected).
        <out>/phase2/<category>/<source-id>.json

Phase 3 (one Markdown document per entity and per book)
    ONE call with thinking per entity: every page found for it in the book (profile pages
    + the following page, and scene pages) -> one well-written Markdown document gathering
    all its information (structure suggested per category, numbers kept as a sheet, pages
    cited inline). Front matter (id, aliases, pages...) is written by code. Only when the
    pages exceed --max-doc-chars are scene pages left out (listed in the document).
        <out>/documents/<source-id>/<category>/<name>--<id8>.md
        <out>/documents/<source-id>/index.json

Robustness: every LLM call is cached on disk, so re-running the same command resumes.
--attempts per call (default 3, slightly hotter each time); an unreachable server is
waited for (--server-wait) without consuming attempts; output cut by the token cap
(repetition loop) is retried, then a phase-1 chunk is split in halves; failed entities
are logged in phase3/_errors.jsonl and retried by the next run; unexpected crashes
restart the run (--restarts). Log: <out>/run.log. Use --force to recompute.

Examples
    python extract_entities.py all --limit 0 --collections darkstryder,addons,sourcebooks,guides-rules
    python extract_entities.py phase1 --sources src-1678b1eb8c4bd691 --categories character
    python extract_entities.py phase3 --sources src-1678b1eb8c4bd691 --categories character --only kyli

Extracted content is unreviewed evidence, not editorial truth.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from build_kbase import (
    ENTITY_NAMESPACE,
    build_page_chunks,
    extract_json_from_text,
    json_dump,
    json_load,
    load_pages,
    norm_text,
    slug,
    source_role_metadata,
)

PIPELINE = "extract_entities/2.0"

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
# label: short name used in prompts; scope: what belongs here; not: boundaries with the other
# categories; outline: suggested (not imposed) structure of the phase-3 document.
CATEGORIES: dict[str, dict[str, str]] = {
    "character": {
        "label": "characters",
        "scope": "named individuals: NPCs, player characters, historical figures, and individual "
                 "droids or creatures that have their own name and personality",
        "not": "a species or creature type (species); a group (organization); a droid model (technology)",
        "outline": "identity (species, gender, occupation, affiliation); appearance and personality; "
                   "background; role in the scenario (motivations, goals, secrets, how they act); "
                   "relationships; game statistics sheet (attributes, skills, special abilities, "
                   "Force/Character/Dark Side points, move, equipment); roleplaying notes and quotes",
    },
    "species": {
        "label": "species and creatures",
        "scope": "sentient species, races, and creature or animal types: biology, culture, homeworld, "
                 "game template",
        "not": "one named member (character); the government, clan or army of a species as a political "
               "group (organization)",
        "outline": "overview; physiology; homeworld and distribution; society and culture; relations with "
                   "other species; game template (attribute dice, special abilities, story factors, move, "
                   "size) or creature statistics",
    },
    "organization": {
        "label": "organizations",
        "scope": "groups with members and structure: governments, empires, military forces and units, "
                 "companies, guilds, crime syndicates, pirate bands, ship crews, cults, rebel cells, "
                 "tribes or clans acting as political groups",
        "not": "a species as a whole (species); a single member (character)",
        "outline": "overview; structure and leadership; members, forces and assets; goals and methods; "
                   "relations with other groups; history; game notes (typical members' statistics)",
    },
    "location": {
        "label": "locations",
        "scope": "places: regions, sectors, star systems, planets, moons, cities, settlements, bases, "
                 "buildings, sites, and space stations or ship areas treated as places to visit",
        "not": "a ship as a vessel (craft); an event that happened there (history/adventure)",
        "outline": "overview (type, system, region); description and geography; inhabitants and groups "
                   "present; points of interest; history; use in the scenario (encounters, hazards, "
                   "secrets); data sheet (planet function, type, temperature, atmosphere, hydrosphere, "
                   "gravity, terrain, day/year length, species, starport, population, tech level...)",
    },
    "craft": {
        "label": "starships, vehicles and stations",
        "scope": "named starships and vehicles, ship/vehicle classes and models, space stations treated "
                 "as vessels",
        "not": "droids (character if an individual, technology if a model); a place inside a ship "
               "visited as a location (location)",
        "outline": "overview (type, class, owner); description and history; role in the scenario; "
                   "technical sheet (craft, type, scale, length, skill, crew, passengers, cargo, "
                   "consumables, cost, hyperdrive, nav computer, maneuverability, space, atmosphere, "
                   "hull, shields, sensors, weapons with fire arc, crew, skill, fire control, range, damage)",
    },
    "technology": {
        "label": "technology and equipment",
        "scope": "weapons, armor, gear, devices, droid models, cybernetics, drugs, artifacts, relics and "
                 "unusual technologies",
        "not": "vessels (craft); an individual droid with a personality (character)",
        "outline": "overview; description and function; origin and availability; use in the scenario; "
                   "game data (cost, availability, model, damage, range, difficulty, special rules)",
    },
    "history": {
        "label": "history of the setting",
        "scope": "past events presented as facts of the setting: wars, battles, disasters, discoveries, "
                 "foundations, eras and timeline entries",
        "not": "what people tell or believe (legend); events the players will live during the adventure "
               "(adventure); ongoing schemes (intrigue)",
        "outline": "summary; when and where; participants; course of events; consequences; differing "
                   "accounts",
    },
    "legend": {
        "label": "legends and beliefs",
        "scope": "myths, legends, prophecies, religions and cults' beliefs, folklore, superstitions, "
                 "rumours — what people tell or believe, true or not",
        "not": "events presented as established facts (history)",
        "outline": "the legend as told; who tells or believes it; truth behind it if the text says so; "
                   "use in the scenario",
    },
    "adventure": {
        "label": "adventures and scenes",
        "scope": "playable units of the scenario: adventures, episodes, acts, scenes, encounters, set "
                 "pieces, with their setup and sequence",
        "not": "threads running across scenes (intrigue); past events of the setting (history)",
        "outline": "summary; place in the campaign (before/after); setup and start; scenes and encounters "
                   "in order; key characters and locations; objectives, rewards and outcomes; game data "
                   "(difficulties, statistics of opponents)",
    },
    "intrigue": {
        "label": "intrigues and plot threads",
        "scope": "threads the gamemaster manages across scenes: plots and conspiracies, antagonists' "
                 "plans, secrets and revelations, clues, hooks, objectives, threats, subplots, rewards",
        "not": "one specific scene or encounter (adventure)",
        "outline": "summary; who is behind it and what is at stake; secrets and revelations; clues and "
                   "where they are found; hooks; possible developments and outcomes; game data",
    },
    "rules": {
        "label": "game rules",
        "scope": "game mechanics: rules, optional and house rules, skills, Force powers, special "
                 "abilities, difficulty and random tables, campaign mechanics",
        "not": "the statistics of one character, creature or ship (they belong to that entity)",
        "outline": "purpose; complete rule text with every number; tables; examples; options and variants",
    },
    "setting": {
        "label": "general setting information",
        "scope": "general information on the Star Wars setting not centred on one named entity above: "
                 "galactic situation and politics, era, economy and trade, society, communications, "
                 "travel conditions of a region",
        "not": "anything that is mainly about one named entity of another category",
        "outline": "overview; details; consequences for play; related entities",
    },
}

TAXONOMY = "\n".join(
    f'- {k} ({v["label"]}): {v["scope"]}. NOT: {v["not"]}.' for k, v in CATEGORIES.items()
) + """
General rules: a named individual is a character whatever their species. A species is never an
organization. Past facts are history, beliefs and tales are legend, events played during the
adventure are adventure, threads managed across scenes are intrigue."""

IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$", re.M)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.S)
PAGE_REF = re.compile(r"(\d{1,4})")
NOTHING = "NOTHING_FOUND"

_print_lock = threading.Lock()
LOG_FILE: Path | None = None  # set from --log-file


def log(*args: Any) -> None:
    line = " ".join(str(a) for a in args)
    with _print_lock:
        print(line, flush=True)
        if LOG_FILE:
            with LOG_FILE.open("a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")


def clean_page_text(text: str) -> str:
    text = HTML_COMMENT.sub("", text)
    text = IMAGE_LINE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def page_file(n: int) -> str:
    return f"{n:04d}.md"


def short_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def strip_repetition(text: str) -> str:
    """Drop exact repeated lines (beyond blank lines/table rules) — the usual shape of a loop."""
    out, seen = [], Counter()
    for line in text.splitlines():
        key = line.strip()
        if key and not set(key) <= set("-|: #*") and len(key) > 3:
            seen[key] += 1
            if seen[key] > 2:
                continue
        out.append(line)
    return "\n".join(out).strip()


# ---------------------------------------------------------------------------
# LLM server
# ---------------------------------------------------------------------------

class ServerDown(Exception):
    """The LLM server stayed unreachable longer than --server-wait."""


class LLMError(Exception):
    """One failed request (HTTP error, timeout...): counts as an attempt."""


def is_connection_error(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return False
    if isinstance(e, urllib.error.URLError):
        return True
    return isinstance(e, (ConnectionError, OSError)) and not isinstance(e, TimeoutError)


class LLM:
    """Minimal OpenAI-compatible client. thinking=False sends reasoning_effort='none' and
    enable_thinking=false; servers ignore the switch they do not know."""

    def __init__(self, base_url: str, model: str | None, timeout: int, attempts: int,
                 max_concurrent: int = 1, server_wait: int = 3600):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.attempts = max(1, attempts)
        self.server_wait = server_wait
        self.slots = threading.BoundedSemaphore(max(1, max_concurrent))
        self.model = model or self._discover_model()
        log(f"[llm] model={self.model} base={self.base_url}")

    def server_up(self) -> bool:
        try:
            with urllib.request.urlopen(self.base_url + "/models", timeout=15):
                return True
        except Exception:  # noqa: BLE001
            return False

    def wait_for_server(self) -> None:
        """Block while the server is unreachable (crash/restart); give up after server_wait s."""
        t0 = time.time()
        while not self.server_up():
            if time.time() - t0 > self.server_wait:
                raise ServerDown(f"server unreachable for {self.server_wait}s: {self.base_url}")
            log(f"  [llm] server unreachable, waiting... ({int(time.time() - t0)}s)")
            time.sleep(30)

    def _discover_model(self) -> str:
        self.wait_for_server()
        with urllib.request.urlopen(self.base_url + "/models", timeout=30) as resp:
            models = json.loads(resp.read().decode("utf-8")).get("data") or []
        if not models:
            raise RuntimeError("No model loaded on the server.")
        return models[0]["id"]

    def complete(self, system: str, user: str, *, thinking: bool, effort: str | None = None,
                 max_tokens: int, temperature: float, penalties: dict | None = None) -> tuple[str, dict]:
        """One completion. Waits for the server when it is down; other failures raise LLMError."""
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
            "top_p": 0.95,
            "repeat_penalty": 1.05,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if thinking and effort:
            payload["reasoning_effort"] = effort  # low | medium | high
        if not thinking:
            payload["reasoning_effort"] = "none"
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        if penalties:
            payload.update(penalties)
        data = json.dumps(payload).encode("utf-8")
        while True:
            req = urllib.request.Request(self.base_url + "/chat/completions", data=data, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with self.slots, urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    out = json.loads(resp.read().decode("utf-8"))
                choice = out["choices"][0]
                return choice["message"].get("content") or "", {
                    "finish_reason": choice.get("finish_reason"), "usage": out.get("usage")}
            except Exception as e:  # noqa: BLE001
                if is_connection_error(e) and not self.server_up():
                    log(f"  [llm] connection lost ({str(e)[:80]})")
                    self.wait_for_server()  # crash/restart: wait, then resend (not an attempt)
                    continue
                detail = e.read().decode("utf-8", "replace")[:200] if isinstance(e, urllib.error.HTTPError) else ""
                raise LLMError(f"{str(e)[:120]} {detail}") from e


def llm_call(llm: LLM, path: Path, force: bool, system: str, user: str, *,
             parse: Callable[[str], Any] | None = None, accept_truncated: bool = False,
             **kw: Any) -> Any:
    """Cached call with up to llm.attempts attempts, a little hotter each time.
    - output cut by the token cap (a repetition loop) is retried; the best cut output is
      used only if accept_truncated;
    - parse(text) raising counts as a failed attempt.
    Returns parse(text) (or text), or None when every attempt failed."""
    if path.exists() and not force:
        text = json_load(path)["text"]
        return parse(text) if parse else text
    base_temp = kw.pop("temperature")
    best: tuple[str, dict] | None = None
    t0 = time.time()
    for attempt in range(llm.attempts):
        try:
            text, meta = llm.complete(system, user, temperature=base_temp + 0.15 * attempt, **kw)
        except LLMError as e:
            log(f"  [llm] attempt {attempt + 1}/{llm.attempts} failed: {e}")
            time.sleep(2 * (attempt + 1))
            continue
        text = strip_repetition(text)
        if meta.get("finish_reason") == "length":
            log(f"  [llm] attempt {attempt + 1}/{llm.attempts}: output hit the token cap (loop)")
            if best is None or len(text) > len(best[0]):
                best = (text, meta)
            continue
        try:
            value = parse(text) if parse else text
        except Exception as e:  # noqa: BLE001
            log(f"  [llm] attempt {attempt + 1}/{llm.attempts}: unusable output ({str(e)[:100]})")
            continue
        _save_call(path, llm, kw, t0, meta, text, truncated=False)
        return value
    if best and accept_truncated:
        try:
            value = parse(best[0]) if parse else best[0]
        except Exception:  # noqa: BLE001
            return None
        log(f"  [llm] keeping the output cut by the token cap: {path.name}")
        _save_call(path, llm, kw, t0, best[1], best[0], truncated=True)
        return value
    return None


def _save_call(path: Path, llm: LLM, kw: dict, t0: float, meta: dict, text: str, truncated: bool) -> None:
    json_dump(path, {"model": llm.model, "thinking": kw.get("thinking"), "effort": kw.get("effort"),
                     "seconds": round(time.time() - t0, 1), "truncated": truncated, "meta": meta,
                     "text": text})


def run_parallel_ordered(tasks: list, fn, workers: int) -> list:
    if workers <= 1 or len(tasks) <= 1:
        return [fn(t) for t in tasks]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, tasks))


def run_parallel(tasks: list, fn, workers: int) -> list:
    if workers <= 1:
        return [fn(t) for t in tasks]
    out = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(fn, t) for t in tasks]):
            out.append(f.result())
    return out


# ---------------------------------------------------------------------------
# Corpus selection
# ---------------------------------------------------------------------------

SOURCE_IDS: list[str] = []  # set from --sources
COLLECTIONS: list[str] = []  # set from --collections


def select_sources(root: Path, index: str, limit: int) -> list[dict]:
    """--sources ids, or complete documents (ordered by --collections), first N if limit > 0."""
    idx = json_load(root / index)
    sources = [s for s in idx.get("sources", []) if s.get("status") == "complete" and s.get("markdown")]
    if SOURCE_IDS:
        missing = set(SOURCE_IDS) - {s["id"] for s in sources}
        if missing:
            raise SystemExit(f"Unknown or incomplete sources: {sorted(missing)}")
        return [s for s in sources if s["id"] in SOURCE_IDS]
    if COLLECTIONS:
        def rank(src):
            cols = set(src.get("collections") or []) | {src.get("collection")}
            return min((COLLECTIONS.index(c) for c in cols if c in COLLECTIONS), default=None)
        sources = sorted((x for x in sources if rank(x) is not None), key=rank)  # stable sort
    # Explicit deferred sources go after all collections, preserving their chosen order.
    order = json_load(root / "extraction-order.json", {}) or {}
    deferred = order.get("defer_sources", [])
    deferred_rank = {sid: i for i, sid in enumerate(deferred)}
    sources = ([s for s in sources if s["id"] not in deferred_rank]
               + sorted((s for s in sources if s["id"] in deferred_rank),
                        key=lambda s: deferred_rank[s["id"]]))
    return sources[:limit] if limit > 0 else sources


def parse_categories(value: str | None) -> list[str]:
    if not value:
        return list(CATEGORIES)
    cats = [c.strip() for c in value.split(",") if c.strip()]
    bad = [c for c in cats if c not in CATEGORIES]
    if bad:
        raise SystemExit(f"Unknown categories {bad}; choose from {list(CATEGORIES)}")
    return cats


# ---------------------------------------------------------------------------
# Phase 1: enumeration
# ---------------------------------------------------------------------------

ENUM_VERSION = "v4-taxonomy-lines"  # bump when the phase-1 prompt/format changes

SYSTEM_ENUM = """You index Star Wars D6 tabletop RPG sourcebook pages (OCR Markdown) for a campaign
knowledge base. Use only the supplied text, never outside knowledge. Follow the output format exactly."""

# Game statistics blocks that upgrade a cited "role" page to "profile" for the entity named above.
STAT_BLOCKS = {
    "character": re.compile(r"\bDEXTERITY\b\s*:?\s*\d+\s*D", re.I),
    "species": re.compile(r"\bDEXTERITY\b\s*:?\s*\d+\s*D", re.I),
    "craft": re.compile(r"\b(Maneuverability|Hull)\b\s*:?\s*\d+\s*D", re.I),
}


def has_stat_block(cat: str, name: str, aliases: list[str], text: str) -> bool:
    """A stat block whose preceding lines name the entity (any core token of its names)."""
    rx = STAT_BLOCKS.get(cat)
    if not rx:
        return False
    tokens = {t for n in [name, *aliases] for t in core_tokens(name_key(n)) if len(t) >= 4}
    for m in rx.finditer(text):
        window = norm_text(text[max(0, m.start() - 400):m.start()])
        if any(re.search(rf"\b{re.escape(t)}\b", window) for t in tokens):
            return True
    return False


def enum_prompt(cat: str, chunk_text: str) -> str:
    c = CATEGORIES[cat]
    # Pages first: identical prefix across the categories of a chunk.
    return f"""SOURCE PAGES:
{chunk_text}

=====
CATEGORY TAXONOMY (every entity belongs to exactly one category):
{TAXONOMY}

TASK: list the {c["label"]} ({cat}) found in the pages above, usable in the tabletop RPG scenario
or setting. Only this category: entities of another category of the taxonomy are not listed here.

Rules:
- One line per distinct entity; never repeat an entity. Spelling variants and titles go in aliases.
- name: the clean name only (no statistics, no description), most complete form in the text.
- hint: at most 12 words saying what it is.
- Pages: file names exactly as in the "=====" headers above.
  - profile pages: pages holding the entity's OWN entry: a section or paragraph headed by its name,
    its game statistics, or its description. Usually 1 to 3 pages.
  - role pages: pages where the entity acts or where something NEW is learned about it.
  - Do not list pages where it is only mentioned without new information about it.
- Keep an entity only if it has at least one profile or role page.
- Ignore credits, publishers, page headers and footers, real-world people.

OUTPUT FORMAT: one line per entity, five fields separated by " | ", nothing else:
name | alias1; alias2 | hint | profile pages separated by commas | role pages separated by commas
Leave a field empty when there is nothing (keep the separators). If there is no {c["label"]}
in these pages, output exactly: NONE"""


def parse_enum_lines(text: str) -> list[dict]:
    """Lines 'name | aliases | hint | profile | role' -> entries. Raises on unusable output."""
    text = text.strip()
    if not text or re.match(r"\W*NONE(?![A-Za-z])", text, re.I):
        return []
    rows, seen = [], set()
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if line.count("|") < 3 or set(line) <= set("|-: "):
            continue
        f = [x.strip() for x in line.split("|")] + ["", "", "", "", ""]
        name = f[0].strip("`*\"' ")
        if not name or name.lower() in ("name", "none") or norm_text(name) in seen:
            continue
        seen.add(norm_text(name))
        rows.append({
            "name": name,
            "aliases": [a.strip() for a in re.split(r"[;]", f[1]) if a.strip()][:6],
            "hint": f[2][:160],
            "profile": [p for p in re.split(r"[,\s]+", f[3]) if p],
            "role": [p for p in re.split(r"[,\s]+", f[4]) if p],
        })
    if not rows:
        raise ValueError("no entity line in the expected format")
    return rows


def content_pages(pages: list, args) -> list:
    """Drop near-empty pages (covers, full-page art) of multi-page documents only.
    Short documents (single-page notes, handouts...) are always kept whole."""
    if len(pages) <= args.min_doc_pages:
        return pages
    return [pg for pg in pages if len(clean_page_text(pg.text)) >= args.min_page_chars]


@dataclass
class EnumChunk:
    """Pages sent together to phase 1: (source, page, label) items."""
    chunk_id: str
    items: list

    def render(self) -> str:
        return "\n\n".join(f"===== {label} =====\n{clean_page_text(pg.text)}" for _, pg, label in self.items)

    def halves(self) -> list["EnumChunk"]:
        k = len(self.items) // 2
        return [EnumChunk(f"{self.chunk_id}-a", self.items[:k]), EnumChunk(f"{self.chunk_id}-b", self.items[k:])]

    def resolve(self, refs: list[str]) -> list[tuple[str, int]]:
        """Page references -> (source id, page), tolerant to '12', '0012', '0012.md', '02-0001.md'."""
        by_key = {tuple(int(x) for x in re.findall(r"\d+", label)): (src["id"], pg.number)
                  for src, pg, label in self.items}
        return [by_key[k] for r in refs
                if (k := tuple(int(x) for x in re.findall(r"\d+", str(r)))) in by_key]


def work_units(root: Path, sources: list[dict], args) -> list[list[tuple[dict, list]]]:
    """Long documents form their own unit; consecutive short documents (<= --min-doc-pages
    pages) are packed together up to --chunk-size pages (one call per pack and category)."""
    units, pack, size = [], [], 0
    for src in sources:
        pages = content_pages(load_pages(root, src), args)
        if not pages:
            continue
        if len(pages) > args.min_doc_pages:
            units.append([(src, pages)])
            continue
        if pack and size + len(pages) > args.chunk_size:
            units.append(pack)
            pack, size = [], 0
        pack.append((src, pages))
        size += len(pages)
    if pack:
        units.append(pack)
    deferred = (json_load(root / "extraction-order.json", {}) or {}).get("defer_sources", [])
    deferred_rank = {sid: i for i, sid in enumerate(deferred)}
    units.sort(key=lambda unit: min((deferred_rank[src["id"]] + 1
                                    for src, _ in unit if src["id"] in deferred_rank), default=0))
    return units


def unit_chunks(unit: list[tuple[dict, list]], args) -> list[EnumChunk]:
    if len(unit) == 1 and len(unit[0][1]) > args.min_doc_pages:
        src, pages = unit[0]
        return [EnumChunk(c.chunk_id, [(src, pg, page_file(pg.number)) for pg in c.pages])
                for c in build_page_chunks(pages, args.chunk_size, args.overlap)]
    # Shared chunk of short documents: labels "<doc #>-<page>.md".
    items = [(src, pg, f"{k:02d}-{pg.number:04d}.md") for k, (src, pages) in enumerate(unit, 1) for pg in pages]
    key = short_hash("|".join(f"{src['id']}:{pg.number}" for src, pg, _ in items))
    return [EnumChunk(f"pack-{key}", items)]


def phase1(args, llm: LLM, root: Path, out: Path, sources: list[dict] | None = None) -> None:
    sources = sources if sources is not None else select_sources(root, args.index, args.limit)
    cats = parse_categories(args.categories)
    penalties = {"frequency_penalty": args.frequency_penalty, "presence_penalty": args.presence_penalty}
    for ui, unit in enumerate(work_units(root, sources, args), 1):
        chunks = unit_chunks(unit, args)
        title = unit[0][0]["name"][:60] if len(unit) == 1 else f"{len(unit)} short documents packed"
        log(f"[phase1] {title} - {sum(len(p) for _, p in unit)} pages, {len(chunks)} chunks x {len(cats)} categories")
        where = unit[0][0]["id"] if len(unit) == 1 else "_packs"

        def cache_path(chunk, cat):
            return out / "phase1" / "_calls" / where / ENUM_VERSION / cat / f"{chunk.chunk_id}.json"

        def extract(chunk, cat) -> None:
            """One chunk; if every attempt loops or fails, its two halves separately."""
            path = cache_path(chunk, cat)
            if not args.force and (path.exists() or (
                    len(chunk.items) > 1 and all(cache_path(h, cat).exists() for h in chunk.halves()))):
                return
            t0 = time.time()
            rows = llm_call(llm, path, args.force, SYSTEM_ENUM, enum_prompt(cat, chunk.render()),
                            parse=parse_enum_lines, accept_truncated=len(chunk.items) == 1,
                            thinking=False, max_tokens=args.enum_max_tokens,
                            temperature=args.temperature, penalties=penalties)
            if rows is None:
                if len(chunk.items) > 1:
                    log(f"  [split] {chunk.chunk_id} {cat}: retrying as two halves")
                    for h in chunk.halves():
                        extract(h, cat)
                else:
                    log(f"  [error] {chunk.chunk_id} {cat}: failed")
                return
            log(f"  {chunk.chunk_id} {cat:<12} {len(rows):>3} entities  {time.time() - t0:5.1f}s")

        def chunk_results(chunk, cat) -> tuple[list, list]:
            """(sub-chunk, rows) pairs covering the chunk, and ids of parts without result."""
            cached = json_load(cache_path(chunk, cat))
            if cached is not None:
                return [(chunk, parse_enum_lines(cached["text"]))], []
            if len(chunk.items) > 1 and any(cache_path(h, cat).exists() for h in chunk.halves()):
                found, missing = [], []
                for h in chunk.halves():
                    f, m = chunk_results(h, cat)
                    found += f
                    missing += m
                return found, missing
            return [], [chunk.chunk_id]

        # Chunk-major, category-minor: same pages prefix on consecutive calls.
        run_parallel([(c, cat) for c in chunks for cat in cats], lambda t: extract(*t), args.workers)

        for src, pages in unit:  # one file per (category, document)
            sid = src["id"]
            page_text = {pg.number: pg.text for pg in pages}
            src_chunks = [c for c in chunks if any(x[0]["id"] == sid for x in c.items)]
            for cat in cats:
                merged: dict[str, dict] = {}
                parts, missing = [], []
                for chunk in src_chunks:
                    found, lost = chunk_results(chunk, cat)
                    parts += found
                    missing += lost
                for chunk, rows in parts:
                    for e in rows:
                        key = norm_text(e["name"])
                        prof = {n for s_, n in chunk.resolve(e["profile"]) if s_ == sid}
                        role = {n for s_, n in chunk.resolve(e["role"]) if s_ == sid} - prof
                        if not key or (not prof and not role):
                            continue  # nothing informative in this document's pages
                        m = merged.setdefault(key, {"name": e["name"], "aliases": [], "hint": "",
                                                    "profile": set(), "role": set()})
                        for a in e["aliases"]:
                            if norm_text(a) != key and a not in m["aliases"]:
                                m["aliases"].append(a)
                        m["hint"] = m["hint"] or e["hint"]
                        m["profile"].update(prof)
                        m["role"].update(role)
                rows_out = []
                for m in sorted(merged.values(), key=lambda m: norm_text(m["name"])):
                    upgraded = {n for n in m["role"] if has_stat_block(cat, m["name"], m["aliases"], page_text[n])}
                    prof = m["profile"] | upgraded
                    role = m["role"] - prof
                    rows_out.append({
                        "name": m["name"], "aliases": m["aliases"], "hint": m["hint"],
                        "profile": [page_file(n) for n in sorted(prof)],
                        "role": [page_file(n) for n in sorted(role)],
                    })
                json_dump(out / "phase1" / cat / f"{sid}.json", rows_out)
                marker = out / "phase1" / cat / f"{sid}.incomplete"
                if missing:
                    log(f"  [incomplete] {cat} {sid}: {len(missing)} chunk(s) failed; re-run to retry")
                    marker.write_text("\n".join(missing), encoding="utf-8")
                elif marker.exists():
                    # Now complete: the deduplication built on partial data must be recomputed.
                    marker.unlink()
                    (out / "phase2" / cat / f"{sid}.json").unlink(missing_ok=True)
                if len(unit) == 1:
                    log(f"  -> {cat:<12} {len(rows_out):>4} entities in {sid}")


# ---------------------------------------------------------------------------
# Phase 2: deduplication inside one document
# ---------------------------------------------------------------------------

TITLES = {
    "the", "a", "an", "captain", "capt", "commander", "general", "admiral", "moff", "grand",
    "lord", "lady", "sir", "doctor", "dr", "lieutenant", "lt", "colonel", "major", "sergeant",
    "governor", "senator", "master", "jedi", "darth", "emperor", "prince", "princess", "king",
    "queen", "high", "chief", "agent", "officer", "of",
}

SYSTEM_RESOLVE = """You decide whether two entries extracted from Star Wars D6 RPG books refer to the
same entity. They are the SAME when one name is a shorter form, a title or rank variant, or a
spelling or OCR variant of the other, and the hints do not clearly describe two different things.
They are DIFFERENT when they are distinct entities that merely share a word, or when one is a
group, unit or species and the other a single member of it. Aliases were extracted automatically
and may contain errors; rely mostly on names and hints. Answer with JSON only."""

SYSTEM_NAMING = """You choose the canonical name of one Star Wars D6 RPG entity from the name variants
extracted from sourcebooks (OCR text, so variants may contain recognition errors). Answer with JSON only."""


def parse_json_dict(text: str) -> dict:
    value = extract_json_from_text(text)
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def name_key(s: str) -> str:
    k = norm_text(s)
    k = re.sub(r"[^\w\s]", " ", k)
    k = re.sub(r"^(the|a|an)\s+", "", k.strip())
    return re.sub(r"\s+", " ", k).strip()


def core_tokens(key: str) -> set[str]:
    return {t for t in key.split() if t not in TITLES and len(t) >= 3}


def core_key(key: str) -> str:
    """Name key without titles/ranks: 'moff kentor sarne' -> 'kentor sarne'."""
    return " ".join(t for t in key.split() if t not in TITLES)


class UnionFind:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def is_candidate(a: str, b: str) -> bool:
    """Fuzzy pair worth an LLM check (exact key matches are already merged)."""
    ta, tb = core_tokens(a), core_tokens(b)
    if ta and tb and (ta <= tb or tb <= ta) and max(len(t) for t in ta & tb or {""}) >= 4:
        return True
    sm = difflib.SequenceMatcher(None, a, b)
    return sm.real_quick_ratio() >= 0.85 and sm.quick_ratio() >= 0.85 and sm.ratio() >= 0.85


def describe_cluster(nodes: list[dict]) -> dict:
    names = sorted({n["name"] for n in nodes})
    return {
        "names": names[:8],
        "aliases": sorted({a for n in nodes for a in n["aliases"]} - set(names))[:8],
        "hints": list(dict.fromkeys(n["hint"] for n in nodes if n["hint"]))[:4],
    }


def naming_prompt(cat: str, variants: list[tuple[str, int]], aliases: list[str], hints: list[str]) -> str:
    return f"""Category: {CATEGORIES[cat]["label"]}
Name variants (with number of extractions): {json.dumps(variants, ensure_ascii=False)}
Extracted aliases (noisy): {json.dumps(aliases, ensure_ascii=False)}
Hints: {json.dumps(hints, ensure_ascii=False)}

Choose "canonical_name": the most complete proper name by which this entity is known
(e.g. given name + family name), WITHOUT a rank or title that changes during the story,
unless the title is the only name known.
The variants come from OCR and may contain recognition errors: correct the OCR errors you
detect in the canonical name. Frequency is not reliable for spelling.
"aliases": the other variants that truly designate this entity (titles, short forms, nicknames,
spelling variants). Drop generic words (species, job, rank alone) and names of other entities.
Return {{"canonical_name": "...", "aliases": [...], "reason": "..."}}"""


def arbitrate_name(llm: LLM | None, cat: str, ns: list[dict], fallback: str, cache_dir: Path,
                   args) -> tuple[str, list[str] | None]:
    """Canonical name (and cleaned aliases) chosen by the LLM; fallback = most frequent name."""
    counts = Counter(n["name"] for n in ns)
    raw_aliases = sorted({a for n in ns for a in n["aliases"]} - set(counts))
    if llm is None or args.no_llm_naming or len(counts) == 1:
        return fallback, None
    hints = list(dict.fromkeys(n["hint"] for n in ns if n["hint"]))[:6]
    prompt = naming_prompt(cat, counts.most_common(), raw_aliases[:20], hints)
    path = cache_dir / f"{short_hash(prompt + str(args.judge_effort))}.json"
    res = llm_call(llm, path, args.force, SYSTEM_NAMING, prompt, parse=parse_json_dict, thinking=True,
                   effort=args.judge_effort, max_tokens=8000, temperature=0.3) or {}
    name = str(res.get("canonical_name") or "").strip()
    # Guard against invention: each word must be a variant word or an OCR-close spelling of one.
    known = {t for v in [*counts, *raw_aliases] for t in name_key(v).split()}
    close = lambda t: t in known or any(difflib.SequenceMatcher(None, t, k).ratio() >= 0.75 for k in known)
    if not name or not all(close(t) for t in name_key(name).split()):
        return fallback, None
    variants = set(counts) | set(raw_aliases)
    aliases = [a for a in (res.get("aliases") or []) if a in variants and name_key(a) != name_key(name)]
    # Extraction names are always kept as aliases; only noisy extracted aliases can be dropped.
    aliases = sorted(set(aliases) | {v for v in counts if name_key(v) != name_key(name)})
    return name, aliases


def phase2(args, llm: LLM | None, root: Path, out: Path, sources: list[dict] | None = None) -> None:
    sources = sources if sources is not None else select_sources(root, args.index, args.limit)
    for s in sources:
        for cat in parse_categories(args.categories):
            phase2_doc(args, llm, out, s, cat)


def phase2_doc(args, llm: LLM | None, out: Path, s: dict, cat: str) -> None:
    """Deduplicate the entities of ONE document and ONE category."""
    sid = s["id"]
    target = out / "phase2" / cat / f"{sid}.json"
    if target.exists() and not args.force:
        return
    nodes = [{"name": e["name"], "aliases": e.get("aliases") or [], "hint": e.get("hint") or "",
              "profile": e.get("profile") or [], "role": e.get("role") or []}
             for e in json_load(out / "phase1" / cat / f"{sid}.json", []) or []]
    if not nodes:
        json_dump(target, [])
        return

    # 1) Names only (with and without titles); extracted aliases are noisy and only
    #    propose pairs to the LLM judge below.
    uf = UnionFind(len(nodes))
    owner: dict[str, int] = {}
    for i, n in enumerate(nodes):
        nk = name_key(n["name"])
        for k in {nk, core_key(nk) if len(core_key(nk)) >= 5 else ""} - {""}:
            if k in owner:
                uf.union(owner[k], i)
            else:
                owner[k] = i

    def clusters() -> dict[int, list[int]]:
        g: dict[int, list[int]] = defaultdict(list)
        for i in range(len(nodes)):
            g[uf.find(i)].append(i)
        return g

    # 2) Fuzzy candidates (blocked on shared alias keys, tokens, token prefixes/suffixes).
    groups = clusters()
    rep_keys = {r: {name_key(nodes[i]["name"]) for i in idx} for r, idx in groups.items()}
    alias_keys = {r: {k for i in idx for a in [nodes[i]["name"], *nodes[i]["aliases"]]
                      if len(core_key(k := name_key(a))) >= 4} for r, idx in groups.items()}
    blocks: dict[str, set[int]] = defaultdict(set)
    for r in groups:
        for k in alias_keys[r]:
            blocks["a:" + k].add(r)
        for k in rep_keys[r]:
            for t in core_tokens(k):
                blocks["t:" + t].add(r)
                if len(t) >= 4:
                    blocks["p:" + t[:3]].add(r)
                    blocks["s:" + t[-3:]].add(r)
    seen_pairs: set[tuple[int, int]] = set()
    pairs = []
    for members in blocks.values():
        if len(members) < 2 or len(members) > args.max_block:
            continue
        ms = sorted(members)
        for x in range(len(ms)):
            for y in range(x + 1, len(ms)):
                pr = (ms[x], ms[y])
                if pr in seen_pairs:
                    continue
                seen_pairs.add(pr)
                if alias_keys[pr[0]] & alias_keys[pr[1]] or any(
                        is_candidate(a, b) for a in rep_keys[pr[0]] for b in rep_keys[pr[1]]):
                    pairs.append(pr)
    log(f"[phase2] {cat} {sid}: {len(nodes)} entries -> {len(groups)} exact clusters, {len(pairs)} fuzzy pairs")

    decisions = []
    if llm and pairs:
        def judge(pair):
            da = describe_cluster([nodes[i] for i in groups[pair[0]]])
            db = describe_cluster([nodes[i] for i in groups[pair[1]]])
            prompt = (f"Category: {CATEGORIES[cat]['label']}\n\nEntity A: {json.dumps(da, ensure_ascii=False)}\n"
                      f"Entity B: {json.dumps(db, ensure_ascii=False)}\n\n"
                      'Are A and B the same entity? Return {"same": true|false, "reason": "..."}')
            path = out / "phase2" / "_calls" / cat / sid / f"pair_{short_hash(prompt + str(args.judge_effort))}.json"
            res = llm_call(llm, path, args.force, SYSTEM_RESOLVE, prompt, parse=parse_json_dict,
                           thinking=True, effort=args.judge_effort, max_tokens=8000, temperature=0.2) or {}
            return pair, res, da, db

        for pair, res, da, db in run_parallel(pairs, judge, args.workers):
            same = res.get("same") is True
            decisions.append({"a": da["names"], "b": db["names"], "same": same, "reason": res.get("reason", "")})
            if same:
                uf.union(*pair)

    # 3) Canonical names and merged entries.
    def name_group(idx):
        ns = [nodes[i] for i in idx]
        ranked = sorted(Counter(n["name"] for n in ns).most_common(), key=lambda kv: (-kv[1], -len(kv[0])))
        canonical, aliases = arbitrate_name(llm, cat, ns, ranked[0][0], out / "phase2" / "_calls" / cat / sid, args)
        if aliases is None:
            aliases = sorted({n["name"] for n in ns} | {a for n in ns for a in n["aliases"]})
            aliases = [a for a in aliases if name_key(a) != name_key(canonical)]
        return idx, canonical, aliases

    merged = []
    for idx, canonical, aliases in run_parallel_ordered(list(clusters().values()), name_group, args.workers):
        ns = [nodes[i] for i in idx]
        prof = sorted({p for n in ns for p in n["profile"]})
        role = sorted({p for n in ns for p in n["role"]} - set(prof))
        merged.append({
            "id": str(uuid.uuid5(ENTITY_NAMESPACE, f"{cat}:{sid}:{slug(canonical)}")),
            "source_id": sid,
            "category": cat,
            "name": canonical,
            "aliases": aliases,
            "hints": list(dict.fromkeys(n["hint"] for n in ns if n["hint"]))[:6],
            "profile": prof,
            "role": role,
        })
    merged.sort(key=lambda e: (-len(e["profile"]), -len(e["role"]), name_key(e["name"])))
    json_dump(out / "phase2" / cat / f"{sid}.decisions.json", decisions)
    json_dump(target, merged)
    log(f"  -> {cat} {sid}: {len(merged)} entities ({sum(d['same'] for d in decisions)} LLM merges)")


# ---------------------------------------------------------------------------
# Phase 3: one Markdown document per entity
# ---------------------------------------------------------------------------

SYSTEM_WRITER = """You write reference documents for a Star Wars D6 tabletop RPG campaign knowledge base,
from sourcebook pages (OCR Markdown). Use only the supplied text, never outside knowledge.
Never invent or complete missing information. Write in English, in Markdown."""


def entity_pages(entity: dict, src: dict, root: Path, after: int) -> list[tuple[int, str, str]]:
    """All pages of the entity in this book: (page, kind, text). "entry" = its own entry
    (profile page, and the `after` following pages where an entry often continues);
    "appears" = a scene or event where it acts or something new is learned about it."""
    pages_dir = (root / src["markdown"]).parent / "pages"
    num = lambda f: int(PAGE_REF.search(f).group(1))
    kinds: dict[int, str] = {}
    for f in entity["profile"]:
        for n in range(num(f), num(f) + after + 1):
            kinds[n] = "entry"
    for f in entity["role"]:
        kinds.setdefault(num(f), "appears")
    out = []
    for n in sorted(kinds):
        path = pages_dir / page_file(n)
        if path.exists():
            out.append((n, kinds[n], clean_page_text(path.read_text(encoding="utf-8", errors="replace"))))
    return out


def fit_pages(pages: list[tuple[int, str, str]], max_chars: int) -> tuple[list, list[int]]:
    """Every page if they fit in max_chars; otherwise entry pages first, then scene pages in
    page order, until the budget is used. Returns (kept pages in page order, dropped pages)."""
    if sum(len(t) for _, _, t in pages) <= max_chars:
        return pages, []
    kept, size = [], 0
    for pg in sorted(pages, key=lambda x: (x[1] != "entry", x[0])):
        if size + len(pg[2]) <= max_chars:
            kept.append(pg)
            size += len(pg[2])
    kept_n = {n for n, _, _ in kept}
    return sorted(kept), [n for n, _, _ in pages if n not in kept_n]


PAGE_KINDS = {"entry": "the target's own entry", "appears": "the target appears"}


def document_prompt(entity: dict, book: str, pages: list[tuple[int, str, str]]) -> str:
    c = CATEGORIES[entity["category"]]
    body = "\n\n".join(
        f"===== page {n} ({PAGE_KINDS[kind]}) =====\n{text}"
        for n, kind, text in pages)
    return f"""TARGET: {entity['name']} ({c["label"]})
Also called: {', '.join(entity['aliases']) or '-'}
Extraction hints: {' / '.join(entity['hints']) or '-'}

Below are ALL the pages of "{book}" where the target was found: pages holding its own entry,
and pages of scenes or events where it appears.

{body}

=====
Write ONE well-written, well-formatted Markdown document about the target that gathers ALL the
information these pages give about it, organized as best suits the content.
Suggested structure for {c["label"]} (adapt freely, skip empty parts): {c["outline"]}.
- Keep every number exactly: attributes, skills, dice codes (3D+2), difficulties, costs, ranges,
  damage, technical data. Put them together in a sheet-like section (list or table).
- From scene pages, keep what they reveal about the target (actions and consequences, decisions,
  relationships, revelations, secrets, changes of status) without retelling the whole scenes.
- Cite pages inline, like (p. 49).
- Ignore what is only about other entities or about a namesake.
- When the pages disagree, keep both versions and say so.
- Do not add a title line: the document title is added automatically.
If these pages say nothing about the target, output exactly: {NOTHING}"""


def is_nothing(text: str | None) -> bool:
    return not text or (NOTHING in text[:200] and len(text.strip()) < len(NOTHING) + 40)


def build_document(llm: LLM, entity: dict, src: dict, root: Path, out: Path, args) -> tuple[str | None, dict]:
    """One call: every page of the entity in this book -> one Markdown document."""
    pages = entity_pages(entity, src, root, args.profile_next)
    pages, dropped = fit_pages(pages, args.max_doc_chars)
    if dropped:
        log(f"  [warning] {entity['name'][:40]}: {len(dropped)} scene page(s) over --max-doc-chars "
            f"left out: {dropped[:20]}")
    stats = {"entry_pages": sum(k == "entry" for _, k, _ in pages),
             "scene_pages": sum(k == "appears" for _, k, _ in pages), "dropped": dropped}
    if not pages:
        return None, stats
    prompt = document_prompt(entity, src["name"], pages)
    kw = dict(thinking=not args.no_think_phase3, effort=args.phase3_effort,
              max_tokens=args.doc_max_tokens, temperature=0.6)
    path = (out / "phase3" / "_calls" / entity["category"] / entity["source_id"]
            / f"{entity['id']}_{short_hash(prompt + str(kw['effort']))}.json")
    text = llm_call(llm, path, args.force, SYSTEM_WRITER, prompt, accept_truncated=True, **kw)
    if text is None:
        raise RuntimeError("document generation failed")
    return (None if is_nothing(text) else text), stats


def yaml_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)  # JSON strings are valid YAML scalars


def doc_path(out: Path, entity: dict) -> Path:
    stem = (slug(entity["name"])[:60] or "entity") + "--" + entity["id"][:8]
    return out / "documents" / entity["source_id"] / entity["category"] / f"{stem}.md"


def render_document(entity: dict, src: dict, body: str, stats: dict, model: str, effort: str) -> str:
    dropped = ("\n> Pages not given to the model (context budget): "
               + ", ".join(map(str, stats["dropped"])) + "\n") if stats.get("dropped") else ""
    pages = lambda fs: "[" + ", ".join(str(int(PAGE_REF.search(f).group(1))) for f in fs) + "]"
    meta = [
        "---",
        f"id: {entity['id']}",
        f"name: {yaml_str(entity['name'])}",
        f"category: {entity['category']}",
        "aliases: [" + ", ".join(yaml_str(a) for a in entity["aliases"]) + "]",
        f"source_id: {src['id']}",
        f"source_name: {yaml_str(src['name'])}",
        f"collection: {src.get('collection') or ''}",
        f"profile_pages: {pages(entity['profile'])}",
        f"role_pages: {pages(entity['role'])}",
        f"pipeline: {PIPELINE}",
        f"model: {model}",
        f"effort: {effort}",
        "reviewed: false",
        "---",
    ]
    return "\n".join(meta) + f"\n\n# {entity['name']}\n{dropped}\n{body.strip()}\n"


def phase3(args, llm: LLM, root: Path, out: Path, sources: list[dict] | None = None) -> None:
    sources = sources if sources is not None else select_sources(root, args.index, args.limit)
    for src in sources:
        sid = src["id"]
        for cat in parse_categories(args.categories):
            entities = json_load(out / "phase2" / cat / f"{sid}.json")
            if entities is None:
                log(f"[phase3] {cat} {sid}: run phase2 first, skipped")
                continue
            if args.only:
                needle = norm_text(args.only)
                entities = [e for e in entities if needle in norm_text(" ".join([e["name"], *e["aliases"]]))]
            if args.entity_limit:
                entities = entities[: args.entity_limit]
            todo = [e for e in entities if args.force or not (doc_path(out, e).exists() or
                    (out / "phase3" / "_empty" / f"{e['id']}").exists())]
            if not todo:
                continue
            log(f"[phase3] {cat} {sid}: {len(todo)}/{len(entities)} entities ({src['name'][:50]})")

            def work(ie):
                i, e = ie
                t0 = time.time()
                try:
                    body, st = build_document(llm, e, src, root, out, args)
                except RuntimeError as exc:
                    log(f"  [error] ({i}/{len(todo)}) {e['name'][:40]}: {str(exc)[:160]}")
                    with (out / "phase3" / "_errors.jsonl").open("a", encoding="utf-8") as f:
                        f.write(json.dumps({"time": time.strftime("%Y-%m-%d %H:%M:%S"), "category": cat,
                                            "source_id": sid, "id": e["id"], "name": e["name"],
                                            "error": str(exc)[:500]}, ensure_ascii=False) + "\n")
                    return  # no document: the next run retries this entity
                if body is None:
                    marker = out / "phase3" / "_empty" / e["id"]
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    marker.write_text(e["name"], encoding="utf-8")
                    log(f"  ({i}/{len(todo)}) {e['name'][:40]:<40} nothing found in its pages")
                    return
                path = doc_path(out, e)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(render_document(e, src, body, st, llm.model, args.phase3_effort), encoding="utf-8")
                log(f"  ({i}/{len(todo)}) {e['name'][:40]:<40} entry={st['entry_pages']:>2}p "
                    f"scenes={st['scene_pages']:>3}p {time.time() - t0:6.1f}s")

            run_parallel(list(enumerate(todo, 1)), work, args.workers)
        write_document_index(out, src)


def write_document_index(out: Path, src: dict) -> int:
    """documents/<source-id>/index.json: every entity document of the source (input of phase 4)."""
    rows = []
    for cat in CATEGORIES:
        for e in json_load(out / "phase2" / cat / f"{src['id']}.json", []) or []:
            path = doc_path(out, e)
            if path.exists():
                rows.append({"id": e["id"], "name": e["name"], "category": cat, "aliases": e["aliases"],
                             "file": path.relative_to(out / "documents" / src["id"]).as_posix(),
                             "profile_pages": e["profile"], "role_pages": e["role"]})
    if rows:
        json_dump(out / "documents" / src["id"] / "index.json", {
            "pipeline": PIPELINE, "source_id": src["id"], "source_name": src["name"],
            "source_metadata": source_role_metadata(src), "entities": rows})
    return len(rows)


def write_outputs(args, out: Path, sources: list[dict]) -> None:
    counts = {src["id"]: write_document_index(out, src) for src in sources}
    json_dump(out / "run_manifest.json", {
        "pipeline": PIPELINE,
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "model": args.model_used,
        "base_url": args.base_url,
        "judge_effort": args.judge_effort,
        "phase3_effort": args.phase3_effort,
        "categories": list(CATEGORIES),
        "documents": {k: v for k, v in counts.items() if v},
    })
    log(f"[output] {sum(counts.values())} entity documents in {sum(1 for v in counts.values() if v)} sources")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("phase", choices=["phase1", "phase2", "phase3", "all", "outputs"])
    p.add_argument("--root", default=".", help="Corpus root containing index.json and src-*/.")
    p.add_argument("--index", default="index.json")
    p.add_argument("--output", default="_extraction")
    p.add_argument("--limit", type=int, default=0, help="First N documents (0 = all).")
    p.add_argument("--sources", default=None, help="Comma list of source ids (overrides --limit).")
    p.add_argument("--collections", default=None,
                   help="Comma list of collections, processed in this order (e.g. darkstryder,addons).")
    p.add_argument("--categories", default=None, help=f"Comma list among {','.join(CATEGORIES)}.")
    p.add_argument("--base-url", default="http://127.0.0.1:8080/v1", help="OpenAI-compatible API.")
    p.add_argument("--model", default=None, help="Model id; default: first model listed by the server.")
    p.add_argument("--timeout", type=int, default=1800, help="Seconds per request.")
    p.add_argument("--attempts", type=int, default=3, help="Max attempts per LLM call.")
    p.add_argument("--server-wait", type=int, default=3600,
                   help="Seconds to wait for an unreachable server (not counted as attempts).")
    p.add_argument("--restarts", type=int, default=3, help="Automatic restarts after an unexpected crash.")
    p.add_argument("--log-file", default="run.log", help="Log file inside --output ('' to disable).")
    p.add_argument("--workers", type=int, default=1, help="Parallel requests (the server's slot count).")
    p.add_argument("--force", action="store_true", help="Ignore cached LLM results.")
    # phase 1
    p.add_argument("--chunk-size", type=int, default=10, help="Phase 1: pages per chunk.")
    p.add_argument("--overlap", type=int, default=2, help="Phase 1: overlapping pages between chunks.")
    p.add_argument("--min-page-chars", type=int, default=150,
                   help="Phase 1: skip pages with less text (only in documents longer than --min-doc-pages).")
    p.add_argument("--min-doc-pages", type=int, default=3,
                   help="Phase 1: documents with this many pages or fewer are never filtered, and are packed.")
    p.add_argument("--enum-max-tokens", type=int, default=6000,
                   help="Phase 1: output cap (a real answer is ~1-2k; reaching it means a loop).")
    p.add_argument("--temperature", type=float, default=0.6, help="Phase 1 sampling temperature.")
    p.add_argument("--frequency-penalty", type=float, default=0.1, help="Phase 1 anti-loop penalty.")
    p.add_argument("--presence-penalty", type=float, default=0.0, help="Phase 1 anti-loop penalty.")
    # phase 2
    p.add_argument("--no-llm-merge", action="store_true", help="Phase 2: keep fuzzy pairs apart, no LLM.")
    p.add_argument("--no-llm-naming", action="store_true", help="Phase 2: canonical name = most frequent.")
    p.add_argument("--max-block", type=int, default=400, help="Phase 2: skip candidate blocks bigger than this.")
    p.add_argument("--judge-effort", default="medium", help="Phase 2: reasoning effort (low|medium|high).")
    # phase 3
    p.add_argument("--phase3-effort", default="medium", help="Phase 3: reasoning effort (low|medium|high).")
    p.add_argument("--no-think-phase3", action="store_true", help="Phase 3 without thinking (debug).")
    p.add_argument("--entity-limit", type=int, default=0, help="Phase 3: only the first N entities per category.")
    p.add_argument("--only", default=None, help="Phase 3: only entities whose name/alias contains this.")
    p.add_argument("--profile-next", type=int, default=1,
                   help="Phase 3: pages added after each profile page (entries continuing on the next page).")
    p.add_argument("--max-doc-chars", type=int, default=300000,
                   help="Phase 3: page text given in one call (~80k tokens); beyond it, scene pages are left out.")
    p.add_argument("--doc-max-tokens", type=int, default=40000, help="Phase 3: output cap (thinking included).")
    return p


def main() -> None:
    args = make_parser().parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    root = Path(args.root).resolve()
    if args.sources:
        SOURCE_IDS.extend(x.strip() for x in args.sources.split(",") if x.strip())
    if args.collections:
        COLLECTIONS.extend(x.strip() for x in args.collections.split(",") if x.strip())
    out = (root / args.output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    global LOG_FILE
    LOG_FILE = out / args.log_file if args.log_file else None
    log(f"[run] {' '.join(sys.argv[1:])}")

    # Every LLM result is cached on disk: a restart resumes where the crash happened.
    for run in range(args.restarts + 1):
        try:
            run_phases(args, root, out)
            log("[run] finished")
            return
        except (KeyboardInterrupt, ServerDown):
            raise
        except Exception as e:  # noqa: BLE001
            import traceback
            log(f"[run] crashed ({run + 1}/{args.restarts + 1}): {e!r}\n{traceback.format_exc()}")
            if run < args.restarts:
                time.sleep(60)
    raise SystemExit("[run] too many crashes; re-run the same command to resume")


def run_phases(args, root: Path, out: Path) -> None:
    llm = LLM(args.base_url, args.model, args.timeout, args.attempts, args.workers, args.server_wait)
    args.model_used = llm.model
    judge = None if args.no_llm_merge else llm
    if args.phase == "all":
        # One unit (a long document, or a pack of short ones) end to end before the next.
        sources = select_sources(root, args.index, args.limit)
        units = work_units(root, sources, args)
        for k, unit in enumerate(units, 1):
            unit_sources = [src for src, _ in unit]
            log(f"[all] unit {k}/{len(units)}: " + (unit_sources[0]["name"][:60] if len(unit) == 1
                                                    else f"{len(unit)} short documents"))
            phase1(args, llm, root, out, unit_sources)
            phase2(args, judge, root, out, unit_sources)
            phase3(args, llm, root, out, unit_sources)
        write_outputs(args, out, sources)
        return
    if args.phase == "phase1":
        phase1(args, llm, root, out)
    elif args.phase == "phase2":
        phase2(args, judge, root, out)
    elif args.phase == "phase3":
        phase3(args, llm, root, out)
    else:
        write_outputs(args, out, select_sources(root, args.index, args.limit))


if __name__ == "__main__":
    main()
