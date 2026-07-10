# NTTS Parking Data

Daily-updated San Francisco parking enforcement data for the
[No Time to Speed](https://smorgiapps.com) iOS app, built entirely from
public DataSF datasets:

- **SFMTA Parking Citations & Fines** (`ab4h-6ztd`) — 12-month rolling window,
  geocoded against the Enterprise Addressing System (`ramy-di5m`)
- **Street Sweeping Schedule** (`yhqp-riqs`)
- **Parking Meters** (`8vzz-qzz9`) + **Meter Operating Schedules** (`6cqg-dxku`)

A GitHub Actions workflow runs `pipeline/run_pipeline.py` daily and publishes
`risk_grid.json`, `sweeping.json`, `meters.json`, and `manifest.json` to the
`gh-pages` branch, served via GitHub Pages.

Historical enforcement patterns are educational only. Always follow posted
signs — absence of past tickets is not permission to park illegally.
