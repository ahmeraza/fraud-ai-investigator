# Crypto Monitoring Data

Stores output from the crypto address screening pipeline.

## Files

| File | Description |
|---|---|
| `screening_results.json` | Output from `scripts/screen_crypto_addresses.py` |

## How to populate

```bash
# Demo mode — uses known Tornado Cash addresses, no API credits wasted
uv run python scripts/screen_crypto_addresses.py --demo

# Screen custom addresses
uv run python scripts/screen_crypto_addresses.py \
  --addresses 0xYourAddress1 0xYourAddress2

# Screen from file (one address per line)
uv run python scripts/screen_crypto_addresses.py --file watchlist.txt
```

## Etherscan API key

Get a free key at: https://etherscan.io/myapikey
Add to `.env`: `ETHERSCAN_API_KEY=your_key_here`

Free tier: 5 req/sec, 100,000 req/day — sufficient for this project.
