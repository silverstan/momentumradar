# 📡 MomentumRadar — rebuilt

Static, auto-updating global stock momentum tracker. Real data from free APIs
(Yahoo Finance public API via `yfinance`, Stooq fallback), rebuilt **every
trading day** by GitHub Actions, deployed to GitHub Pages at momentumradar.net.

## What's inside

```
config/            markets.json (13 markets + ticker universes), names.json, site.json
build/build.py     the whole pipeline: fetch → score → analyse → render
build/templates/   Jinja2 templates (design + SEO)
content/           articles, glossary, FAQ, legal pages (edit freely)
static/            CSS, JS, favicon, robots.txt, ads.txt, CNAME
data/latest.json   yesterday's snapshot (committed daily by the bot)
site/              GENERATED OUTPUT — never edit by hand
.github/workflows/daily-build.yml   the daily automation
```

## One-time setup (about 15 minutes)

1. **Create a GitHub repo** (e.g. `momentumradar`) and push this folder to `main`.
2. Repo **Settings → Pages** → Source: **GitHub Actions**.
3. Repo **Settings → Actions → General** → Workflow permissions: **Read and write**.
4. **Custom domain:** Settings → Pages → Custom domain → `momentumradar.net`, tick *Enforce HTTPS*.
   At your DNS provider: `A` records for apex → GitHub Pages IPs
   (185.199.108.153 / .109. / .110. / .111.) and `CNAME www` → `<user>.github.io`.
   (`static/CNAME` is already in place.)
5. Go to **Actions → Daily data refresh & deploy → Run workflow**. First run
   fetches real data for all 13 markets and deploys (~3–6 min).

After that it runs automatically at **22:30 UTC every weekday** (after the US
close, when all 13 markets have same-day closing prices). Pushing any edit to
`main` also triggers a rebuild.

## Local preview

```bash
pip install -r requirements.txt
python build/build.py --seed     # offline sample data
python build/build.py            # real data (needs internet)
python -m http.server -d site 8000
```

`--seed` builds display a yellow "sample data" banner automatically. The banner
disappears on any real-data build.

## Google AdSense (do this AFTER the site is live with real data)

1. Apply at adsense.google.com with `momentumradar.net`. The site is designed to
   meet review requirements: substantial original content (5 articles, glossary,
   FAQ, methodology), privacy policy with the required AdSense cookie
   disclosures, terms, disclaimer, contact page, fast mobile-friendly pages,
   sitemap and robots.txt.
2. When approved, put your publisher ID in `config/site.json`:
   `"adsense_client": "ca-pub-XXXXXXXXXXXXXXXX"` — ad units then render
   automatically in the pre-placed, clearly-labelled slots (the site shows NO
   empty ad boxes while unset — important both for reviews and for looking
   professional).
3. Replace the placeholder line in `static/ads.txt` with the line AdSense gives you.
4. In AdSense, enable Google's **consent management platform (CMP)** for
   EEA/UK visitors — pairs with the disclosures already in the privacy policy.
5. Optional: replace the four `data-ad-slot` numbers in
   `build/templates/*.j2` with real slot IDs from AdSense ad units.

## Editing & extending

- **Add/remove tickers or whole markets:** edit `config/markets.json` (+ add
  display names in `config/names.json`). Everything else — pages, nav, footer,
  sitemap — regenerates automatically. To add e.g. India: new entry with
  `"index_symbol": "^NSEI"` and `.NS` tickers.
- **Change score weights/scales:** `config/site.json` → also update the two
  methodology write-ups (`content/about.html`, the score article) to stay honest.
- **Articles:** add a row in `content/articles.json` + an HTML fragment in
  `content/articles/`.
- **Never edit `site/`** — it's overwritten on every build.

## Data notes & honesty features

- Prices are end-of-day, dividend/split-adjusted, local currency.
- Every page shows the exact refresh timestamp.
- If a market's fetch fails, the previous day's data is reused and the page
  shows a visible "showing YYYY-MM-DD data" notice — the site never silently
  lies about freshness. If ALL markets fail, the build aborts and yesterday's
  site stays up.
- Vietnam coverage on Yahoo's free feed can be spotty; symbols that return no
  data are skipped automatically and the ranking uses whatever is available.
- The "why it's the top pick" text is **generated from the day's numbers** —
  it cannot go stale, and it never invents facts about the company.
- yfinance uses Yahoo's publicly available endpoints; keep the daily (not
  intraday) schedule to stay well within polite usage.

## Legal

Educational information only; not financial advice. See `content/disclaimer.html`,
`content/terms.html`, `content/privacy.html` — all published on the site.
Contact: hello@momentumradar.net
