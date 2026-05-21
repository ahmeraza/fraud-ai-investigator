"""
scripts/load_ofac_data.py
──────────────────────────
Downloads and parses the OFAC SDN list from the US Treasury.

What is OFAC SDN?
  The Specially Designated Nationals list — ~12,000 individuals and entities
  that US persons (and banks processing USD) cannot do business with.
  UAE banks must screen against this list under correspondent banking rules
  and CBUAE AML guidelines.

Output files:
  app/data/sanctions/ofac_sdn.json      → parsed entities (used by screener)
  app/data/sanctions/ofac_metadata.json → download stats and timestamp

Flags:
  --sample  Use 5 built-in fictional entities (no internet, for dev/testing)
  --force   Re-download even if cached XML is < 24 hours old

Run:
  uv run python scripts/load_ofac_data.py --sample   (offline, always works)
  uv run python scripts/load_ofac_data.py            (real data, needs internet)
  uv run python scripts/load_ofac_data.py --force    (force fresh download)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_DIR   = PROJECT_ROOT / "app" / "data" / "sanctions"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SDN_OUTPUT_PATH  = OUTPUT_DIR / "ofac_sdn.json"
META_OUTPUT_PATH = OUTPUT_DIR / "ofac_metadata.json"
CACHE_PATH       = OUTPUT_DIR / "ofac_sdn_raw.xml"

OFAC_XML_URL = "https://www.treasury.gov/ofac/downloads/sdn.xml"

# Sanctions programs relevant to UAE/MENA operations
MENA_RELEVANT_PROGRAMS = {
    "IRAN", "SDGT", "SYRIA", "HAMAS", "HIZBALLAH",
    "RUSSIA", "YEMEN", "LIBYA", "SUDAN", "DPRK",
    "IFSR", "SDNTK", "TCO",
}

# ── Sample data for offline/testing use ───────────────────────────────────────
# Fictional entities that mirror real OFAC XML structure exactly.
# Used when --sample is passed or when internet is unavailable.

SAMPLE_SDN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<sdnList>
  <publShfDate>05/21/2026</publShfDate>
  <sdnEntry>
    <uid>1001</uid>
    <lastName>EASTERN STAR TRADING CORPORATION</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>DPRK</program></programList>
    <akaList>
      <aka><uid>2001</uid><type>a.k.a.</type><category>strong</category><lastName>EAST STAR TRADING CORP</lastName></aka>
      <aka><uid>2002</uid><type>a.k.a.</type><category>weak</category><lastName>ESTC</lastName></aka>
    </akaList>
    <addressList>
      <address><uid>3001</uid><country>KP</country></address>
    </addressList>
    <remarks>DPRK weapons procurement.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1002</uid>
    <lastName>AL RASHIDI</lastName>
    <firstName>MOHAMMAD</firstName>
    <sdnType>Individual</sdnType>
    <programList><program>SDGT</program><program>HAMAS</program></programList>
    <akaList>
      <aka><uid>2003</uid><type>a.k.a.</type><category>strong</category><lastName>AL-RASHIDI</lastName><firstName>MOHAMMED</firstName></aka>
      <aka><uid>2004</uid><type>a.k.a.</type><category>strong</category><lastName>AL RASHIDI</lastName><firstName>MUHAMMAD</firstName></aka>
      <aka><uid>2005</uid><type>a.k.a.</type><category>weak</category><lastName>RASHIDI</lastName><firstName>M.</firstName></aka>
    </akaList>
    <addressList>
      <address><uid>3002</uid><country>YE</country></address>
    </addressList>
    <remarks>Senior terrorism financier.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1003</uid>
    <lastName>GULF RESOURCES GENERAL TRADING LLC</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>IRAN</program><program>IFSR</program></programList>
    <akaList>
      <aka><uid>2006</uid><type>a.k.a.</type><category>strong</category><lastName>GULF RESOURCES FZE</lastName></aka>
      <aka><uid>2007</uid><type>a.k.a.</type><category>weak</category><lastName>GR TRADING</lastName></aka>
    </akaList>
    <addressList>
      <address><uid>3003</uid><country>AE</country></address>
      <address><uid>3004</uid><country>IR</country></address>
    </addressList>
    <remarks>UAE-registered Iranian sanctions evasion front company.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1004</uid>
    <lastName>HORIZON CAPITAL GROUP</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>RUSSIA</program></programList>
    <akaList>
      <aka><uid>2008</uid><type>a.k.a.</type><category>strong</category><lastName>HCG HOLDINGS</lastName></aka>
    </akaList>
    <addressList>
      <address><uid>3005</uid><country>RU</country></address>
    </addressList>
    <remarks>Russian entity subject to blocking sanctions.</remarks>
  </sdnEntry>
  <sdnEntry>
    <uid>1005</uid>
    <lastName>SHELL HOLDINGS INTERNATIONAL</lastName>
    <sdnType>Entity</sdnType>
    <programList><program>IRAN</program></programList>
    <akaList>
      <aka><uid>2009</uid><type>a.k.a.</type><category>strong</category><lastName>SHELL HOLDINGS INTL LTD</lastName></aka>
      <aka><uid>2010</uid><type>a.k.a.</type><category>strong</category><lastName>SHI LIMITED</lastName></aka>
    </akaList>
    <addressList>
      <address><uid>3006</uid><country>IR</country></address>
    </addressList>
    <remarks>Iranian petroleum company front.</remarks>
  </sdnEntry>
</sdnList>"""


