#!/usr/bin/env python3
"""
build_kbase.py

Local LLM extraction/consolidation pipeline for RPG campaign corpora transcribed to Markdown.

Designed for an OpenAI-compatible LM Studio server (default: http://localhost:1234/v1).

Pipeline
--------
1) Read index.json and select the first N complete documents.
2) Read per-page Markdown: <source-id>/pages/0001.md, ...
3) Build overlapping page chunks.
4) For every chunk:
   A. exhaustive entity/narrative/rule inventory
   B. coverage audit for missed entities
   C. relation + story-thread extraction
   D. skeptical validation pass
5) Store MENTIONS separately from canonical entity content.
6) Conservatively deduplicate mentions into canonical entity clusters.
7) Gather ALL source pages mentioning each canonical entity.
8) Re-run the LLM per canonical entity to reconstruct a rich structured JSON
   with provenance and future-use metadata.
9) Write one JSON file per entity type, ready for later DB population.

The script is intentionally independent from the future database schema.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Configuration / ontology
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "campaign-extract/2.0"

ENTITY_TYPES = [
    # world / lore
    "character", "faction", "organization", "species", "creature",
    "location", "planet", "moon", "star_system", "region", "settlement",
    "ship", "vehicle", "station", "droid",
    "item", "weapon", "armor", "artifact", "technology",
    "historical_event", "culture", "government",
    # narrative
    "campaign", "arc", "plot", "adventure", "episode", "scene", "event",
    "objective", "secret", "clue", "revelation", "consequence",
    "threat", "clock", "encounter",
    # rules
    "rule", "mechanic", "skill", "ability", "force_power", "stat_block",
    "random_table", "difficulty", "house_rule", "game_effect",
    # GM / inspiration
    "hook", "rumor", "complication", "npc_seed", "location_seed",
    "encounter_seed", "description", "gm_advice", "improvisation_seed",
]

RELATION_PREDICATES = [
    "member_of", "formerly_member_of", "commands", "serves", "serves_under",
    "works_for", "worked_for", "recruited_by", "assigned_by",
    "allied_with", "enemy_of", "rival_of",
    "friend_of", "trusts", "distrusts", "hates", "loves",
    "related_to", "parent_of", "sibling_of",
    "knows", "knows_secret",
    "located_in", "originates_from", "born_on",
    "owns", "uses", "carries", "created_by", "built_by", "controlled_by",
    "captured_by", "abducted_by", "sold_to", "owes_favor_to",
    "caused_by", "affected_by",
    "appears_in", "part_of", "involves", "requires", "leads_to",
    "reveals", "foreshadows", "blocks", "triggers", "targets",
    "protects", "attacks", "pursues", "supports", "opposes",
    "uses_rule", "has_stat_block", "variant_of", "inspired_by",
]

USAGE_TAGS = [
    # Directly useful to reconstruct the DarkStryder campaign itself
    "darkstryder_lore",
    "darkstryder_scenario",
    "darkstryder_gm_material",
    # External material that should normally not be merged into DS canon
    "external_lore",
    "external_scenario",
    # Mechanical/reference resources
    "rules",
    "reference_catalog",
    # Reusable drawer / inspiration material
    "inspiration",
    # Credits, advertisements, indices with no useful game content, etc.
    "non_game_content",
]

RELEVANCE_VALUES = ["direct", "reusable", "external", "none"]

# Stable namespace for deterministic mention IDs.
MENTION_NAMESPACE = uuid.UUID("f66383fa-8c3f-4fb4-9891-8baf61a26f64")
ENTITY_NAMESPACE = uuid.UUID("ee4533b6-8a8c-4d80-b66f-7401bc9a41fd")


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def norm_text(value: str | None) -> str:
    if not value:
        return ""
    s = unicodedata.normalize("NFKD", str(value))
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = s.replace("’", "'").replace("`", "'")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def slug(value: str | None) -> str:
    return norm_text(value).replace(" ", "_")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def stable_mention_id(key: str) -> str:
    return "mnt:" + str(uuid.uuid5(MENTION_NAMESPACE, key))


def stable_entity_id(key: str) -> str:
    return "ent:" + str(uuid.uuid5(ENTITY_NAMESPACE, key))


def json_dump(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def json_load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def jsonl_write(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def sanitize_filename(s: str) -> str:
    s = slug(s) or "unknown"
    return s[:120]


def extract_json_from_text(text: str) -> Any:
    """Parse JSON even if the model wrapped it in a code fence or preamble."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try the widest JSON object/array region.
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        raise ValueError("No JSON object/array found in model output.")
    start = min(starts)

    # Prefer object if it starts first, otherwise array.
    opener = text[start]
    closer = "}" if opener == "{" else "]"
    end = text.rfind(closer)
    if end <= start:
        raise ValueError("Incomplete JSON in model output.")
    return json.loads(text[start:end + 1])


def chunks_of(items: list[Any], n: int) -> Iterable[list[Any]]:
    for i in range(0, len(items), n):
        yield items[i:i+n]


# ---------------------------------------------------------------------------
# LM Studio client
# ---------------------------------------------------------------------------

