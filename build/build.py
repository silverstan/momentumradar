#!/usr/bin/env python3
"""
MomentumRadar daily build pipeline.

  python build/build.py            # fetch real data (yfinance + Stooq fallback) and render site/
  python build/build.py --seed     # render with deterministic sample data (no network) for local preview
  python build/build.py --render-only  # re-render templates from existing data/latest.json

Data sources (all free):
  1. Yahoo Finance public API via yfinance  (primary, covers all 13 markets)
  2. Stooq CSV endpoint                     (fallback for US tickers Yahoo misses)
If a market fails entirely, the previous day's data for that market is reused and
flagged "stale" on the page — the site never shows silently wrong numbers.
"""
import argparse, datetime as dt, hashlib, json, math, os, random, shutil, sys

import numpy as np
import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "config")
DATA = os.path.join(ROOT, "data")
SITE = os.path.join(ROOT, "site")
TPL = os.path.join(ROOT, "build", "templates")
CONTENT = os.path.join(ROOT, "content")
STATIC = os.path.join(ROOT, "static")

TF = ["d1", "w1", "m1", "q1", "y1"]
TF_LABEL = {"d1": "1D", "w1": "1W", "m1": "1M", "q1": "3M", "y1": "1Y"}
TF_NAME = {"d1": "day", "w1": "week", "m1": "month", "q1": "quarter", "y1": "year"}
TF_OFFSET = {"d1": 1, "w1": 5, "m1": 21, "q1": 63, "y1": 252}


def jload(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _np_default(o):
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    raise TypeError(f"not serializable: {type(o)}")


def jdump(obj, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, default=_np_default)


# ---------------------------------------------------------------- data fetch
def fetch_market_history(tickers, index_symbol):
    """Return (closes DataFrame [date x ticker], index Series or None)."""
    import yfinance as yf
    syms = list(tickers) + ([index_symbol] if index_symbol else [])
    df = yf.download(syms, period="2y", interval="1d", auto_adjust=True,
                     progress=False, threads=True, group_by="column")
    closes = df["Close"] if isinstance(df.columns, pd.MultiIndex) else df[["Close"]]
    closes = closes.dropna(axis=1, how="all")
    idx = None
    if index_symbol and index_symbol in closes.columns:
        idx = closes[index_symbol].dropna()
        closes = closes.drop(columns=[index_symbol])
    # Stooq fallback for plain US tickers Yahoo missed
    missing = [t for t in tickers if t not in closes.columns and "." not in t and "-" not in t]
    for t in missing:
        s = fetch_stooq(t)
        if s is not None:
            closes[t] = s
    return closes.sort_index(), idx


def fetch_stooq(ticker):
    import requests
    try:
        url = f"https://stooq.com/q/d/l/?s={ticker.lower()}.us&i=d"
        r = requests.get(url, timeout=20)
        if r.ok and r.text.startswith("Date"):
            from io import StringIO
            df = pd.read_csv(StringIO(r.text), parse_dates=["Date"], index_col="Date")
            if len(df) > 260:
                return df["Close"]
    except Exception as e:
        print(f"  stooq fallback failed for {ticker}: {e}")
    return None


def seed_series(ticker, n=520):
    """Deterministic plausible price series for offline preview builds."""
    rng = random.Random(int(hashlib.md5(ticker.encode()).hexdigest(), 16))
    drift = rng.uniform(-0.0006, 0.0018)
    vol = rng.uniform(0.010, 0.028)
    p = rng.uniform(4, 900)
    out = []
    for _ in range(n):
        p *= math.exp(rng.gauss(drift, vol))
        out.append(p)
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=n)
    return pd.Series(out, index=idx)


# ------------------------------------------------------------- computations
def pct(series, back):
    if len(series) <= back:
        return None
    a, b = series.iloc[-1 - back], series.iloc[-1]
    if a and not math.isnan(a) and a != 0:
        return (b / a - 1.0) * 100.0
    return None


def score_at(series, weights, scales):
    """Momentum Score 0-100 for the last point of `series`."""
    total_w, acc = 0.0, 0.0
    for tf in TF:
        r = pct(series, TF_OFFSET[tf])
        if r is None:
            continue
        s = 50.0 + 50.0 * math.tanh(r / scales[tf])
        acc += weights[tf] * s
        total_w += weights[tf]
    if total_w == 0:
        return None
    return round(acc / total_w)


def score_label(s):
    if s >= 80: return "Strong"
    if s >= 65: return "Good"
    if s >= 50: return "Moderate"
    if s >= 35: return "Weak"
    return "Very Weak"