def parse_ofac_xml(xml_content: str) -> list[dict]:
    """
    Parse OFAC SDN XML into normalised entity dicts.

    Each dict contains:
      uid, name, entity_type, programs, aliases (with category),
      all_names (primary + all aliases), countries, remarks, is_mena_relevant
    """
    root = ET.fromstring(xml_content.encode("utf-8") if isinstance(xml_content, str) else xml_content)

    # Handle optional XML namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    def tag(t): return f"{ns}{t}"
    def txt(el, t, default=""): e = el.find(tag(t)); return e.text.strip() if e is not None and e.text else default

    entities = []
    for entry in root.findall(tag("sdnEntry")):
        last  = txt(entry, "lastName")
        first = txt(entry, "firstName")
        name  = f"{first} {last}".strip() if first else last

        programs = [p.text.strip() for p in entry.findall(f".//{tag('program')}") if p.text]

        aliases = []
        for aka in entry.findall(f".//{tag('aka')}"):
            al = txt(aka, "lastName")
            af = txt(aka, "firstName")
            an = f"{af} {al}".strip() if af else al
            if an:
                aliases.append({"name": an, "category": txt(aka, "category", "weak")})

        countries = list({
            c.text.strip()
            for c in entry.findall(f".//{tag('country')}")
            if c.text
        })

        all_names = [name] + [a["name"] for a in aliases]

        entities.append({
            "uid"             : txt(entry, "uid"),
            "name"            : name,
            "entity_type"     : txt(entry, "sdnType", "Entity"),
            "programs"        : programs,
            "aliases"         : aliases,
            "all_names"       : all_names,
            "countries"       : countries,
            "remarks"         : txt(entry, "remarks"),
            "is_mena_relevant": bool(set(programs) & MENA_RELEVANT_PROGRAMS),
        })

    return entities


def download_ofac_xml(force: bool = False) -> str:
    """Download SDN XML, using 24h cache unless --force."""
    if CACHE_PATH.exists() and not force:
        age_h = (time.time() - CACHE_PATH.stat().st_mtime) / 3600
        if age_h < 24:
            print(f"  Using cached XML ({age_h:.1f}h old) — use --force to re-download")
            return CACHE_PATH.read_text(encoding="utf-8")

    if not HAS_REQUESTS:
        raise RuntimeError("requests not installed: uv pip install requests")

    print(f"  Downloading {OFAC_XML_URL} ...")
    start  = time.time()
    resp   = requests.get(OFAC_XML_URL, timeout=60, stream=True)
    resp.raise_for_status()

    chunks = []
    total  = 0
    for chunk in resp.iter_content(65536):
        chunks.append(chunk)
        total += len(chunk)
        if total % (1024 * 1024) == 0:
            print(f"    {total // (1024*1024)}MB downloaded...")

    content = b"".join(chunks).decode("utf-8", errors="replace")
    CACHE_PATH.write_text(content, encoding="utf-8")
    print(f"  Downloaded {total/1024/1024:.1f}MB in {time.time()-start:.1f}s")
    return content


def main(force: bool = False, sample: bool = False) -> None:
    print("=" * 55)
    print("  OFAC SDN LIST LOADER")
    print("  US Treasury Specially Designated Nationals")
    print("=" * 55)

    print("\n[1/3] Fetching XML...")
    if sample:
        print("  Using sample data (--sample)")
        xml = SAMPLE_SDN_XML
    else:
        try:
            xml = download_ofac_xml(force=force)
        except Exception as e:
            print(f"  Download failed: {e}")
            print("  Falling back to sample data.")
            xml = SAMPLE_SDN_XML

    print("\n[2/3] Parsing...")
    t0       = time.time()
    entities = parse_ofac_xml(xml)
    elapsed  = time.time() - t0

    total         = len(entities)
    individuals   = sum(1 for e in entities if e["entity_type"] == "Individual")
    corps         = sum(1 for e in entities if e["entity_type"] == "Entity")
    mena_relevant = sum(1 for e in entities if e["is_mena_relevant"])
    total_aliases = sum(len(e["aliases"]) for e in entities)
    total_names   = sum(len(e["all_names"]) for e in entities)
    prog_counts   = Counter(p for e in entities for p in e["programs"])

    print(f"  {total:,} entities parsed in {elapsed:.2f}s")
    print(f"  Individuals   : {individuals:,}")
    print(f"  Entities/Corps: {corps:,}")
    print(f"  MENA-relevant : {mena_relevant:,}")
    print(f"  Name variants : {total_names:,} (aliases included)")
    print(f"\n  Top programs:")
    for prog, count in prog_counts.most_common(5):
        star = "★" if prog in MENA_RELEVANT_PROGRAMS else " "
        print(f"    {star} {prog:<15} {count:>5}")

    print("\n[3/3] Saving...")
    SDN_OUTPUT_PATH.write_text(
        json.dumps(entities, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    META_OUTPUT_PATH.write_text(json.dumps({
        "source"         : "US Treasury OFAC SDN List",
        "source_url"     : OFAC_XML_URL,
        "downloaded_at"  : datetime.now(timezone.utc).isoformat(),
        "is_sample_data" : sample or not HAS_REQUESTS,
        "total_entities" : total,
        "individuals"    : individuals,
        "entities"       : corps,
        "mena_relevant"  : mena_relevant,
        "total_aliases"  : total_aliases,
        "total_names"    : total_names,
        "program_counts" : dict(prog_counts.most_common()),
    }, indent=2), encoding="utf-8")

    print(f"  {SDN_OUTPUT_PATH}")
    print(f"  {META_OUTPUT_PATH}")
    print("\n" + "=" * 55)
    print(f"  Done — {total:,} entities ready for screening")
    print("=" * 55 + "\n")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Download and parse the OFAC SDN list")
    p.add_argument("--force",  action="store_true", help="Force re-download")
    p.add_argument("--sample", action="store_true", help="Use built-in sample data")
    args = p.parse_args()
    main(force=args.force, sample=args.sample)