class LMStudioClient:
    def __init__(
        self,
        base_url: str,
        model: str | None,
        temperature: float = 0.1,
        max_tokens: int = 12000,
        timeout: int = 600,
        retries: int = 2,
        json_mode: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        self.json_mode = json_mode
        self.model = model or self._discover_model()
        print(f"[LM Studio] model={self.model} base={self.base_url}")

    def _request(self, method: str, url: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _discover_model(self) -> str:
        data = self._request("GET", self.base_url + "/models")
        models = data.get("data") or []
        if not models:
            raise RuntimeError("No model loaded in LM Studio.")
        return models[0]["id"]

    def chat(self, system: str, user: str, temperature: float | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_exc = None
        for attempt in range(self.retries + 1):
            try:
                data = self._request("POST", self.base_url + "/chat/completions", payload)
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_exc = e
                wait = 1.5 * (attempt + 1)
                print(f"[LM Studio] request failed ({e}); retry in {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
        raise RuntimeError(f"LM Studio request failed: {last_exc}")

    def chat_json(self, system: str, user: str) -> Any:
        last_raw = ""
        for attempt in range(self.retries + 1):
            raw = self.chat(system, user)
            last_raw = raw
            try:
                return extract_json_from_text(raw)
            except Exception as e:
                if attempt >= self.retries:
                    break
                print(f"[JSON] invalid output ({e}); retrying", file=sys.stderr)
                user = (
                    user
                    + "\n\nIMPORTANT RETRY: Your previous response was invalid JSON. "
                      "Return ONLY one syntactically valid JSON object, with no markdown or commentary."
                )
        raise ValueError(f"Could not parse JSON output. Last output:\n{last_raw[:4000]}")


# ---------------------------------------------------------------------------
# Source / page handling
# ---------------------------------------------------------------------------

@dataclass
class Page:
    number: int
    path: Path
    text: str


@dataclass
class PageChunk:
    chunk_id: str
    pages: list[Page]

    @property
    def start(self) -> int:
        return self.pages[0].number

    @property
    def end(self) -> int:
        return self.pages[-1].number

    def render(self) -> str:
        parts = []
        for p in self.pages:
            parts.append(f"\n===== PDF PAGE {p.number} =====\n{p.text}")
        return "\n".join(parts)


def source_role_metadata(source: dict[str, Any]) -> dict[str, Any]:
    """
    Deterministic coarse-grained source role from the corpus collections.

    This does NOT decide what individual passages contain. The LLM also classifies
    every chunk using USAGE_TAGS.
    """
    collections = set(source.get("collections") or [])
    if source.get("collection"):
        collections.add(source["collection"])

    family = "unknown"
    default_tags: list[str] = []
    darkstryder_scope = "external"

    if "darkstryder" in collections:
        family = "darkstryder_core"
        default_tags = ["darkstryder_lore", "darkstryder_scenario", "darkstryder_gm_material"]
        darkstryder_scope = "core"
    elif "addons" in collections:
        family = "darkstryder_addon"
        default_tags = ["darkstryder_lore", "darkstryder_scenario", "inspiration"]
        darkstryder_scope = "extension"
    elif "guides-rules" in collections:
        family = "rules_reference"
        default_tags = ["rules", "external_lore", "reference_catalog", "inspiration"]
        darkstryder_scope = "resource"
    elif "sourcebooks" in collections:
        family = "external_sourcebook"
        default_tags = ["external_lore", "external_scenario", "inspiration"]
        darkstryder_scope = "resource"

    return {
        "source_family": family,
        "darkstryder_scope": darkstryder_scope,
        "default_usage_tags": default_tags,
        "collections": sorted(collections),
        "format": source.get("format"),
    }


def load_pages(root: Path, source: dict[str, Any]) -> list[Page]:
    md_rel = source.get("markdown")
    if not md_rel:
        return []

    book_path = root / md_rel
    source_dir = book_path.parent
    pages_dir = source_dir / "pages"

    page_paths = []
    if pages_dir.exists():
        for p in pages_dir.glob("*.md"):
            if re.fullmatch(r"\d+", p.stem):
                page_paths.append(p)
        page_paths.sort(key=lambda p: int(p.stem))

    if page_paths:
        return [
            Page(number=int(p.stem), path=p, text=p.read_text(encoding="utf-8", errors="replace"))
            for p in page_paths
        ]

    # Fallback for native documents / documents without per-page split.
    if book_path.exists():
        return [Page(number=1, path=book_path, text=book_path.read_text(encoding="utf-8", errors="replace"))]

    return []


def build_page_chunks(pages: list[Page], chunk_size: int, overlap: int) -> list[PageChunk]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be >=0 and < chunk_size")

    step = chunk_size - overlap
    out = []
    for i in range(0, len(pages), step):
        group = pages[i:i+chunk_size]
        if not group:
            break
        out.append(PageChunk(
            chunk_id=f"p{group[0].number:04d}-p{group[-1].number:04d}",
            pages=group,
        ))
        if i + chunk_size >= len(pages):
            break
    return out


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

SYSTEM_EXTRACTOR = """You are a high-recall information extraction engine for tabletop RPG material.
Use only the supplied source text. Never use outside knowledge.
Do not summarize away minor entities. Preserve uncertainty and OCR variants.
Return valid JSON only."""

def inventory_prompt(source: dict, role_meta: dict, chunk: PageChunk) -> str:
    return f"""
TASK: EXHAUSTIVE inventory of all independently referenceable entities in this RPG source chunk.

Priority = RECALL. Read the full text twice before answering.
Include minor one-line mentions, historical references, GM-only material, rules, stat blocks,
locations, named equipment/technology, plot threads, scenes, secrets, clues and reusable hooks.
Do not omit something because it may occur in another overlapping chunk.

Allowed entity types:
{", ".join(ENTITY_TYPES)}

Also classify THIS CHUNK for future use with zero or more tags from:
{", ".join(USAGE_TAGS)}

Meaning:
- darkstryder_lore: facts/worldbuilding that belong directly to DarkStryder
- darkstryder_scenario: actual DarkStryder adventure/plot/scene material
- darkstryder_gm_material: DarkStryder secrets, GM notes, hooks, hidden information
- external_lore: Star Wars / other lore useful as reference but not directly DarkStryder
- external_scenario: external adventure material, potentially reusable
- rules: game mechanics, stat rules, skills, tables, game effects
- reference_catalog: lists/catalogs/reference data such as NPC lists, planets, equipment, systems
- inspiration: reusable material that should remain a drawer/resource, not DS canon
- non_game_content: credits, adverts, irrelevant publication material

Index-derived source metadata (a hint, not a substitute for reading the text):
{json.dumps(role_meta, ensure_ascii=False)}

For each entity:
- canonical_name: best normalized name supported by the text
- source_names: exact spellings/forms seen in this chunk
- aliases
- canonical_key_candidate: "<type>:<ascii_snake_case_name>"
- page_start/page_end using ONLY the explicit PDF PAGE markers
- completeness: complete | partial_start | partial_end | fragment
- context: <= 300 characters explaining what this mention is, grounded in the source
- identity_confidence: 0..1
- normalization_notes: OCR/spelling uncertainty only

Do not invent names for unnamed people/groups. You may extract them only when independently useful,
using a descriptive source-grounded name such as "unnamed resistance prisoner".

Return:
{{
  "usage": {{
    "tags": [],
    "darkstryder_relevance": "direct|reusable|external|none",
    "notes": ""
  }},
  "entities": [
    {{
      "name": "",
      "type": "",
      "source_names": [],
      "aliases": [],
      "canonical_key_candidate": "",
      "page_start": null,
      "page_end": null,
      "completeness": "complete",
      "context": "",
      "identity_confidence": 1.0,
      "normalization_notes": []
    }}
  ]
}}

SOURCE:
document_id={source.get("id")}
document_title={source.get("name")}
chunk_id={chunk.chunk_id}

{chunk.render()}
""".strip()


def audit_prompt(source: dict, chunk: PageChunk, entities: list[dict]) -> str:
    compact = [
        {"name": e.get("name"), "type": e.get("type"),
         "canonical_key_candidate": e.get("canonical_key_candidate")}
        for e in entities
    ]
    return f"""
AUDIT an entity inventory for omissions.

Read the ENTIRE source chunk again. Find every independently referenceable entity that is missing
from the supplied inventory. Priority = RECALL. Check especially:
- minor characters and unnamed-but-specific individuals
- factions/organizations
- planets/systems/locations
- ships/vehicles/stations/droids
- species/creatures
- artifacts/technology/equipment
- historical events
- adventures/scenes/plots/objectives/secrets/clues/threats
- rules/abilities/mechanics/stat blocks/tables
- GM hooks/rumors/complications/inspiration material

Do not return entities already represented in the inventory, even under an alias.
Do not use outside knowledge.

CURRENT INVENTORY:
{json.dumps(compact, ensure_ascii=False)}

Return JSON only:
{{
  "missing_entities": [
    {{
      "name": "",
      "type": "",
      "source_names": [],
      "aliases": [],
      "canonical_key_candidate": "",
      "page_start": null,
      "page_end": null,
      "completeness": "complete|partial_start|partial_end|fragment",
      "context": "",
      "identity_confidence": 1.0,
      "normalization_notes": []
    }}
  ]
}}

SOURCE:
{chunk.render()}
""".strip()


def relations_prompt(source: dict, chunk: PageChunk, entities: list[dict]) -> str:
    compact = [
        {"name": e.get("name"), "type": e.get("type"),
         "canonical_key_candidate": e.get("canonical_key_candidate")}
        for e in entities
    ]
    return f"""
Extract ALL explicit or strongly text-supported relationships and narrative threads from this RPG chunk.

Entity inventory:
{json.dumps(compact, ensure_ascii=False)}

Allowed relation predicates:
{", ".join(RELATION_PREDICATES)}

Relations may connect ANY types: character->faction, faction->planet, clue->secret,
scene->adventure, artifact->location, rule->stat_block, etc.

Do not invent relationships. Use canonical_key_candidate from the inventory whenever possible.
If an important referenced entity is missing from the inventory, report it in missing_entities
instead of dropping the relation.

Also extract coherent STORY THREADS that cross individual facts: hidden loyalties, revenge arcs,
missions, conspiracies, investigations, escalation, intended future events, scenario dependencies.
These are not prose summaries of the whole chunk: only actual reusable narrative threads.

Return JSON only:
{{
  "relations": [
    {{
      "source": "",
      "predicate": "",
      "target": "",
      "qualifiers": {{}},
      "confidence": 1.0,
      "page_start": null,
      "page_end": null
    }}
  ],
  "story_threads": [
    {{
      "name": "",
      "kind": "plot|objective|secret|threat|hook|scenario_thread",
      "involved_entities": [],
      "description": "",
      "page_start": null,
      "page_end": null,
      "visibility": "gm|player|unknown",
      "confidence": 1.0
    }}
  ],
  "missing_entities": []
}}

SOURCE:
{chunk.render()}
""".strip()


def validation_prompt(chunk: PageChunk, entities: list[dict], relations: list[dict], story_threads: list[dict]) -> str:
    compact_entities = [
        {"name": e.get("name"), "type": e.get("type"),
         "canonical_key_candidate": e.get("canonical_key_candidate")}
        for e in entities
    ]
    return f"""
You are the skeptical final auditor for an RPG extraction.

Compare the source against the extracted entities, relations and story threads.
Return ONLY genuinely missing items or clear extraction errors.
Be exhaustive but conservative: do not create outside-lore facts.

ENTITIES:
{json.dumps(compact_entities, ensure_ascii=False)}

RELATIONS:
{json.dumps(relations, ensure_ascii=False)}

STORY THREADS:
{json.dumps(story_threads, ensure_ascii=False)}

Return JSON only:
{{
  "missing_entities": [],
  "missing_relations": [],
  "missing_story_threads": [],
  "errors": [
    {{
      "kind": "wrong_identity|wrong_type|unsupported_relation|other",
      "item": "",
      "reason": ""
    }}
  ]
}}

SOURCE:
{chunk.render()}
""".strip()


SYSTEM_RESOLVER = """You resolve identity between RPG entity mentions.
Be conservative. Merge only when they clearly refer to the same in-universe entity.
OCR/spelling/title variants may be the same entity. Similar roles or similar names are not enough.
Return valid JSON only."""

def dedup_pair_prompt(a: dict, b: dict, skeptical: bool = False) -> str:
    extra = (
        "Act as a skeptical reviewer. Prefer 'uncertain' over a false merge."
        if skeptical else
        "Decide whether these two mentions denote the same entity."
    )
    return f"""
{extra}

Mention A:
{json.dumps(a, ensure_ascii=False)}

Mention B:
{json.dumps(b, ensure_ascii=False)}

Return JSON only:
{{
  "decision": "same_entity|different_entity|uncertain",
  "confidence": 0.0,
  "preferred_name": null,
  "reason_code": "exact|alias|ocr_variant|title_variant|context_match|conflict|insufficient"
}}
""".strip()


SYSTEM_ENTITY = """You reconstruct one canonical RPG knowledge-base entity from source evidence.
Use every supplied source, preserve contradictions instead of silently choosing, and cite provenance
with source_id + page numbers in every substantial fact. Never use outside knowledge.
Return valid JSON only."""

def evidence_extraction_prompt(entity: dict, evidence_text: str) -> str:
    return f"""
TARGET ENTITY:
canonical_id={entity["canonical_id"]}
type={entity["type"]}
name={entity["canonical_name"]}
known aliases={json.dumps(entity.get("aliases", []), ensure_ascii=False)}

Extract EVERY fact in the evidence specifically relevant to this entity.
Do not write the final profile yet. Preserve source-specific variants and contradictions.

Separate future usage:
- darkstryder_lore
- darkstryder_scenario
- darkstryder_gm_material
- external_lore
- external_scenario
- rules
- reference_catalog
- inspiration

Return JSON only:
{{
  "names": [],
  "facts": [
    {{"key": "", "value": null, "usage_tags": [], "source_refs": [], "confidence": 1.0}}
  ],
  "descriptions": [
    {{"text": "", "usage_tags": [], "source_refs": []}}
  ],
  "relations": [
    {{"predicate": "", "target_name": "", "target_type": null, "source_refs": [], "confidence": 1.0}}
  ],
  "narrative": {{
    "roles": [],
    "secrets": [],
    "objectives": [],
    "hooks": [],
    "events": [],
    "clues": [],
    "threats": []
  }},
  "rules": [],
  "stat_blocks": [],
  "conflicts_or_variants": []
}}

EVIDENCE:
{evidence_text}
""".strip()


def final_entity_prompt(entity: dict, extracted_evidence: list[dict]) -> str:
    return f"""
Build the FINAL canonical JSON profile for this RPG entity.

Canonical identity:
{json.dumps(entity, ensure_ascii=False)}

All extracted evidence packages:
{json.dumps(extracted_evidence, ensure_ascii=False)}

Requirements:
- Keep DarkStryder-direct material separate from external lore/inspiration/rules.
- Do NOT turn external lore into DarkStryder canon.
- Merge compatible facts, but keep contradictions/source variants explicit.
- Preserve all useful RPG stats and game rules.
- Relationships point to names/types; IDs may be resolved later.
- Every substantial fact, narrative item, rule or stat block must retain source_refs.
- This JSON will be used later to populate a database.

Return JSON only:
{{
  "canonical_id": "{entity["canonical_id"]}",
  "type": "{entity["type"]}",
  "name": "",
  "aliases": [],
  "usage": {{
    "darkstryder_lore": false,
    "darkstryder_scenario": false,
    "darkstryder_gm_material": false,
    "external_lore": false,
    "external_scenario": false,
    "rules": false,
    "reference_catalog": false,
    "inspiration": false
  }},
  "summary": "",
  "description": "",
  "facts": [],
  "relationships": [],
  "narrative": {{
    "roles": [],
    "secrets": [],
    "objectives": [],
    "hooks": [],
    "events": [],
    "clues": [],
    "threats": []
  }},
  "rules": [],
  "stat_blocks": [],
  "conflicts_or_variants": [],
  "mentions": {json.dumps(entity.get("mention_ids", []), ensure_ascii=False)},
  "provenance": []
}}
""".strip()


# ---------------------------------------------------------------------------
# Chunk extraction
# ---------------------------------------------------------------------------

def validate_usage_tags(tags: Any) -> list[str]:
    if not isinstance(tags, list):
        return []
    return [t for t in tags if t in USAGE_TAGS]


def normalize_entity_record(e: dict, source: dict, chunk: PageChunk, usage: dict, occurrence: int) -> dict:
    etype = e.get("type") if e.get("type") in ENTITY_TYPES else "description"
    name = (e.get("name") or "").strip() or "unnamed"
    ckey = e.get("canonical_key_candidate") or f"{etype}:{slug(name)}"
    if ":" not in ckey:
        ckey = f"{etype}:{slug(ckey)}"

    pstart = e.get("page_start")
    pend = e.get("page_end")
    if not isinstance(pstart, int):
        pstart = chunk.start
    if not isinstance(pend, int):
        pend = pstart

    mention_key = (
        f"{source['id']}|{chunk.chunk_id}|{etype}|{ckey}|{pstart}|{occurrence}"
    )
    mention_id = stable_mention_id(mention_key)

    return {
        "mention_id": mention_id,
        "mention_key": mention_key,
        "source_id": source["id"],
        "source_name": source.get("name"),
        "chunk_id": chunk.chunk_id,
        "type": etype,
        "name": name,
        "source_names": list(dict.fromkeys(e.get("source_names") or [name])),
        "aliases": list(dict.fromkeys(e.get("aliases") or [])),
        "canonical_key_candidate": ckey,
        "page_start": pstart,
        "page_end": pend,
        "completeness": e.get("completeness") or "fragment",
        "context": e.get("context") or "",
        "identity_confidence": e.get("identity_confidence", 1.0),
        "normalization_notes": e.get("normalization_notes") or [],
        "usage_tags": validate_usage_tags(usage.get("tags") or []),
        "darkstryder_relevance": usage.get("darkstryder_relevance", "none"),
    }


def merge_local_entities(entities: list[dict]) -> list[dict]:
    """Within ONE chunk only: remove duplicate inventory/audit mentions by key/name+type."""
    out = []
    seen = set()
    for e in entities:
        key = (
            e.get("type"),
            norm_text(e.get("canonical_key_candidate") or e.get("name")),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def cache_call_json(
    client: LMStudioClient,
    path: Path,
    system: str,
    prompt: str,
    force: bool,
) -> Any:
    if path.exists() and not force:
        cached = json_load(path)
        if cached is not None:
            return cached
    result = client.chat_json(system, prompt)
    json_dump(path, result)
    return result


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

class UnionFind:
    def __init__(self, ids: list[str]):
        self.parent = {x: x for x in ids}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # deterministic root
            if ra < rb:
                self.parent[rb] = ra
            else:
                self.parent[ra] = rb


def mention_name_forms(m: dict) -> set[str]:
    vals = [m.get("name"), m.get("canonical_key_candidate")]
    vals.extend(m.get("source_names") or [])
    vals.extend(m.get("aliases") or [])
    forms = set()
    for v in vals:
        nv = norm_text(v)
        if nv:
            # canonical keys include type prefix; keep last component too
            forms.add(nv)
            if ":" in str(v):
                forms.add(norm_text(str(v).split(":", 1)[1]))
    return forms


def dedup_candidates(mentions: list[dict], threshold: float = 0.88) -> list[tuple[dict, dict, float]]:
    by_type: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        by_type[m["type"]].append(m)

    pairs = []
    for etype, group in by_type.items():
        # Keep candidate generation reasonably bounded by blocking on first alphanumeric char
        blocks: dict[str, list[dict]] = defaultdict(list)
        for m in group:
            n = norm_text(m["name"])
            key = n[:1] if n else "_"
            blocks[key].append(m)

        for block in blocks.values():
            for i in range(len(block)):
                a = block[i]
                af = mention_name_forms(a)
                for j in range(i + 1, len(block)):
                    b = block[j]
                    bf = mention_name_forms(b)
                    if af & bf:
                        pairs.append((a, b, 1.0))
                        continue
                    score = difflib.SequenceMatcher(
                        None, norm_text(a["name"]), norm_text(b["name"])
                    ).ratio()
                    # Title variants often contain the same normalized surname/name
                    na, nb = norm_text(a["name"]), norm_text(b["name"])
                    contained = (
                        len(na) >= 5 and len(nb) >= 5 and
                        (na in nb or nb in na)
                    )
                    if score >= threshold or contained:
                        pairs.append((a, b, max(score, 0.9 if contained else score)))
    return pairs


def compact_for_resolver(m: dict) -> dict:
    return {
        "mention_id": m["mention_id"],
        "type": m["type"],
        "name": m["name"],
        "source_names": m.get("source_names", []),
        "aliases": m.get("aliases", []),
        "context": m.get("context", ""),
        "source": {
            "source_id": m["source_id"],
            "source_name": m.get("source_name"),
            "pages": [m.get("page_start"), m.get("page_end")],
        },
    }


def canonicalize_mentions(
    client: LMStudioClient,
    mentions: list[dict],
    outdir: Path,
    force: bool,
    dedup_threshold: float,
) -> tuple[list[dict], list[dict]]:
    uf = UnionFind([m["mention_id"] for m in mentions])

    # Exact same candidate key = merge immediately.
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for m in mentions:
        by_key[(m["type"], norm_text(m["canonical_key_candidate"]))].append(m)
    for group in by_key.values():
        for m in group[1:]:
            uf.union(group[0]["mention_id"], m["mention_id"])

    reviews = []
    pairs = dedup_candidates(mentions, threshold=dedup_threshold)
    pair_dir = outdir / "dedup_pairs"
    pair_dir.mkdir(parents=True, exist_ok=True)

    for idx, (a, b, similarity) in enumerate(pairs, 1):
        if uf.find(a["mention_id"]) == uf.find(b["mention_id"]):
            continue

        pair_key = sha256_text("|".join(sorted([a["mention_id"], b["mention_id"]])))[:20]
        p1 = pair_dir / f"{pair_key}.resolver.json"
        p2 = pair_dir / f"{pair_key}.skeptic.json"

        r1 = cache_call_json(
            client, p1, SYSTEM_RESOLVER,
            dedup_pair_prompt(compact_for_resolver(a), compact_for_resolver(b), skeptical=False),
            force,
        )
        r2 = cache_call_json(
            client, p2, SYSTEM_RESOLVER,
            dedup_pair_prompt(compact_for_resolver(a), compact_for_resolver(b), skeptical=True),
            force,
        )

        same = (
            r1.get("decision") == "same_entity"
            and r2.get("decision") == "same_entity"
            and float(r1.get("confidence", 0)) >= 0.80
            and float(r2.get("confidence", 0)) >= 0.80
        )
        if same:
            uf.union(a["mention_id"], b["mention_id"])

        reviews.append({
            "a": a["mention_id"],
            "b": b["mention_id"],
            "similarity": similarity,
            "resolver": r1,
            "skeptic": r2,
            "merged": same,
        })
        if idx % 25 == 0:
            print(f"[dedup] reviewed {idx}/{len(pairs)} candidate pairs")

    groups: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        groups[uf.find(m["mention_id"])].append(m)

    # Load persistent identity registry.
    registry_path = outdir / "identity_registry.json"
    registry = json_load(registry_path, default={"version": 1, "entities": {}})
    reg_entities: dict[str, dict] = registry.setdefault("entities", {})

    canonical = []
    for group in groups.values():
        # Choose preferred name from highest-confidence + complete mention, then longest.
        ranked = sorted(
            group,
            key=lambda m: (
                m.get("completeness") == "complete",
                float(m.get("identity_confidence", 0)),
                len(m.get("name", "")),
            ),
            reverse=True,
        )
        preferred = ranked[0]["name"]
        etype = ranked[0]["type"]

        aliases = set()
        candidate_keys = set()
        for m in group:
            aliases.add(m["name"])
            aliases.update(m.get("source_names") or [])
            aliases.update(m.get("aliases") or [])
            candidate_keys.add(m.get("canonical_key_candidate") or "")

        # Reuse registry ID if any known alias key matches.
        registry_hit = None
        normalized_aliases = {norm_text(x) for x in aliases if x}
        for ent_id, rec in reg_entities.items():
            if rec.get("type") != etype:
                continue
            known = {norm_text(rec.get("name"))}
            known.update(norm_text(x) for x in rec.get("aliases", []))
            if normalized_aliases & known:
                registry_hit = ent_id
                break

        if registry_hit:
            cid = registry_hit
        else:
            seed = f"{etype}:{slug(preferred)}"
            cid = stable_entity_id(seed)

        rec = {
            "canonical_id": cid,
            "type": etype,
            "canonical_name": preferred,
            "aliases": sorted(a for a in aliases if a and a != preferred),
            "candidate_keys": sorted(k for k in candidate_keys if k),
            "mention_ids": sorted(m["mention_id"] for m in group),
            "source_ids": sorted(set(m["source_id"] for m in group)),
            "usage_tags": sorted(set(t for m in group for t in m.get("usage_tags", []))),
        }
        canonical.append(rec)

        reg_entities[cid] = {
            "type": etype,
            "name": preferred,
            "aliases": rec["aliases"],
        }

    canonical.sort(key=lambda x: (x["type"], norm_text(x["canonical_name"])))
    json_dump(registry_path, registry)
    json_dump(outdir / "dedup_review.json", reviews)
    json_dump(outdir / "entity_clusters.json", canonical)
    return canonical, reviews


# ---------------------------------------------------------------------------
# Evidence gathering and final entity reconstruction
# ---------------------------------------------------------------------------

def build_page_lookup(root: Path, sources: list[dict]) -> dict[tuple[str, int], Page]:
    lookup = {}
    for s in sources:
        for p in load_pages(root, s):
            lookup[(s["id"], p.number)] = p
    return lookup


def source_lookup(sources: list[dict]) -> dict[str, dict]:
    return {s["id"]: s for s in sources}


def evidence_blocks_for_entity(
    entity: dict,
    mentions_by_id: dict[str, dict],
    pages: dict[tuple[str, int], Page],
    sources: dict[str, dict],
    padding: int,
) -> list[str]:
    wanted: set[tuple[str, int]] = set()
    usage_for_page: dict[tuple[str, int], set[str]] = defaultdict(set)

    for mid in entity["mention_ids"]:
        m = mentions_by_id[mid]
        start = int(m.get("page_start") or 1)
        end = int(m.get("page_end") or start)
        for pg in range(max(1, start-padding), end+padding+1):
            key = (m["source_id"], pg)
            if key in pages:
                wanted.add(key)
                usage_for_page[key].update(m.get("usage_tags", []))

    blocks = []
    for sid, pg in sorted(wanted, key=lambda x: (x[0], x[1])):
        page = pages[(sid, pg)]
        s = sources[sid]
        role = source_role_metadata(s)
        tags = sorted(set(role["default_usage_tags"]) | usage_for_page[(sid, pg)])
        blocks.append(
            f"===== SOURCE {sid} | {s.get('name')} | PAGE {pg} | "
            f"source_family={role['source_family']} | usage_tags={','.join(tags)} =====\n"
            f"{page.text}"
        )
    return blocks


def pack_text_blocks(blocks: list[str], max_chars: int) -> list[str]:
    packs = []
    cur = []
    size = 0
    for b in blocks:
        if cur and size + len(b) > max_chars:
            packs.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(b)
        size += len(b)
    if cur:
        packs.append("\n\n".join(cur))
    return packs


def reconstruct_entity(
    client: LMStudioClient,
    entity: dict,
    blocks: list[str],
    outdir: Path,
    force: bool,
    context_chars: int,
) -> dict:
    entity_dir = outdir / "entity_work" / sanitize_filename(entity["canonical_id"])
    entity_dir.mkdir(parents=True, exist_ok=True)

    packs = pack_text_blocks(blocks, context_chars)
    evidence_packages = []
    for i, pack in enumerate(packs, 1):
        ep = entity_dir / f"evidence_{i:03d}.json"
        result = cache_call_json(
            client,
            ep,
            SYSTEM_ENTITY,
            evidence_extraction_prompt(entity, pack),
            force,
        )
        evidence_packages.append(result)

    final_path = entity_dir / "final.json"
    final = cache_call_json(
        client,
        final_path,
        SYSTEM_ENTITY,
        final_entity_prompt(entity, evidence_packages),
        force,
    )

    # Enforce IDs/type if model drifted.
    final["canonical_id"] = entity["canonical_id"]
    final["type"] = entity["type"]
    final.setdefault("name", entity["canonical_name"])
    final.setdefault("mentions", entity["mention_ids"])
    return final


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(args: argparse.Namespace) -> None:
    root = Path(args.root).resolve()
    index_path = (root / args.index).resolve()
    outdir = (root / args.output).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    index = json.loads(index_path.read_text(encoding="utf-8"))
    all_sources = index.get("sources") or []

    # "first N docs" = first N complete sources with Markdown and >0 units.
    eligible = [
        s for s in all_sources
        if s.get("status") == "complete"
        and s.get("markdown")
        and int(s.get("units") or 0) > 0
    ]
    selected = eligible[:args.limit]

    print(f"[corpus] selected {len(selected)} documents (limit={args.limit})")
    for i, s in enumerate(selected, 1):
        print(f"  {i:02d}. {s['id']}  {s.get('name')}")

    client = LMStudioClient(
        base_url=args.base_url,
        model=args.model,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        retries=args.retries,
        json_mode=args.json_mode,
    )

    run_manifest = {
        "schema_version": SCHEMA_VERSION,
        "index": str(index_path),
        "limit": args.limit,
        "chunk_size": args.chunk_size,
        "overlap": args.overlap,
        "model": client.model,
        "base_url": args.base_url,
        "selected_sources": [s["id"] for s in selected],
    }
    json_dump(outdir / "run_manifest.json", run_manifest)

    source_meta_records = []
    all_mentions: list[dict] = []
    all_relations: list[dict] = []
    all_threads: list[dict] = []

    for doc_idx, source in enumerate(selected, 1):
        print(f"\n[{doc_idx}/{len(selected)}] {source.get('name')}")
        pages = load_pages(root, source)
        if not pages:
            print("  ! no pages found; skipping", file=sys.stderr)
            continue

        role_meta = source_role_metadata(source)
        chunks = build_page_chunks(pages, args.chunk_size, args.overlap)
        doc_usage = defaultdict(int)

        source_meta_records.append({
            "source_id": source["id"],
            "name": source.get("name"),
            "format": source.get("format"),
            "units": source.get("units"),
            "markdown": source.get("markdown"),
            **role_meta,
        })

        for chunk_idx, chunk in enumerate(chunks, 1):
            print(f"  chunk {chunk_idx:03d}/{len(chunks):03d} pages {chunk.start}-{chunk.end}")
            cdir = outdir / "chunks" / source["id"] / chunk.chunk_id
            cdir.mkdir(parents=True, exist_ok=True)

            inv = cache_call_json(
                client,
                cdir / "01_inventory.json",
                SYSTEM_EXTRACTOR,
                inventory_prompt(source, role_meta, chunk),
                args.force,
            )
            usage = inv.get("usage") or {}
            usage["tags"] = validate_usage_tags(usage.get("tags"))
            for t in usage["tags"]:
                doc_usage[t] += 1

            entities = inv.get("entities") or []

            audit = cache_call_json(
                client,
                cdir / "02_audit.json",
                SYSTEM_EXTRACTOR,
                audit_prompt(source, chunk, entities),
                args.force,
            )
            entities.extend(audit.get("missing_entities") or [])
            entities = merge_local_entities(entities)

            rel = cache_call_json(
                client,
                cdir / "03_relations.json",
                SYSTEM_EXTRACTOR,
                relations_prompt(source, chunk, entities),
                args.force,
            )
            # Relations can reveal missing entities.
            entities.extend(rel.get("missing_entities") or [])
            entities = merge_local_entities(entities)

            val = cache_call_json(
                client,
                cdir / "04_validation.json",
                SYSTEM_EXTRACTOR,
                validation_prompt(
                    chunk,
                    entities,
                    rel.get("relations") or [],
                    rel.get("story_threads") or [],
                ),
                args.force,
            )
            entities.extend(val.get("missing_entities") or [])
            entities = merge_local_entities(entities)

            relations = (rel.get("relations") or []) + (val.get("missing_relations") or [])
            threads = (rel.get("story_threads") or []) + (val.get("missing_story_threads") or [])

            normalized = []
            for occ, e in enumerate(entities):
                normalized.append(
                    normalize_entity_record(e, source, chunk, usage, occ)
                )
            all_mentions.extend(normalized)

            # Keep relation mentions with source provenance.
            for r in relations:
                rr = dict(r)
                rr["source_id"] = source["id"]
                rr["source_name"] = source.get("name")
                rr["chunk_id"] = chunk.chunk_id
                rr["usage_tags"] = usage["tags"]
                all_relations.append(rr)

            for st in threads:
                tt = dict(st)
                tt["source_id"] = source["id"]
                tt["source_name"] = source.get("name")
                tt["chunk_id"] = chunk.chunk_id
                tt["usage_tags"] = usage["tags"]
                all_threads.append(tt)

        # Aggregate observed chunk classification into source metadata.
        source_meta_records[-1]["observed_usage_counts"] = dict(doc_usage)
        source_meta_records[-1]["observed_usage_tags"] = sorted(doc_usage)

        # Incremental checkpoint after every document.
        jsonl_write(outdir / "mentions.jsonl", all_mentions)
        jsonl_write(outdir / "relation_mentions.jsonl", all_relations)
        jsonl_write(outdir / "story_threads.jsonl", all_threads)
        json_dump(outdir / "source_metadata.json", source_meta_records)

    print(f"\n[mentions] {len(all_mentions)} extracted mentions")
    print(f"[relations] {len(all_relations)} extracted relation mentions")
    print(f"[threads] {len(all_threads)} extracted story threads")

    # Canonical entity clustering.
    canonical, reviews = canonicalize_mentions(
        client,
        all_mentions,
        outdir,
        args.force,
        args.dedup_threshold,
    )
    print(f"[canonical] {len(canonical)} entity clusters")

    # Rebuild canonical entity content from all relevant page evidence.
    mention_by_id = {m["mention_id"]: m for m in all_mentions}
    page_lookup = build_page_lookup(root, selected)
    src_lookup = source_lookup(selected)

    finals = []
    by_type: dict[str, list[dict]] = defaultdict(list)

    for i, entity in enumerate(canonical, 1):
        print(
            f"[entity {i}/{len(canonical)}] "
            f"{entity['type']} :: {entity['canonical_name']} "
            f"({len(entity['mention_ids'])} mentions)"
        )
        blocks = evidence_blocks_for_entity(
            entity,
            mention_by_id,
            page_lookup,
            src_lookup,
            padding=args.evidence_padding,
        )
        if not blocks:
            # Should be rare; still preserve identity.
            final = {
                "canonical_id": entity["canonical_id"],
                "type": entity["type"],
                "name": entity["canonical_name"],
                "aliases": entity.get("aliases", []),
                "mentions": entity["mention_ids"],
                "provenance": [],
            }
        else:
            final = reconstruct_entity(
                client,
                entity,
                blocks,
                outdir,
                args.force,
                args.entity_context_chars,
            )
        finals.append(final)
        by_type[entity["type"]].append(final)

        # Incremental final checkpoint.
        if i % 10 == 0:
            json_dump(outdir / "entities_all.json", finals)

    json_dump(outdir / "entities_all.json", finals)

    types_dir = outdir / "entities_by_type"
    types_dir.mkdir(parents=True, exist_ok=True)
    for etype, rows in sorted(by_type.items()):
        json_dump(types_dir / f"{sanitize_filename(etype)}.json", rows)

    # A small DB-import-oriented catalog.
    catalog = [
        {
            "canonical_id": e.get("canonical_id"),
            "type": e.get("type"),
            "name": e.get("name"),
            "aliases": e.get("aliases", []),
            "usage": e.get("usage", {}),
            "mention_count": len(e.get("mentions", [])),
        }
        for e in finals
    ]
    json_dump(outdir / "entity_catalog.json", catalog)

    print("\nDONE")
    print(f"Output: {outdir}")
    print("Key files:")
    print("  source_metadata.json")
    print("  mentions.jsonl")
    print("  relation_mentions.jsonl")
    print("  story_threads.jsonl")
    print("  entity_clusters.json")
    print("  identity_registry.json")
    print("  entities_all.json")
    print("  entities_by_type/*.json")


def make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Extract, validate, deduplicate and reconstruct RPG KB entities with LM Studio."
    )
    p.add_argument("--root", default=".", help="Corpus root containing index.json and src-*/ directories.")
    p.add_argument("--index", default="index.json")
    p.add_argument("--output", default="_kbase_build")
    p.add_argument("--limit", type=int, default=15, help="Process first N complete indexed documents.")
    p.add_argument("--chunk-size", type=int, default=6, help="Pages per extraction chunk.")
    p.add_argument("--overlap", type=int, default=3, help="Overlapping pages between chunks.")
    p.add_argument("--base-url", default="http://localhost:1234/v1", help="LM Studio OpenAI-compatible API.")
    p.add_argument("--model", default=None, help="Loaded model id. If omitted, auto-detect first LM Studio model.")
    p.add_argument("--temperature", type=float, default=0.1)
    p.add_argument("--max-tokens", type=int, default=12000)
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--retries", type=int, default=2)
    p.add_argument("--json-mode", action="store_true",
                   help="Ask OpenAI-compatible server for json_object response format.")
    p.add_argument("--force", action="store_true",
                   help="Ignore cached stage outputs and rerun LLM calls.")
    p.add_argument("--dedup-threshold", type=float, default=0.88,
                   help="Name similarity threshold for sending a pair to the LLM identity resolver.")
    p.add_argument("--evidence-padding", type=int, default=1,
                   help="Include N pages before/after each mention when reconstructing an entity.")
    p.add_argument("--entity-context-chars", type=int, default=45000,
                   help="Max evidence characters per per-entity extraction call.")
    return p


if __name__ == "__main__":
    run(make_parser().parse_args())
