#!/usr/bin/env python3
"""
Quality automation for large recommendation datasets.

Reads CSV/JSON/JSONL, applies rule-based quality checks, and outputs
structured decisions: Keep / Remove / Needs more information / Needs editing.
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

DECISIONS = ["Keep", "Remove", "Needs more information", "Needs editing"]

DEFAULT_CONFIG = {
    "min_description_length": 40,
    "min_tags_count": 1,
    "required_fields": ["name", "full_address"],
    "remove_keywords": [
        "mcdonalds",
        "burger king",
        "hesburger",
        "kfc",
        "subway",
        "starbucks",
        "taco bell",
        "domino",
        "domino's",
        "pizza hut",
        "quick service",
    ],
    "profanity_keywords": [
        "fuck",
        "shit",
        "bitch",
        "cunt",
        "asshole",
    ],
    "edit_if_missing": ["description", "tags"],
    "info_if_missing": ["name", "full_address"],
    "dedupe": True,
}


@dataclass
class Record:
    raw: Dict[str, str]
    name: str
    description: str
    address: str
    tags: str
    source: str
    key: str


def normalize_text(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def extract_field(data: Dict[str, str], keys: List[str]) -> str:
    for key in keys:
        if key in data and str(data[key]).strip():
            return str(data[key]).strip()
    return ""


def build_record(row: Dict[str, str]) -> Record:
    name = extract_field(
        row,
        [
            "name",
            "title",
            "restaurant_name",
            "venue_name",
            "place_name",
        ],
    )
    description = extract_field(row, ["description", "summary", "about", "notes", "why"])
    address = extract_field(row, ["full_address", "address", "location"]) 
    tags = extract_field(row, ["tags", "tag", "cuisine", "category", "categories"]) 
    source = extract_field(row, ["source"]) 

    key = normalize_text(f"{name}|{address}")
    return Record(raw=row, name=name, description=description, address=address, tags=tags, source=source, key=key)


def load_input(path: str) -> Iterable[Dict[str, str]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)

    if p.suffix.lower() == ".csv":
        with p.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                yield {k: (v or "") for k, v in row.items()}
        return

    if p.suffix.lower() in {".jsonl", ".ndjson"}:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)
        return

    if p.suffix.lower() == ".json":
        with p.open(encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    yield row
            return
        if isinstance(data, dict):
            rows = data.get("data") or data.get("items") or []
            for row in rows:
                if isinstance(row, dict):
                    yield row
            return

    raise ValueError("Unsupported input format. Use CSV, JSON, or JSONL.")


def save_output(path: str, rows: List[Dict[str, str]]) -> None:
    p = Path(path)
    if p.suffix.lower() == ".csv":
        fieldnames = sorted({k for row in rows for k in row.keys()})
        with p.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        return

    with p.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_config(path: Optional[str]) -> Dict:
    config = dict(DEFAULT_CONFIG)
    if not path:
        return config
    with open(path, encoding="utf-8") as f:
        override = json.load(f)
    if isinstance(override, dict):
        config.update(override)
    return config


def classify(record: Record, config: Dict, seen: set) -> Tuple[str, List[str], str, int]:
    reasons_remove = []
    reasons_info = []
    reasons_edit = []

    name_norm = normalize_text(record.name)
    desc_norm = normalize_text(record.description)
    tags_norm = normalize_text(record.tags)

    if config.get("dedupe") and record.key in seen and record.key:
        reasons_remove.append("duplicate_name_address")
    else:
        if record.key:
            seen.add(record.key)

    # Remove keywords (chains / not a fit)
    for kw in config.get("remove_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm and (kw_norm in name_norm or kw_norm in desc_norm or kw_norm in tags_norm):
            reasons_remove.append(f"keyword:{kw_norm}")
            break

    # Profanity
    for kw in config.get("profanity_keywords", []):
        kw_norm = normalize_text(kw)
        if kw_norm and (kw_norm in name_norm or kw_norm in desc_norm):
            reasons_remove.append(f"profanity:{kw_norm}")
            break

    # Missing required fields
    if not record.name:
        reasons_info.append("missing_name")
    if not record.address:
        reasons_info.append("missing_address")

    # Needs edit if description/tags missing or too short
    min_desc = int(config.get("min_description_length", 0))
    if record.description and len(record.description.strip()) < min_desc:
        reasons_edit.append("description_too_short")
    if not record.description:
        reasons_edit.append("missing_description")

    if record.tags:
        # Count tags by separators
        tag_count = len([t for t in re.split(r"[;,]", record.tags) if t.strip()])
    else:
        tag_count = 0
    if tag_count < int(config.get("min_tags_count", 0)):
        reasons_edit.append("missing_tags")

    # Decision
    if reasons_remove:
        decision = "Remove"
    elif reasons_info:
        decision = "Needs more information"
    elif reasons_edit:
        decision = "Needs editing"
    else:
        decision = "Keep"

    # Confidence (simple heuristic)
    if decision == "Remove":
        confidence = "High"
    elif decision == "Keep":
        confidence = "Medium"
    else:
        confidence = "Low"

    # Quality score (simple, transparent)
    score = 100
    score -= 25 * len(reasons_remove)
    score -= 15 * len(reasons_info)
    score -= 10 * len(reasons_edit)
    score = max(score, 0)

    reasons = reasons_remove + reasons_info + reasons_edit
    return decision, reasons, confidence, score


def emit_llm_batch(path: str, rows: List[Record]) -> None:
    p = Path(path)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            payload = {
                "name": r.name,
                "description": r.description,
                "address": r.address,
                "tags": r.tags,
                "source": r.source,
                "prompt": (
                    "Classify this recommendation into one of: Keep, Remove, "
                    "Needs more information, Needs editing. Provide a short reason."
                ),
            }
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Scale quality automation for recommendations.")
    parser.add_argument("--input", required=True, help="Input dataset (CSV/JSON/JSONL)")
    parser.add_argument("--output", required=True, help="Output CSV/JSONL")
    parser.add_argument("--config", default="", help="Optional JSON config file")
    parser.add_argument("--emit-llm-batch", default="", help="Write a JSONL file for LLM review")
    args = parser.parse_args()

    config = load_config(args.config)
    rows = []
    seen = set()
    records = []

    for row in load_input(args.input):
        record = build_record(row)
        decision, reasons, confidence, score = classify(record, config, seen)
        out = dict(record.raw)
        out.update(
            {
                "decision": decision,
                "reasons": ";".join(reasons),
                "confidence": confidence,
                "quality_score": str(score),
            }
        )
        rows.append(out)
        records.append(record)

    save_output(args.output, rows)

    if args.emit_llm_batch:
        emit_llm_batch(args.emit_llm_batch, records)

    print(f"Wrote {len(rows)} rows to {args.output}")
    if args.emit_llm_batch:
        print(f"Wrote LLM batch to {args.emit_llm_batch}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
