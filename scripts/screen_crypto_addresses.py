"""
scripts/screen_crypto_addresses.py
────────────────────────────────────
Batch crypto address screening script.

Use this to screen a list of wallet addresses offline (no API server needed).
Results saved to app/data/crypto/screening_results.json.

When to use this vs the API:
  API (/v1/crypto/screen)    → real-time, one address, human-facing
  This script                → batch watchlist monitoring, CI/CD, scheduled jobs

Usage:
  # Screen addresses from a text file (one per line)
  uv run python scripts/screen_crypto_addresses.py --file addresses.txt

  # Screen specific addresses directly
  uv run python scripts/screen_crypto_addresses.py \
    --addresses 0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045 \
                0x742d35Cc6634C0532925a3b844Bc454e4438f44e

  # Demo mode — uses known Tornado Cash addresses to verify detection works
  uv run python scripts/screen_crypto_addresses.py --demo

Output:
  app/data/crypto/screening_results.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

OUTPUT_DIR = PROJECT_ROOT / "app" / "data" / "crypto"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Known Tornado Cash addresses for demo/verification
DEMO_ADDRESSES = [
    "0xd90e2f925da726b50c4ed8d0fb90ad053324f31b",  # TC Router — sanctioned
    "0x47ce0c6ed5b0ce3d3a51fdb1c52dc66a7c3c2936",  # TC 1 ETH Pool — sanctioned
    "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045",  # Vitalik.eth — should be clean
]


def load_settings():
    from app.core.config import get_settings
    return get_settings()


def screen_addresses(addresses: list[str], api_key: str, demo: bool = False) -> dict:
    from app.crypto.crypto_alert_engine import CryptoAlertEngine, CryptoScreeningRequest
    from app.crypto.mixer_detector import SANCTIONED_MIXER_ADDRESSES

    print("=" * 60)
    print("  CRYPTO ADDRESS SCREENING")
    print(f"  {len(addresses)} address(es) | Etherscan V2 API")
    print("=" * 60)

    engine  = CryptoAlertEngine(etherscan_api_key=api_key, score_threshold=60)
    results = []
    flagged = 0

    for i, address in enumerate(addresses, 1):
        print(f"\n[{i}/{len(addresses)}] Screening {address[:10]}...")

        # Check known mixer list first (instant, no API call)
        addr_lower = address.lower()
        if addr_lower in SANCTIONED_MIXER_ADDRESSES:
            mixer = SANCTIONED_MIXER_ADDRESSES[addr_lower]
            print(f"  ⚠ KNOWN MIXER: {mixer['name']} ({mixer['sanction']})")

        request  = CryptoScreeningRequest(
            address     = address,
            customer_id = f"BATCH-{i:03d}",
            tx_id       = f"SCREEN-{i:03d}",
        )
        response = engine.screen_address(request)

        if response.error:
            print(f"  ✗ Error: {response.error}")
        elif response.detection_result:
            r = response.detection_result
            status = "🔴 FLAGGED" if r.is_flagged else "🟢 CLEAR"
            print(f"  {status} | score={r.risk_score} | severity={r.severity}")
            print(f"  txs={r.transaction_count} | direct_hits={len(r.direct_hits)}")
            if r.is_flagged:
                flagged += 1
                for signal in r.signals[:2]:
                    print(f"  → {signal.signal_type}: {signal.description[:80]}")

        results.append(response.to_dict())

        # Rate limit between addresses
        if i < len(addresses):
            time.sleep(1.0)

    # Save results
    output = {
        "screened_at"   : datetime.now(timezone.utc).isoformat(),
        "total_addresses": len(addresses),
        "flagged"       : flagged,
        "clear"         : len(addresses) - flagged,
        "demo_mode"     : demo,
        "results"       : results,
    }

    output_path = OUTPUT_DIR / "screening_results.json"
    output_path.write_text(json.dumps(output, indent=2, default=str))

    print("\n" + "=" * 60)
    print(f"  Screening complete | {len(addresses)} addresses | {flagged} flagged")
    print(f"  Results saved: {output_path}")
    print("=" * 60 + "\n")

    return output


def main():
    p = argparse.ArgumentParser(description="Batch crypto address screening")
    p.add_argument("--addresses", nargs="+", help="Addresses to screen")
    p.add_argument("--file",      help="Text file with one address per line")
    p.add_argument("--demo",      action="store_true", help="Use demo addresses")
    args = p.parse_args()

    settings = load_settings()

    if not settings.etherscan_api_key:
        print("ERROR: ETHERSCAN_API_KEY not set in .env")
        print("Get free key: https://etherscan.io/myapikey")
        sys.exit(1)

    addresses = []

    if args.demo:
        addresses = DEMO_ADDRESSES
        print("Demo mode: using known Tornado Cash addresses")

    elif args.file:
        path = Path(args.file)
        if not path.exists():
            print(f"ERROR: File not found: {path}")
            sys.exit(1)
        addresses = [
            line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")
        ]

    elif args.addresses:
        addresses = args.addresses

    else:
        print("Provide --addresses, --file, or --demo")
        p.print_help()
        sys.exit(1)

    screen_addresses(addresses, api_key=settings.etherscan_api_key, demo=args.demo)


if __name__ == "__main__":
    main()
