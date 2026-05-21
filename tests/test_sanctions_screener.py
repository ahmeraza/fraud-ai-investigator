"""
tests/test_sanctions_screener.py
─────────────────────────────────
Tests for the OFAC sanctions screener.

All tests run offline — no internet, no API credits.
Uses the sample SDN XML embedded in load_ofac_data.py.

Coverage:
  - Name normalisation and Arabic transliteration
  - Exact matching (score 100)
  - Alias matching
  - Arabic name variant matching (Mohammed/Muhammad/Mohammad)
  - Country score boost
  - Severity classification (DEFINITIVE/STRONG/PROBABLE/POSSIBLE)
  - OFAC XML parser against sample data
  - Full main() pipeline with --sample flag

Run:
    uv run pytest tests/test_sanctions_screener.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.services.sanctions_screener import (
    SanctionsScreener, ScreeningMatch,
    normalise_name, get_tokens,
    THRESHOLD_PROBABLE, THRESHOLD_STRONG, THRESHOLD_DEFINITIVE,
)
from app.services.alert_store import store

# ── Sample entities (used for screener fixture) ───────────────────────────────

SAMPLE_ENTITIES = [
    {
        "uid": "1001", "name": "EASTERN STAR TRADING CORPORATION",
        "entity_type": "Entity", "programs": ["DPRK"],
        "aliases": [
            {"name": "EAST STAR TRADING CORP", "category": "strong"},
            {"name": "ESTC", "category": "weak"},
        ],
        "all_names": ["EASTERN STAR TRADING CORPORATION", "EAST STAR TRADING CORP", "ESTC"],
        "countries": ["KP"], "remarks": "DPRK weapons.", "is_mena_relevant": True,
    },
    {
        "uid": "1002", "name": "MOHAMMAD AL RASHIDI",
        "entity_type": "Individual", "programs": ["SDGT", "HAMAS"],
        "aliases": [
            {"name": "MOHAMMED AL-RASHIDI",  "category": "strong"},
            {"name": "MUHAMMAD AL RASHIDI",  "category": "strong"},
            {"name": "M. RASHIDI",           "category": "weak"},
        ],
        "all_names": ["MOHAMMAD AL RASHIDI", "MOHAMMED AL-RASHIDI", "MUHAMMAD AL RASHIDI", "M. RASHIDI"],
        "countries": ["YE", "IR"], "remarks": "Terrorism financier.", "is_mena_relevant": True,
    },
    {
        "uid": "1003", "name": "GULF RESOURCES GENERAL TRADING LLC",
        "entity_type": "Entity", "programs": ["IRAN", "IFSR"],
        "aliases": [
            {"name": "GULF RESOURCES FZE", "category": "strong"},
            {"name": "GR TRADING",         "category": "weak"},
        ],
        "all_names": ["GULF RESOURCES GENERAL TRADING LLC", "GULF RESOURCES FZE", "GR TRADING"],
        "countries": ["AE", "IR"], "remarks": "Iranian front company.", "is_mena_relevant": True,
    },
]


@pytest.fixture
def screener(tmp_path) -> SanctionsScreener:
    """Screener loaded with sample entities via patched SDN path."""
    sdn = tmp_path / "sanctions" / "ofac_sdn.json"
    sdn.parent.mkdir(parents=True)
    sdn.write_text(json.dumps(SAMPLE_ENTITIES))
    with patch("app.services.sanctions_screener.SDN_PATH", sdn):
        return SanctionsScreener()


@pytest.fixture(autouse=True)
def clear_store():
    store.clear()
    yield
    store.clear()


# ── Name normalisation ────────────────────────────────────────────────────────

class TestNormalisation:
    def test_lowercase(self):
        assert normalise_name("GULF RESOURCES FZE") == "gulf resources fze"

    def test_strips_hyphens(self):
        assert normalise_name("Al-Rashidi") == "al rashidi"

    def test_mohammed_variants_all_normalise_to_muhammad(self):
        variants = ["Mohammed", "Mohammad", "Mohamed", "Mohamad", "Muhammed"]
        assert len({normalise_name(v) for v in variants}) == 1

    def test_al_and_el_prefix_normalise_same(self):
        assert normalise_name("Al-Rashidi") == normalise_name("El-Rashidi")

    def test_collapses_spaces(self):
        assert normalise_name("Gulf   Resources") == "gulf resources"

    def test_empty_string(self):
        assert normalise_name("") == ""

    def test_tokens(self):
        assert get_tokens("Gulf Resources FZE") == {"gulf", "resources", "fze"}


# ── Screening ─────────────────────────────────────────────────────────────────

class TestScreener:
    def test_exact_match_scores_100(self, screener):
        r = screener.screen("EASTERN STAR TRADING CORPORATION")
        assert r.is_hit
        assert r.best_score == 100
        assert r.top_match.match_type == "exact"

    def test_alias_match(self, screener):
        r = screener.screen("GULF RESOURCES FZE")
        assert r.is_hit
        assert r.best_score >= THRESHOLD_PROBABLE

    def test_arabic_transliteration_mohammed(self, screener):
        r = screener.screen("Mohammed Al-Rashidi")
        assert r.is_hit, f"Expected hit — got score {r.best_score}"
        assert r.top_match.entity_uid == "1002"

    def test_muhammad_variant(self, screener):
        r = screener.screen("Muhammad Al Rashidi")
        assert r.is_hit
        assert r.top_match.entity_uid == "1002"

    def test_no_match_clean_name(self, screener):
        r = screener.screen("Emirates NBD Bank Dubai")
        assert not r.is_hit

    def test_country_boost(self, screener):
        with_country    = screener.screen("Gulf Resources FZE", country="IR")
        without_country = screener.screen("Gulf Resources FZE")
        assert with_country.best_score >= without_country.best_score

    def test_empty_name_no_hit(self, screener):
        assert not screener.screen("").is_hit

    def test_result_has_timing(self, screener):
        r = screener.screen("Gulf Resources FZE")
        assert r.screening_ms > 0

    def test_match_includes_programs(self, screener):
        r = screener.screen("GULF RESOURCES GENERAL TRADING LLC")
        assert "IRAN" in r.top_match.programs

    def test_stats(self, screener):
        s = screener.stats()
        assert s["entity_count"] == len(SAMPLE_ENTITIES)
        assert s["name_variant_count"] > len(SAMPLE_ENTITIES)


# ── ScreeningMatch ────────────────────────────────────────────────────────────

class TestScreeningMatch:
    def _m(self, score):
        return ScreeningMatch(
            entity_uid="99", matched_name="Test", primary_name="Test Ltd",
            score=score, match_type="fuzzy", programs=["IRAN"],
        )

    def test_definitive(self):
        m = self._m(95)
        assert m.severity == "DEFINITIVE"
        assert m.recommended_action == "BLOCK_IMMEDIATELY"

    def test_strong(self):
        m = self._m(80)
        assert m.severity == "STRONG"
        assert m.recommended_action == "ESCALATE_COMPLIANCE"

    def test_probable(self):
        m = self._m(60)
        assert m.severity == "PROBABLE"
        assert m.recommended_action == "MANUAL_REVIEW"

    def test_possible(self):
        m = self._m(45)
        assert m.severity == "POSSIBLE"

    def test_to_dict(self):
        d = self._m(85).to_dict()
        for key in {"entity_uid", "score", "severity", "recommended_action", "programs"}:
            assert key in d


# ── OFAC loader ───────────────────────────────────────────────────────────────

class TestOFACLoader:
    def test_parse_sample_xml(self):
        from scripts.load_ofac_data import parse_ofac_xml, SAMPLE_SDN_XML
        entities = parse_ofac_xml(SAMPLE_SDN_XML)
        assert len(entities) == 5

    def test_aliases_parsed(self):
        from scripts.load_ofac_data import parse_ofac_xml, SAMPLE_SDN_XML
        entities = parse_ofac_xml(SAMPLE_SDN_XML)
        individual = next(e for e in entities if "RASHIDI" in e["name"])
        assert len(individual["aliases"]) >= 2

    def test_all_names_includes_primary(self):
        from scripts.load_ofac_data import parse_ofac_xml, SAMPLE_SDN_XML
        for entity in parse_ofac_xml(SAMPLE_SDN_XML):
            assert entity["name"] in entity["all_names"]

    def test_mena_flag_on_iran_entity(self):
        from scripts.load_ofac_data import parse_ofac_xml, SAMPLE_SDN_XML
        entities = parse_ofac_xml(SAMPLE_SDN_XML)
        iran = next(e for e in entities if "IRAN" in e["programs"])
        assert iran["is_mena_relevant"] is True

    def test_main_sample_runs(self, tmp_path):
        from scripts.load_ofac_data import main
        with patch("scripts.load_ofac_data.OUTPUT_DIR", tmp_path), \
             patch("scripts.load_ofac_data.SDN_OUTPUT_PATH", tmp_path / "ofac_sdn.json"), \
             patch("scripts.load_ofac_data.META_OUTPUT_PATH", tmp_path / "ofac_metadata.json"), \
             patch("scripts.load_ofac_data.CACHE_PATH", tmp_path / "cache.xml"):
            main(sample=True)
        assert (tmp_path / "ofac_sdn.json").exists()