def spark_path(values, w=120, h=36, pad=2):
    """SVG path 'd' for a sparkline."""
    v = [x for x in values if x is not None and not math.isnan(x)]
    if len(v) < 2:
        return ""
    lo, hi = min(v), max(v)
    rng = (hi - lo) or 1.0
    pts = []
    for i, x in enumerate(v):
        px = pad + i * (w - 2 * pad) / (len(v) - 1)
        py = h - pad - (x - lo) * (h - 2 * pad) / rng
        pts.append(f"{px:.1f},{py:.1f}")
    return "M" + " L".join(pts)


def fmt_price(p, sym):
    if p is None:
        return "—"
    if p >= 10000:
        return f"{sym}{p:,.0f}"
    if p >= 100:
        return f"{sym}{p:,.2f}".rstrip("0").rstrip(".")
    return f"{sym}{p:,.2f}"


# --------------------------------------------------- explanation generation
def build_analysis(st, market, rank_total):
    """Honest, data-driven narrative. Every sentence is derived from the numbers."""
    name, score = st["name"], st["score"]
    r = st["returns"]
    pos_tfs = [tf for tf in TF if r.get(tf) is not None and r[tf] > 0]
    best_tf = max((tf for tf in TF if r.get(tf) is not None), key=lambda t: r[t], default=None)
    worst_tf = min((tf for tf in TF if r.get(tf) is not None), key=lambda t: r[t], default=None)

    p = []
    p.append(
        f"{name} currently ranks #{st['rank']} of {rank_total} stocks we track in {market['name']}, "
        f"with a Momentum Score of {score}/100 ({score_label(score)}). The score combines its price "
        f"performance over the past day, week, month, quarter and year — so a high number means the "
        f"stock has been rising consistently across several horizons, not just spiking for a day."
    )

    s2 = f"It is up across {len(pos_tfs)} of the 5 tracked timeframes."
    if best_tf is not None:
        s2 += f" Its strongest stretch is the past {TF_NAME[best_tf]} ({r[best_tf]:+.1f}%)"
        if worst_tf is not None and worst_tf != best_tf:
            s2 += f", while its weakest is the past {TF_NAME[worst_tf]} ({r[worst_tf]:+.1f}%)"
        s2 += "."
    if r.get("m1") is not None and r.get("y1") is not None:
        if r["m1"] > 0 and r["m1"] * 12 > r["y1"]:
            s2 += " Short-term gains are running ahead of the longer-term trend — momentum has been accelerating recently."
        elif r["m1"] < 0 < r["y1"]:
            s2 += " The longer-term uptrend is intact, but the last month has cooled — momentum may be pausing."
        elif r["m1"] > 0 and r["y1"] > 0:
            s2 += " Gains have been reasonably steady rather than concentrated in one burst."
    p.append(s2)

    s3 = ""
    if st.get("hi52_dist") is not None:
        d = st["hi52_dist"]
        if d > -1.0:
            s3 += "The share price is trading within 1% of its 52-week high — a level many traders watch closely. "
        else:
            s3 += f"The share price sits {abs(d):.1f}% below its 52-week high. "
    if st.get("score_30d_ago") is not None:
        a, b = st["score_30d_ago"], score
        verb = "risen" if b > a else ("fallen" if b < a else "held steady")
        s3 += f"Its Momentum Score has {verb} from {a} to {b} over the past 30 trading sessions — see the momentum history chart below."
    if s3:
        p.append(s3.strip())

    p.append(
        "Remember: a high Momentum Score describes what the price has already done — it is not a "
        "prediction. Strong runs can and do reverse quickly. Before acting on any stock, review the "
        "company's actual financial results using the report links provided, and consider speaking to "
        "a licensed financial adviser."
    )

    facts = []
    for tf in TF:
        if r.get(tf) is not None:
            facts.append({"label": f"Past {TF_NAME[tf]}", "value": f"{r[tf]:+.2f}%", "up": r[tf] >= 0})
    if st.get("hi52_dist") is not None:
        facts.append({"label": "vs 52-week high", "value": f"{st['hi52_dist']:+.1f}%", "up": st["hi52_dist"] > -5})
    if st.get("vol30") is not None:
        facts.append({"label": "30-day volatility (annualised)", "value": f"{st['vol30']:.0f}%", "up": None})
    return {"paragraphs": p, "facts": facts}


