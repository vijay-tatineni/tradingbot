# Classifier vs deterministic rules

_Generated: 2026-06-01T12:18:04.887217+00:00_

Rule baseline:

- `adx_14 > 25 AND range_efficiency > 0.5` → TRENDING
- `adx_14 < 20 AND range_efficiency < 0.3` → RANGING
- otherwise → UNCLEAR

## Summary

- Cache rows scanned: **126**
- Rows with a valid Claude regime: **126** (skipped 0)
- Agreement (rules == Claude): **97 / 126 = 77.0%**

## Confusion matrix

Rows = the rule's verdict. Columns = Claude's verdict. Diagonal cells are agreement; off-diagonal cells are disagreement.

| Rules \ Claude | TRENDING | RANGING | UNCLEAR | Row total |
|---|---|---|---|---|
| **TRENDING** | 0 | 0 | 0 | 0 |
| **RANGING** | 0 | 44 | 19 | 63 |
| **UNCLEAR** | 10 | 0 | 53 | 63 |
| **Column total** | 10 | 44 | 72 | 126 |

## Per-rule-label accuracy

| When rules said... | Claude agreed | Agreement % |
|---|---|---|
| TRENDING | 0 / 0 | 0.0% |
| RANGING | 44 / 63 | 69.8% |
| UNCLEAR | 53 / 63 | 84.1% |

## Disagreements (29)

| Instrument | Date | Claude | Conf | Rules | ADX | Range eff | MA200 slope %/d | ATR % |
|---|---|---|---|---|---|---|---|---|
| ANTO | 2026-05-19 | UNCLEAR | 0.62 | RANGING | 18.9 | 0.044 | 0.306 | 5.20 |
| SGLN | 2026-05-19 | UNCLEAR | 0.55 | RANGING | 19.7 | 0.227 | 0.148 | 1.63 |
| ANTO | 2026-05-20 | UNCLEAR | 0.62 | RANGING | 17.8 | 0.033 | 0.307 | 5.14 |
| SCCO | 2026-05-20 | UNCLEAR | 0.00 | RANGING | 15.9 | 0.087 | 0.286 | 4.92 |
| ANTO | 2026-05-21 | UNCLEAR | 0.62 | RANGING | 17.1 | 0.038 | 0.309 | 4.99 |
| SSLN | 2026-05-21 | UNCLEAR | 0.62 | RANGING | 15.7 | 0.044 | 0.316 | 4.70 |
| AAPL | 2026-05-22 | TRENDING | 0.82 | UNCLEAR | 37.0 | 0.339 | 0.156 | 1.86 |
| ANTO | 2026-05-22 | UNCLEAR | 0.62 | RANGING | 16.6 | 0.032 | 0.310 | 4.90 |
| NVTS | 2026-05-22 | TRENDING | 0.72 | UNCLEAR | 44.8 | 0.226 | 0.615 | 10.63 |
| SSLN | 2026-05-22 | UNCLEAR | 0.62 | RANGING | 15.1 | 0.000 | 0.317 | 4.44 |
| AAPL | 2026-05-25 | TRENDING | 0.82 | UNCLEAR | 37.0 | 0.339 | 0.156 | 1.86 |
| ANTO | 2026-05-25 | UNCLEAR | 0.62 | RANGING | 16.6 | 0.032 | 0.310 | 4.90 |
| SSLN | 2026-05-25 | UNCLEAR | 0.62 | RANGING | 15.1 | 0.000 | 0.317 | 4.44 |
| AAPL | 2026-05-26 | TRENDING | 0.82 | UNCLEAR | 38.7 | 0.363 | 0.160 | 1.77 |
| ANTO | 2026-05-26 | UNCLEAR | 0.62 | RANGING | 16.9 | 0.118 | 0.313 | 4.80 |
| NVTS | 2026-05-26 | TRENDING | 0.78 | UNCLEAR | 46.6 | 0.249 | 0.643 | 10.26 |
| SSLN | 2026-05-26 | UNCLEAR | 0.62 | RANGING | 14.5 | 0.016 | 0.318 | 4.33 |
| AAPL | 2026-05-27 | TRENDING | 0.85 | UNCLEAR | 40.5 | 0.356 | 0.163 | 1.71 |
| ANTO | 2026-05-27 | UNCLEAR | 0.62 | RANGING | 17.3 | 0.143 | 0.315 | 4.44 |
| NVTS | 2026-05-27 | TRENDING | 0.78 | UNCLEAR | 47.2 | 0.249 | 0.674 | 11.69 |
| SCCO | 2026-05-27 | UNCLEAR | 0.62 | RANGING | 13.8 | 0.149 | 0.295 | 4.42 |
| AAPL | 2026-05-28 | TRENDING | 0.88 | UNCLEAR | 42.1 | 0.379 | 0.166 | 1.63 |
| ANTO | 2026-05-28 | UNCLEAR | 0.62 | RANGING | 17.9 | 0.219 | 0.317 | 4.38 |
| NVTS | 2026-05-28 | TRENDING | 0.75 | UNCLEAR | 47.4 | 0.233 | 0.708 | 11.91 |
| SCCO | 2026-05-28 | UNCLEAR | 0.62 | RANGING | 14.2 | 0.209 | 0.297 | 4.38 |
| AAPL | 2026-05-29 | TRENDING | 0.88 | UNCLEAR | 43.8 | 0.372 | 0.168 | 1.59 |
| ANTO | 2026-05-29 | UNCLEAR | 0.62 | RANGING | 18.7 | 0.238 | 0.318 | 4.28 |
| SCCO | 2026-05-29 | UNCLEAR | 0.62 | RANGING | 14.5 | 0.157 | 0.299 | 4.36 |
| SSLN | 2026-05-29 | UNCLEAR | 0.62 | RANGING | 14.1 | 0.099 | 0.315 | 3.95 |
