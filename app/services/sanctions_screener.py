"""
app/services/sanctions_screener.py
────────────────────────────────────
Production-grade sanctions screening with fuzzy Arabic name matching.

Why fuzzy matching matters for MENA:
  Arabic names have no single romanisation standard. "Mohammed Al-Rashidi"
  and "Muhammad Al Rashidi" are the same person. An exact-match screener
  misses obvious variants — a critical AML compliance failure.

Screening passes (in order):
  1. Exact match          — instant, 100% score
  2. Fuzzy match          — rapidfuzz token_set_ratio, handles reordering
  3. Token overlap        — fallback for partial names

Score thresholds:
  90-100 DEFINITIVE  → BLOCK_IMMEDIATELY
  75-89  STRONG      → ESCALATE_COMPLIANCE
  50-74  PROBABLE    → MANUAL_REVIEW
  40-49  POSSIBLE    → FLAG_FOR_MONITORING
  < 40   no match

Technology: rapidfuzz (Rust-backed) — already in pyproject.toml
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from rapidfuzz import fuzz, process

from app.core.logging import get_logger

logger = get_logger(__name__)

DATA_DIR    = Path(__file__).parent.parent / "data"
SDN_PATH    = DATA_DIR / "sanctions" / "ofac_sdn.json"
LEGACY_PATH = DATA_DIR / "sanctions_watchlist.json"

THRESHOLD_DEFINITIVE = 90
THRESHOLD_STRONG     = 75
THRESHOLD_PROBABLE   = 50
THRESHOLD_MINIMUM    = 40

# Arabic transliteration normalisation map
# Converts common variants to canonical forms before fuzzy matching.
ARABIC_MAP = {
    "mohammed": "muhammad", "mohammad": "muhammad", "mohamad": "muhammad",
    "mohamed" : "muhammad", "muhammed": "muhammad", "mahomed": "muhammad",
    "al-"     : "al ",      "el-"     : "al ",      "el "    : "al ",
    "abdallah": "abdullah", "abdollah": "abdullah",
    "hassan"  : "hasan",    "hussain" : "husayn",   "hussein": "husayn",
    "hossein" : "husayn",   "bin "    : "ibn ",
    "yusuf"   : "yusuf",    "yousef"  : "yusuf",    "youssef": "yusuf",
    "omar"    : "umar",     "omer"    : "umar",
    "osama"   : "usama",
}


@dataclass
class ScreeningMatch:
    entity_uid  : str
    matched_name: str
    primary_name: str
    score       : int
    match_type  : str   # exact | fuzzy | transliteration | token
    programs    : list[str] = field(default_factory=list)
    countries   : list[str] = field(default_factory=list)
    is_mena     : bool = False
    remarks     : str = ""

    @property
    def severity(self) -> str:
        if self.score >= THRESHOLD_DEFINITIVE: return "DEFINITIVE"
        if self.score >= THRESHOLD_STRONG:     return "STRONG"
        if self.score >= THRESHOLD_PROBABLE:   return "PROBABLE"
        return "POSSIBLE"

    @property
    def recommended_action(self) -> str:
        if self.score >= THRESHOLD_DEFINITIVE: return "BLOCK_IMMEDIATELY"
        if self.score >= THRESHOLD_STRONG:     return "ESCALATE_COMPLIANCE"
        if self.score >= THRESHOLD_PROBABLE:   return "MANUAL_REVIEW"
        return "FLAG_FOR_MONITORING"

    def to_dict(self) -> dict:
        return {
            "entity_uid"        : self.entity_uid,
            "matched_name"      : self.matched_name,
            "primary_name"      : self.primary_name,
            "score"             : self.score,
            "severity"          : self.severity,
            "match_type"        : self.match_type,
            "recommended_action": self.recommended_action,
            "programs"          : self.programs,
            "countries"         : self.countries,
            "is_mena_relevant"  : self.is_mena,
            "remarks"           : self.remarks[:200],
        }


@dataclass
class ScreeningResult:
    query_name    : str
    query_country : Optional[str]
    is_hit        : bool
    best_score    : int
    matches       : list[ScreeningMatch] = field(default_factory=list)
    screening_ms  : float = 0.0

    @property
    def top_match(self) -> Optional[ScreeningMatch]:
        return self.matches[0] if self.matches else None

    def to_dict(self) -> dict:
        return {
            "query_name"   : self.query_name,
            "query_country": self.query_country,
            "is_hit"       : self.is_hit,
            "best_score"   : self.best_score,
            "match_count"  : len(self.matches),
            "matches"      : [m.to_dict() for m in self.matches[:5]],
            "screening_ms" : round(self.screening_ms, 1),
        }


def normalise_name(name: str) -> str:
    """
    Normalise a name for consistent comparison:
      1. Unicode NFD decomposition (removes accents)
      2. Lowercase
      3. Remove punctuation (keep letters and spaces)
      4. Apply Arabic transliteration map
      5. Collapse whitespace
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFD", name)
    n = "".join(c for c in n if not unicodedata.combining(c))
    n = n.lower()
    n = re.sub(r"[^\w\s]", " ", n)
    for variant, canonical in ARABIC_MAP.items():
        n = n.replace(variant, canonical)
    return " ".join(n.split())