def stock_links(ticker):
    t = ticker
    return {
        "quote": f"https://finance.yahoo.com/quote/{t}",
        "financials": f"https://finance.yahoo.com/quote/{t}/financials",
        "history": f"https://finance.yahoo.com/quote/{t}/history",
        "profile": f"https://finance.yahoo.com/quote/{t}/profile",
    }


# ------------------------------------------------------------ market builds
def build_market(m, closes, idx_series, names, site_cfg):
    W, S = site_cfg["score_weights"], site_cfg["score_scales"]
    hist_days = site_cfg.get("history_days", 90)
    stocks = []
    for t in m["tickers"]:
        if t not in closes.columns:
            continue
        s = closes[t].dropna()
        if len(s) < 30:
            continue
        returns = {tf: (None if pct(s, TF_OFFSET[tf]) is None else round(pct(s, TF_OFFSET[tf]), 2)) for tf in TF}
        sc = score_at(s, W, S)
        if sc is None:
            continue
        # momentum score history
        hist = []
        n = min(hist_days, max(0, len(s) - 60))
        for i in range(n, 0, -1):
            sub = s.iloc[: len(s) - i]
            hs = score_at(sub, W, S)
            if hs is not None:
                hist.append(hs)
        hist.append(sc)
        last252 = s.iloc[-252:] if len(s) >= 252 else s
        hi52, lo52 = float(last252.max()), float(last252.min())
        rets30 = s.pct_change().iloc[-30:].dropna()
        vol30 = float(rets30.std() * math.sqrt(252) * 100) if len(rets30) > 5 else None
        stocks.append({
            "ticker": t,
            "name": names.get(t, t),
            "price": float(s.iloc[-1]),
            "price_fmt": fmt_price(float(s.iloc[-1]), m["currency_symbol"]),
            "returns": returns,
            "score": sc,
            "score_lbl": score_label(sc),
            "score_hist": hist,
            "score_30d_ago": hist[0] if len(hist) >= 30 else None,
            "hi52_dist": round((float(s.iloc[-1]) / hi52 - 1) * 100, 2) if hi52 else None,
            "lo52_dist": round((float(s.iloc[-1]) / lo52 - 1) * 100, 2) if lo52 else None,
            "vol30": vol30,
            "spark_price": [round(float(x), 4) for x in s.iloc[-30:].tolist()],
            "links": stock_links(t),
        })
    stocks.sort(key=lambda x: (-x["score"], x["ticker"]))
    universe_n = len(stocks)
    stocks = stocks[:10]
    for i, st in enumerate(stocks, 1):
        st["rank"] = i
        st["spark_price_d"] = spark_path(st["spark_price"])
        st["spark_score_d"] = spark_path(st["score_hist"])
        st["analysis"] = build_analysis(st, m, universe_n)

    index_block = None
    if idx_series is not None and len(idx_series) > 2:
        index_block = {
            "level": round(float(idx_series.iloc[-1]), 2),
            "d1": round(pct(idx_series, 1) or 0, 2),
            "m1": round(pct(idx_series, 21), 2) if pct(idx_series, 21) is not None else None,
        }
    avg = round(sum(s["score"] for s in stocks) / len(stocks)) if stocks else None
    return {"stocks": stocks, "index": index_block, "avg_score": avg,
            "avg_lbl": score_label(avg) if avg is not None else None,
            "universe_n": universe_n}


def gather(seed, site_cfg, markets_cfg, names):
    try:
        prev = jload(os.path.join(DATA, "latest.json"))
    except Exception:
        prev = {}
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
           "date": dt.date.today().isoformat(), "is_seed": seed, "markets": {}}
    for m in markets_cfg["markets"]:
        print(f"[{m['id']}] fetching {len(m['tickers'])} tickers ...")
        try:
            if seed:
                closes = pd.DataFrame({t: seed_series(t) for t in m["tickers"]})
                idx = seed_series("^IDX_" + m["id"])
            else:
                closes, idx = fetch_market_history(m["tickers"], m.get("index_symbol") or None)
            built = build_market(m, closes, idx, names, site_cfg)
            if not built["stocks"]:
                raise RuntimeError("no usable price data")
            built["stale"] = False
            out["markets"][m["id"]] = built
            print(f"  ok — {built['universe_n']} priced, top score {built['stocks'][0]['score']}")
        except Exception as e:
            print(f"  FAILED ({e}); reusing previous data if available")
            prev_m = (prev.get("markets") or {}).get(m["id"])
            if prev_m:
                prev_m["stale"] = True
                prev_m["stale_since"] = prev.get("date")
                out["markets"][m["id"]] = prev_m
    return out


