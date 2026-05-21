# IEEE-CIS Fraud Detection Dataset

This directory holds the processed IEEE-CIS real-world transaction data.

## Download instructions (free, manual)

1. Go to: https://www.kaggle.com/competitions/ieee-fraud-detection/data
2. Create a free Kaggle account (if needed)
3. Accept the competition rules ("I Understand and Accept")
4. Download these two files:
   - `train_transaction.csv` (~500MB)
   - `train_identity.csv` (~34MB)
5. Place both files in this directory (`app/data/ieee_cis/`)
6. Run the processor from the project root:

```bash
uv run python scripts/load_ieee_data.py
```

## What the processor creates

| File | Description |
|---|---|
| `ieee_transactions.json` | 2,000 sampled transactions in pipeline format |
| `ieee_metadata.json` | Processing stats and timestamp |
| `ieee_feature_stats.json` | Full dataset feature distributions |

## Without downloading

The system works without this data — it automatically falls back
to the synthetic Faker-generated transactions. The API endpoint
`GET /v1/alerts/datasource` shows which source is currently active.

## Dataset facts

- 590,540 transactions from Vesta Corporation (real e-commerce)
- 3.5% fraud rate (vs 20% in synthetic data)
- 434 features including device, identity, and Vesta-engineered signals
- Released for the 2019 Kaggle IEEE-CIS Fraud Detection competition

## .gitignore note

The raw CSV files are not committed (too large, Kaggle license).
The processed JSON output files ARE committed (small, no PII).
