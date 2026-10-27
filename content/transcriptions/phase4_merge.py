#!/usr/bin/env python3
"""SQLite retrieval helpers and CLI entry point for the staged phase-4 pipeline.

Use --help for explicit CPU/inference stages. No combined automatic run.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path
from typing import Any

import extract_entities as extraction


SCHEMA_VERSION = "phase4/2.0"
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
HEADING = re.compile(r"(?m)^(#{2,6})\s+(.+?)\s*$")
PAGE_CITE = re.compile(r"\(p\.\s*(\d+)\)", re.I)
CORPUS_NAMESPACE = uuid.UUID("374fcdf3-6ea6-4a37-985e-59de670a738a")


def log(message: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}"
    print(line, flush=True)
    if extraction.LOG_FILE:
        extraction.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with extraction.LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def plain(value: str) -> str:
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value.casefold().replace("’", "'")).strip()


def parse_scalar(value: str) -> Any:
    value = value.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value.strip('"\'')


def name_grams(value: str) -> set[str]:
    normalized = " ".join(WORD.findall(plain(value)))[:160]
    return {normalized[i:i + 3] for i in range(max(0, len(normalized) - 2))}


def read_document(path: Path, documents_root: Path) -> dict[str, Any]:
    path.resolve().relative_to(documents_root.resolve())
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig").replace("\r\n", "\n")
    meta: dict[str, Any] = {}
    body = text
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end >= 0:
            for line in text[4:end].splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    meta[key.strip()] = parse_scalar(value)
            body = text[end + 5:].lstrip()
    relative = path.relative_to(documents_root).as_posix()
    doc_id = str(meta.get("id") or "")
    if not doc_id or not re.fullmatch(r"[A-Za-z0-9_-]+", doc_id):
        raise ValueError(f"Missing/unsafe phase-3 document ID: {relative}")
    source_id = str(meta.get("source_id") or "")
    if not re.fullmatch(r"src-[0-9a-f]+", source_id) or source_id != path.parents[1].name:
        raise ValueError(f"Source ID/path mismatch: {relative}")
    aliases = meta.get("aliases") if isinstance(meta.get("aliases"), list) else []
    return {
        "doc_id": doc_id,
        "entity_id": doc_id,
        "source_id": source_id,
        "source_name": str(meta.get("source_name") or ""),
        "category": str(meta.get("category") or path.parent.name),
        "name": str(meta.get("name") or path.stem.rsplit("--", 1)[0]),
        "aliases": [str(x) for x in aliases if str(x).strip()],
        "profile_pages": meta.get("profile_pages") if isinstance(meta.get("profile_pages"), list) else [],
        "role_pages": meta.get("role_pages") if isinstance(meta.get("role_pages"), list) else [],
        "path": relative,
        "body": body,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def split_passages(doc: dict[str, Any], max_chars: int = 6000) -> list[dict[str, Any]]:
    text = doc["body"]
    matches = list(HEADING.finditer(text))
    sections: list[tuple[str, str]] = []
    if not matches:
        sections.append((doc["name"], text))
    else:
        prefix = text[:matches[0].start()].strip()
        if prefix:
            sections.append((doc["name"], prefix))
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            sections.append((match.group(2).strip(), text[match.end():end].strip()))
    out = []
    sequence = 0
    for heading, section in sections:
        paragraphs = [x.strip() for x in re.split(r"\n\s*\n", section) if x.strip()] or [section]
        chunks, current, size = [], [], 0
        for paragraph in paragraphs:
            if len(paragraph) > max_chars:
                if current:
                    chunks.append("\n\n".join(current))
                    current, size = [], 0
                chunks.extend(paragraph[offset:offset + max_chars]
                              for offset in range(0, len(paragraph), max_chars))
                continue
            if current and size + len(paragraph) + 2 > max_chars:
                chunks.append("\n\n".join(current))
                current, size = [], 0
            current.append(paragraph)
            size += len(paragraph) + 2
        if current:
            chunks.append("\n\n".join(current))
        for chunk in chunks:
            if not chunk.strip():
                continue
            sequence += 1
            key = f"{doc['doc_id']}:{sequence}:{heading}:{chunk}"
            out.append({
                "passage_id": hashlib.sha1(key.encode("utf-8")).hexdigest()[:20],
                "sequence": sequence,
                "heading": heading,
                "pages": sorted({int(x) for x in PAGE_CITE.findall(chunk)}),
                "text": chunk,
            })
    return out


def hash_features(text: str, limit: int = 384) -> dict[int, float]:
    words = [plain(x) for x in WORD.findall(text)]
    terms = words + [f"{a}\x1f{b}" for a, b in zip(words, words[1:])]
    counts: dict[int, float] = defaultdict(float)
    for term in terms:
        if not term:
            continue
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=4).digest()
        feature = int.from_bytes(digest, "little") & 0xFFFFF
        counts[feature] += 1.0
    strongest = sorted(counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    weighted = {key: 1.0 + math.log(value) for key, value in strongest}
    length = math.sqrt(sum(value * value for value in weighted.values())) or 1.0
    return {key: value / length for key, value in weighted.items()}


SCHEMA = """
PRAGMA journal_mode=DELETE;
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE documents(
  doc_id TEXT PRIMARY KEY, entity_id TEXT NOT NULL, source_id TEXT NOT NULL,
  source_name TEXT NOT NULL, category TEXT NOT NULL, name TEXT NOT NULL,
  name_norm TEXT NOT NULL, aliases_json TEXT NOT NULL, aliases_norm TEXT NOT NULL,
  profile_pages_json TEXT NOT NULL, role_pages_json TEXT NOT NULL,
  path TEXT NOT NULL UNIQUE, body TEXT NOT NULL, sha256 TEXT NOT NULL
);
CREATE TABLE passages(
  passage_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL REFERENCES documents(doc_id),
  sequence INTEGER NOT NULL, heading TEXT NOT NULL, pages_json TEXT NOT NULL, text TEXT NOT NULL
);
CREATE TABLE vector_terms(
  doc_id TEXT NOT NULL REFERENCES documents(doc_id), feature INTEGER NOT NULL, weight REAL NOT NULL,
  PRIMARY KEY(doc_id, feature)
);
CREATE INDEX vector_terms_feature ON vector_terms(feature);
CREATE INDEX documents_name_norm ON documents(name_norm);
CREATE TABLE name_forms(form TEXT NOT NULL, doc_id TEXT NOT NULL, PRIMARY KEY(form,doc_id));
CREATE TABLE name_grams(gram TEXT NOT NULL,doc_id TEXT NOT NULL,PRIMARY KEY(gram,doc_id));
CREATE TABLE sources(source_id TEXT PRIMARY KEY, metadata_json TEXT NOT NULL);
CREATE INDEX passages_doc ON passages(doc_id, sequence);
CREATE VIRTUAL TABLE documents_fts USING fts5(doc_id UNINDEXED, name, aliases, body, tokenize='unicode61 remove_diacritics 2');
CREATE VIRTUAL TABLE passages_fts USING fts5(passage_id UNINDEXED, doc_id UNINDEXED, heading, text, tokenize='unicode61 remove_diacritics 2');
"""


def build_index(root: Path, out: Path) -> dict[str, int]:
    documents_root = out / "documents"
    paths = sorted(documents_root.glob("src-*/*/*.md"))
    phase4 = out / "phase4"
    phase4.mkdir(parents=True, exist_ok=True)
    target = phase4 / "index.sqlite"
    temporary = phase4 / "index.sqlite.tmp"
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    docs_count = passages_count = 0
    try:
        db.executescript(SCHEMA)
        from phase4_pipeline import source_registry
        registry = source_registry(root)
        db.executemany("INSERT INTO sources VALUES(?,?)",
                       ((key, json.dumps(value, ensure_ascii=False)) for key, value in registry.items()))
        db.execute("INSERT INTO meta(key,value) VALUES('schema_version',?)", (SCHEMA_VERSION,))
        for number, path in enumerate(paths, 1):
            doc = read_document(path, documents_root)
            if doc["source_id"] not in registry:
                raise ValueError(f"Source absent from published registry: {doc['source_id']}")
            aliases_json = json.dumps(doc["aliases"], ensure_ascii=False)
            aliases_norm = " | ".join(plain(x) for x in doc["aliases"])
            db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc["doc_id"], doc["entity_id"], doc["source_id"], doc["source_name"],
                 doc["category"], doc["name"], plain(doc["name"]), aliases_json, aliases_norm,
                 json.dumps(doc["profile_pages"]), json.dumps(doc["role_pages"]), doc["path"],
                 doc["body"], doc["sha256"]),
            )
            db.execute("INSERT INTO documents_fts VALUES(?,?,?,?)",
                       (doc["doc_id"], doc["name"], " ".join(doc["aliases"]), doc["body"]))
            db.executemany("INSERT OR IGNORE INTO name_forms VALUES(?,?)",
                           ((plain(name), doc["doc_id"]) for name in [doc["name"], *doc["aliases"]]))
            db.executemany("INSERT OR IGNORE INTO name_grams VALUES(?,?)",
                           ((gram, doc["doc_id"]) for name in [doc["name"], *doc["aliases"]]
                            for gram in name_grams(name)))
            searchable = "\n".join([doc["name"], *doc["aliases"], doc["body"]])
            db.executemany("INSERT INTO vector_terms VALUES(?,?,?)",
                           ((doc["doc_id"], key, value) for key, value in hash_features(searchable).items()))
            for passage in split_passages(doc):
                db.execute("INSERT INTO passages VALUES(?,?,?,?,?,?)",
                           (passage["passage_id"], doc["doc_id"], passage["sequence"], passage["heading"],
                            json.dumps(passage["pages"]), passage["text"]))
                db.execute("INSERT INTO passages_fts VALUES(?,?,?,?)",
                           (passage["passage_id"], doc["doc_id"], passage["heading"], passage["text"]))
                passages_count += 1
            docs_count += 1
            if number % 250 == 0:
                db.commit()
                log(f"[index] {number}/{len(paths)} documents")
        db.execute("INSERT INTO meta(key,value) VALUES('documents',?)", (str(docs_count),))
        db.execute("INSERT INTO meta(key,value) VALUES('passages',?)", (str(passages_count),))
        digest = hashlib.sha256()
        for row in db.execute("SELECT doc_id,sha256 FROM documents ORDER BY doc_id"):
            digest.update(json.dumps(list(row)).encode("utf-8"))
        digest.update(json.dumps(registry, sort_keys=True, ensure_ascii=False).encode("utf-8"))
        db.execute("INSERT INTO meta(key,value) VALUES('snapshot',?)", (digest.hexdigest(),))
        db.commit()
    finally:
        db.close()
    temporary.replace(target)
    log(f"[index] ready: {docs_count} documents, {passages_count} passages")
    return {"documents": docs_count, "passages": passages_count}


def fts_query(query: str) -> str:
    tokens = [plain(x).replace('"', '') for x in WORD.findall(query) if len(plain(x)) > 1]
    return " OR ".join(f'"{token}"' for token in tokens[:24])


class SearchIndex:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
        self.db.row_factory = sqlite3.Row
        if self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0] != SCHEMA_VERSION:
            self.db.close()
            raise ValueError("Old index: rebuild with phase4_merge.py index")

    def close(self) -> None:
        self.db.close()

    def document(self, doc_id: str) -> dict[str, Any] | None:
        row = self.db.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def documents(self) -> list[dict[str, Any]]:
        return [dict(x) for x in self.db.execute("SELECT * FROM documents ORDER BY source_id,category,name_norm")]

    def passages(self, ids: list[str]) -> list[dict[str, Any]]:
        if not ids:
            return []
        marks = ",".join("?" for _ in ids)
        return [dict(x) for x in self.db.execute(
            f"SELECT * FROM passages WHERE passage_id IN ({marks}) ORDER BY doc_id,sequence", ids)]

    def first_passages(self, doc_id: str, limit: int = 2) -> list[dict[str, Any]]:
        return [dict(x) for x in self.db.execute(
            "SELECT * FROM passages WHERE doc_id=? ORDER BY sequence LIMIT ?", (doc_id, limit))]

    def search(self, query: str, limit: int, exclude: set[str]) -> list[dict[str, Any]]:
        scores: dict[str, float] = defaultdict(float)
        hits: dict[str, list[str]] = defaultdict(list)
        normalized = plain(query)
        # Exact entity names and aliases are the strongest signal.
        for row in self.db.execute(
                "SELECT doc_id FROM name_forms WHERE form=?", (normalized,)):
            scores[row["doc_id"]] += 12.0
        match = fts_query(query)
        if match:
            try:
                for rank_number, row in enumerate(self.db.execute(
                        "SELECT doc_id,bm25(documents_fts,0,5,3,1) rank FROM documents_fts "
                        "WHERE documents_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit * 4)), 1):
                    scores[row["doc_id"]] += 4.0 / rank_number
                for rank_number, row in enumerate(self.db.execute(
                        "SELECT passage_id,doc_id,bm25(passages_fts,0,0,2,1) rank FROM passages_fts "
                        "WHERE passages_fts MATCH ? ORDER BY rank LIMIT ?", (match, limit * 8)), 1):
                    scores[row["doc_id"]] += 3.0 / rank_number
                    if len(hits[row["doc_id"]]) < 3:
                        hits[row["doc_id"]].append(row["passage_id"])
            except sqlite3.OperationalError:
                pass
        # Deterministic cosine similarity over hashed word and bigram vectors.
        vector = hash_features(query, limit=96)
        if vector:
            features = list(vector)
            marks = ",".join("?" for _ in features)
            case = "CASE feature " + " ".join("WHEN ? THEN ?" for _ in features) + " ELSE 0 END"
            params: list[Any] = []
            for feature in features:
                params.extend((feature, vector[feature]))
            params.extend(features)
            sql = (f"SELECT doc_id,SUM(weight*({case})) cosine FROM vector_terms "
                   f"WHERE feature IN ({marks}) GROUP BY doc_id ORDER BY cosine DESC LIMIT ?")
            params.append(limit * 4)
            for row in self.db.execute(sql, params):
                scores[row["doc_id"]] += 5.0 * float(row["cosine"] or 0.0)
        ranked = sorted(((score, doc_id) for doc_id, score in scores.items()
                         if doc_id not in exclude), reverse=True)[:limit]
        out = []
        for score, doc_id in ranked:
            doc = self.document(doc_id)
            if not doc:
                continue
            passage_ids = hits.get(doc_id) or [x["passage_id"] for x in self.first_passages(doc_id)]
            passage_rows = self.passages(passage_ids[:3])
            out.append({
                "doc_id": doc_id, "name": doc["name"], "category": doc["category"],
                "source_id": doc["source_id"], "aliases": json.loads(doc["aliases_json"]),
                "score": round(score, 4),
                "passages": [{"passage_id": p["passage_id"], "heading": p["heading"],
                              "pages": json.loads(p["pages_json"]), "text": p["text"][:1600]}
                             for p in passage_rows],
            })
        return out



def main() -> None:
    if '--unified' in __import__('sys').argv:
        import runpy
        import sys
        sys.argv.remove('--unified')
        runpy.run_path(str(Path(__file__).resolve().parents[2] / 'tools' / 'sw-kbase' / 'cli.py'), run_name='__main__')
        return
    from phase4_pipeline import main as pipeline_main
    pipeline_main()


if __name__ == "__main__":
    main()