# ------------------------------------------------------------------ render
def render(data, site_cfg, markets_cfg):
    env = Environment(loader=FileSystemLoader(TPL), autoescape=select_autoescape(["html"]))
    env.globals.update(site=site_cfg, now_year=dt.date.today().year, data=data,
                       markets_cfg=markets_cfg["markets"], tf_label=TF_LABEL)

    if os.path.isdir(SITE):
        try:
            shutil.rmtree(SITE)
        except OSError:
            pass  # delete-protected mounts: files are simply overwritten below
    os.makedirs(SITE, exist_ok=True)
    # static
    for fn in os.listdir(STATIC):
        shutil.copy(os.path.join(STATIC, fn), os.path.join(SITE, fn))

    pages = []  # (url, template, ctx, changefreq, priority)

    glossary = jload(os.path.join(CONTENT, "glossary.json"))
    faq = jload(os.path.join(CONTENT, "faq.json"))
    articles = jload(os.path.join(CONTENT, "articles.json"))
    for a in articles:
        with open(os.path.join(CONTENT, "articles", a["slug"] + ".html"), encoding="utf-8") as f:
            a["body"] = f.read()

    def page(url, tpl, ctx, cf="weekly", pr="0.6"):
        pages.append((url, tpl, ctx, cf, pr))

    # global movers = top 10 across all markets
    movers = []
    for m in markets_cfg["markets"]:
        blk = data["markets"].get(m["id"])
        if blk:
            for s in blk["stocks"]:
                movers.append({**s, "market": m})
    movers.sort(key=lambda x: -x["score"])
    movers = movers[:10]

    page("index.html", "index.j2", {"movers": movers, "faq": faq, "articles": articles}, "daily", "1.0")
    for m in markets_cfg["markets"]:
        blk = data["markets"].get(m["id"])
        page(f"markets/{m['id']}/index.html", "market.j2", {"m": m, "blk": blk}, "daily", "0.9")
    page("learn/index.html", "learn.j2", {"articles": articles}, "weekly", "0.8")
    for a in articles:
        page(f"learn/{a['slug']}/index.html", "article.j2", {"a": a, "articles": articles}, "monthly", "0.7")
    page("glossary/index.html", "glossary.j2", {"glossary": glossary}, "monthly", "0.6")
    page("faq/index.html", "faq.j2", {"faq": faq}, "monthly", "0.6")
    for slug in ["about", "contact", "privacy", "terms", "disclaimer"]:
        with open(os.path.join(CONTENT, slug + ".html"), encoding="utf-8") as f:
            body = f.read()
        meta = jload(os.path.join(CONTENT, "pages.json"))[slug]
        page(f"{slug}/index.html", "page.j2", {"body": body, "meta": meta, "slug": slug}, "yearly", "0.4")
    page("404.html", "404.j2", {}, "yearly", "0.1")

    base = site_cfg["base_url"].rstrip("/")
    for url, tpl, ctx, _, _ in pages:
        canonical = base + "/" + url.replace("index.html", "")
        html = env.get_template(tpl).render(canonical=canonical, path=url, **ctx)
        dest = os.path.join(SITE, url)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as f:
            f.write(html)

    # sitemap
    today = dt.date.today().isoformat()
    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url, _, _, cf, pr in pages:
        if url == "404.html":
            continue
        loc = base + "/" + url.replace("index.html", "")
        sm.append(f"<url><loc>{loc}</loc><lastmod>{today}</lastmod>"
                  f"<changefreq>{cf}</changefreq><priority>{pr}</priority></url>")
    sm.append("</urlset>")
    with open(os.path.join(SITE, "sitemap.xml"), "w") as f:
        f.write("\n".join(sm))
    print(f"rendered {len(pages)} pages -> site/")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true", help="offline sample data")
    ap.add_argument("--render-only", action="store_true")
    args = ap.parse_args()

    site_cfg = jload(os.path.join(CFG, "site.json"))
    markets_cfg = jload(os.path.join(CFG, "markets.json"))
    names = jload(os.path.join(CFG, "names.json"))

    if args.render_only:
        data = jload(os.path.join(DATA, "latest.json"))
    else:
        data = gather(args.seed, site_cfg, markets_cfg, names)
        ok = [k for k, v in data["markets"].items() if not v.get("stale")]
        if not args.seed and not ok:
            print("FATAL: no market fetched successfully — keeping previous site.")
            sys.exit(1)
        jdump(data, os.path.join(DATA, "latest.json"))
    render(data, site_cfg, markets_cfg)


if __name__ == "__main__":
    main()