def get_tokens(name: str) -> set[str]:
    return set(normalise_name(name).split())


class SanctionsScreener:
    """
    Full-featured sanctions screener backed by the OFAC SDN list.

    Builds an in-memory name index at startup — maps every name variant
    (primary + all aliases) to its entity. rapidfuzz searches across
    all ~40,000 name variants in the full SDN list in under 100ms.

    Falls back to legacy 5-entry list if OFAC data not downloaded yet.
    """

    def __init__(self) -> None:
        self._entities  : list[dict] = []
        self._name_index: dict[str, dict] = {}
        self._raw_names : list[str] = []
        self._load_data()
        self._build_index()
        logger.info(
            f"SanctionsScreener ready | "
            f"entities={len(self._entities):,} | "
            f"name_variants={len(self._raw_names):,}"
        )

    def _load_data(self) -> None:
        if SDN_PATH.exists():
            with open(SDN_PATH, encoding="utf-8") as f:
                self._entities = json.load(f)
            logger.info(f"Loaded OFAC SDN | entities={len(self._entities):,}")
        elif LEGACY_PATH.exists():
            with open(LEGACY_PATH, encoding="utf-8") as f:
                raw = json.load(f)
            self._entities = [
                {
                    "uid"             : str(i),
                    "name"            : e["name"],
                    "entity_type"     : "Entity",
                    "programs"        : [e.get("reason", "UNKNOWN")],
                    "aliases"         : [{"name": a, "category": "strong"} for a in e.get("aliases", [])],
                    "all_names"       : [e["name"]] + e.get("aliases", []),
                    "countries"       : [e.get("country", "")],
                    "remarks"         : e.get("reason", ""),
                    "is_mena_relevant": True,
                }
                for i, e in enumerate(raw)
            ]
            logger.warning("Using legacy 5-entry watchlist — run load_ofac_data.py --sample")
        else:
            logger.error("No sanctions data — run: uv run python scripts/load_ofac_data.py --sample")

    def _build_index(self) -> None:
        for entity in self._entities:
            for name in entity.get("all_names", []):
                norm = normalise_name(name)
                if norm and len(norm) > 2:
                    self._name_index[norm] = entity
                    self._raw_names.append(norm)

    def screen(
        self,
        name     : str,
        country  : Optional[str] = None,
        threshold: int = THRESHOLD_MINIMUM,
        max_results: int = 5,
    ) -> ScreeningResult:
        """
        Screen a name against the full SDN list.

        Args:
            name       : counterparty or merchant name to screen
            country    : ISO 2-letter country (used for score boost)
            threshold  : minimum score to include (default 40)
            max_results: maximum matches to return

        Returns ScreeningResult with all matches above threshold.
        """
        import time
        start = time.monotonic()

        if not name or not self._raw_names:
            return ScreeningResult(
                query_name=name, query_country=country,
                is_hit=False, best_score=0,
            )

        query_norm   = normalise_name(name)
        query_tokens = get_tokens(name)
        matches: list[ScreeningMatch] = []

        # Pass 1: Exact match
        if query_norm in self._name_index:
            e = self._name_index[query_norm]
            matches.append(ScreeningMatch(
                entity_uid=e["uid"], matched_name=name,
                primary_name=e["name"], score=100, match_type="exact",
                programs=e["programs"], countries=e["countries"],
                is_mena=e["is_mena_relevant"], remarks=e.get("remarks", ""),
            ))

        # Pass 2: Fuzzy matching
        seen = {m.entity_uid for m in matches}
        fuzzy_results = process.extract(
            query_norm, self._raw_names,
            scorer=fuzz.token_set_ratio, limit=20, score_cutoff=threshold,
        )

        for norm_name, score, _ in fuzzy_results:
            entity = self._name_index.get(norm_name)
            if not entity:
                continue
            uid = entity["uid"]
            if uid in seen:
                for m in matches:
                    if m.entity_uid == uid and score > m.score:
                        m.score = int(score)
                continue
            seen.add(uid)

            boosted = int(score)
            if country and country in entity.get("countries", []):
                boosted = min(100, boosted + 5)  # country match boost

            mtype = "transliteration" if query_norm != normalise_name(norm_name) else "fuzzy"
            matches.append(ScreeningMatch(
                entity_uid=uid, matched_name=norm_name,
                primary_name=entity["name"], score=boosted, match_type=mtype,
                programs=entity["programs"], countries=entity["countries"],
                is_mena=entity["is_mena_relevant"], remarks=entity.get("remarks", ""),
            ))

        # Pass 3: Token overlap fallback
        if not matches and len(query_tokens) >= 2:
            for entity in self._entities:
                for ename in entity.get("all_names", []):
                    overlap = query_tokens & get_tokens(ename)
                    if len(overlap) >= 2:
                        score = int(100 * len(overlap) / max(len(query_tokens), len(get_tokens(ename))))
                        if score >= threshold:
                            uid = entity["uid"]
                            if uid not in seen:
                                seen.add(uid)
                                matches.append(ScreeningMatch(
                                    entity_uid=uid, matched_name=ename,
                                    primary_name=entity["name"], score=score, match_type="token",
                                    programs=entity["programs"], countries=entity["countries"],
                                    is_mena=entity["is_mena_relevant"], remarks=entity.get("remarks", ""),
                                ))

        matches.sort(key=lambda m: m.score, reverse=True)
        top     = matches[:max_results]
        best    = top[0].score if top else 0
        is_hit  = best >= THRESHOLD_PROBABLE
        ms      = (time.monotonic() - start) * 1000

        if is_hit:
            logger.warning(
                f"Sanctions HIT | query={name} | country={country} | "
                f"score={best} | match={top[0].primary_name} | "
                f"programs={top[0].programs} | severity={top[0].severity}"
            )

        return ScreeningResult(
            query_name=name, query_country=country,
            is_hit=is_hit, best_score=best,
            matches=top, screening_ms=ms,
        )

    def screen_transaction(self, counterparty: str, country: Optional[str] = None) -> ScreeningResult:
        """Convenience wrapper for transaction counterparty screening."""
        return self.screen(counterparty, country=country)

    @property
    def entity_count(self) -> int: return len(self._entities)

    @property
    def name_variant_count(self) -> int: return len(self._raw_names)

    def stats(self) -> dict:
        return {
            "entity_count"      : self.entity_count,
            "name_variant_count": self.name_variant_count,
            "data_source"       : "OFAC SDN" if SDN_PATH.exists() else "Legacy watchlist",
            "sdn_path_exists"   : SDN_PATH.exists(),
        }
