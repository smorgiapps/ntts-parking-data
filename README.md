# NTTS Parking Data

Daily-updated San Francisco parking enforcement data for the
[No Time to Speed](https://smorgiapps.com) iOS app, built entirely from
public DataSF datasets:

- **SFMTA Parking Citations & Fines** (`ab4h-6ztd`) — 12-month rolling window,
  geocoded against the Enterprise Addressing System (`ramy-di5m`)
- **Street Sweeping Schedule** (`yhqp-riqs`)
- **Parking Meters** (`8vzz-qzz9`) + **Meter Operating Schedules** (`6cqg-dxku`)
- **SFMTA Digital Curb** — posted tow-away, time-limit, and RPP rules

A GitHub Actions workflow runs `pipeline/run_pipeline.py` daily and publishes
the **v6 bundle** to the `gh-pages` branch, served via GitHub Pages.

## Published files

| File | Purpose |
|------|---------|
| `manifest.json` | Bundle version (6) + file index |
| `risk_grid.json` | Citation risk grid with **pre-bound street rules** and per-block side parity |
| `sweeping.json` | Street sweeping schedule blockfaces |
| `meters.json` | Metered blocks with operating hours |
| `regulations.json` | RPP / time-limit regulations |
| `violation_index.json` | Violation code → category lookup |
| `insights.json` | Block-level baseline summaries |
| `details/` | On-demand citation detail shards (~550 m tiles) |
| `curb/` | Digital curb rule shards |

The pipeline validates known blocks (900 Pine, 1400 Pine, 800 Taylor, 800 Bush)
before publishing. Side-specific rules use EAS address parity and citation
corroboration so tow-away and sweep rules are not duplicated across both sides
of a block.

Historical enforcement patterns are educational only. Always follow posted
signs — absence of past tickets is not permission to park illegally.
