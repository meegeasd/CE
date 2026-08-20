"""
Price Fetcher — Multi-source with priority-based selection for Railway deployment.

Architecture:
- Fetch ALL sources ONCE per cycle (parallel)
- Merge results with priority-based conflict resolution
- Cache for 5 minutes
- Proper rate limiting and error handling
"""
from __future__ import annotations
import re
import html
import time
import logging
import requests
import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum

from config.settings import get_settings

logger = logging.getLogger("price_fetcher")

_EXECUTOR = ThreadPoolExecutor(max_workers=8)
_CACHE = {"data": None, "ts": 0.0, "ttl": 300}  # 5 min cache

# Rate limiting for CoinGecko
_COINGECKO_LAST_CALL = 0.0
_COINGECKO_MIN_INTERVAL = 60.0  # 1 call per minute max


class PriceSource(str, Enum):
    TGJU = "tgju"
    DO_L4 = "do_l4"
    YAHOO = "yahoo"
    COINGECKO = "coingecko"
    TSETMC = "tsetmc"


# Symbol definitions with source preferences
SYMBOLS = [
    # (code, name_fa, name_en, unit, primary_source, fallback_sources, priority)
    ("USD", "دلار", "USD", "تومان", PriceSource.TGJU, [PriceSource.DO_L4, PriceSource.YAHOO], 1),
    ("GOLD18", "طلای ۱۸ عیار", "GOLD18", "تومان", PriceSource.TGJU, [PriceSource.DO_L4], 1),
    ("USDT", "تتر", "USDT", "تومان", PriceSource.TGJU, [PriceSource.DO_L4, PriceSource.COINGECKO], 2),
    ("EUR_TMN", "یورو", "EUR", "تومان", PriceSource.TGJU, [PriceSource.DO_L4], 2),
    ("GBP_TMN", "پوند", "GBP", "تومان", PriceSource.TGJU, [PriceSource.DO_L4], 2),
    ("GOLD_MES", "مثقال طلا", "MES", "تومان", PriceSource.TGJU, [PriceSource.DO_L4], 2),
    ("SEKE_NEW", "سکه جدید", "SEKE", "تومان", PriceSource.TGJU, [PriceSource.DO_L4], 3),
    ("XAU", "انس طلا", "XAU", "$", PriceSource.YAHOO, [PriceSource.COINGECKO, PriceSource.TGJU], 2),
    ("XAG", "انس نقره", "XAG", "$", PriceSource.YAHOO, [PriceSource.COINGECKO], 3),
    ("OIL_WTI", "نفت WTI", "WTI", "$", PriceSource.YAHOO, [], 2),
    ("OIL_BRENT", "نفت برنت", "BRENT", "$", PriceSource.YAHOO, [], 2),
    ("EURUSD", "یورو/دلار", "EUR/USD", "$", PriceSource.YAHOO, [], 3),
    ("GBPUSD", "پوند/دلار", "GBP/USD", "$", PriceSource.YAHOO, [], 3),
    ("NAS100", "نزدک ۱۰۰", "NAS100", "$", PriceSource.YAHOO, [], 2),
    ("US30", "داو جونز", "US30", "$", PriceSource.YAHOO, [], 2),
    ("BTC", "بیت‌کوین", "BTC", "$", PriceSource.COINGECKO, [PriceSource.TGJU, PriceSource.DO_L4, PriceSource.YAHOO], 2),
    ("ETH", "اتریوم", "ETH", "$", PriceSource.COINGECKO, [PriceSource.TGJU, PriceSource.DO_L4, PriceSource.YAHOO], 2),
    ("SOL", "سولانا", "SOL", "$", PriceSource.COINGECKO, [PriceSource.TGJU, PriceSource.DO_L4, PriceSource.YAHOO], 3),
    ("NVDA", "انویدیا", "NVDA", "$", PriceSource.YAHOO, [], 3),
    ("AAPL", "اپل", "AAPL", "$", PriceSource.YAHOO, [], 3),
    ("MSFT", "مایکروسافت", "MSFT", "$", PriceSource.YAHOO, [], 3),
]

YAHOO_TICKERS = {
    "XAU": "GC=F", "XAG": "SI=F",
    "OIL_WTI": "CL=F", "OIL_BRENT": "BZ=F",
    "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
    "NAS100": "^NDX", "US30": "^DJI",
    "NVDA": "NVDA", "AAPL": "AAPL", "MSFT": "MSFT",
}

COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "USDT": "tether", "XAU": "tether-gold", "XAG": "silver",
}

TGJU_SYMBOLS = {
    "USD": "price_dollar_rl",
    "EUR_TMN": "price_eur",
    "GBP_TMN": "price_gbp",
    "GOLD18": "price_gold_18",
    "GOLD_MES": "price_gold_mesghal",
    "SEKE_NEW": "price_sekeh",
    "USDT": "price_tether",
    "BTC": "price_bitcoin",
    "ETH": "price_ethereum",
}

DO_L4_DISPATCH = [
    ("بیتکوین", "BTC"), ("بیت کوین", "BTC"),
    ("اتریوم", "ETH"), ("سولانا", "SOL"), ("تتر", "USDT"),
    ("دلار کانادا", "CAD"), ("دلار استرالیا", "AUD"), ("دلار", "USD"),
    ("یورو", "EUR_TMN"), ("پوند", "GBP_TMN"), ("یوان", "CNY"), ("درهم", "AED"),
    ("لیر", "TRY"), ("دینار عراق", "IQD"), ("دینار", "IQD"),
    ("منات", "AZN"), ("افغانی", "AFN"), ("ریال عمان", "OMR"),
    ("سکه جدید", "SEKE_NEW"), ("سکه قدیم", "SEKE_OLD"), ("سکه نیم", "SEKE_HALF"),
    ("سکه ربع", "SEKE_QUARTER"), ("سکه گرمی", "SEKE_GRAM"),
    ("مثقال طلا", "GOLD_MES"), ("گرم ۱۸ عیار", "GOLD18"), ("گرم 18 عیار", "GOLD18"),
    ("اونس", "XAU"), ("انس", "XAU"),
]

_FA_TO_EN = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


@dataclass
class PriceData:
    price: float
    unit: str
    name_fa: str
    name_en: str
    change_pct: Optional[float] = None
    toman_price: Optional[float] = None
    source: PriceSource = PriceSource.YAHOO


def _to_float(s: str) -> Optional[float]:
    if not s:
        return None
    s = s.translate(_FA_TO_EN).replace(",", "").replace("٬", "").strip()
    m = re.match(r'[+-]?\d+(?:\.\d+)?', s)
    return float(m.group(0)) if m else None


def _clean_name(raw: str) -> str:
    name = re.sub(r'[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\U00002600-\U000027BF️‍‌​]+', '', raw)
    name = re.sub(r'\s+', ' ', name).strip()
    name = re.sub(r'\s*\(.*?\)\s*', '', name).strip()
    return name


def _get_meta(code: str) -> tuple:
    for s in SYMBOLS:
        if s[0] == code:
            return s
    return ()


# ─── Source Fetchers (each fetches ALL symbols it supports in ONE call) ─────────

async def _fetch_tgju_all(proxy_variants: List[Optional[dict]]) -> Dict[str, PriceData]:
    """Fetch ALL TGJU symbols in one request."""
    settings = get_settings()
    if not settings.tgju_api_key:
        logger.debug("TGJU API key not configured, skipping")
        return {}

    out = {}
    url = "https://api.tgju.org/v1/market/indicator/summary"
    headers = {**_HEADERS, "Authorization": f"Bearer {settings.tgju_api_key}"}

    for proxies in proxy_variants:
        try:
            proxy_label = proxies["http"] if proxies else "direct"
            logger.info(f"TGJU: trying {proxy_label}...")
            r = await asyncio.get_event_loop().run_in_executor(
                _EXECUTOR,
                lambda: requests.get(url, headers=headers, timeout=10, proxies=proxies)
            )
            r.raise_for_status()
            data = r.json()

            for code, tgju_key in TGJU_SYMBOLS.items():
                indicator = data.get("data", {}).get(tgju_key)
                if indicator and "price" in indicator:
                    price = _to_float(str(indicator["price"]))
                    if price and price > 0:
                        meta = _get_meta(code)
                        out[code] = PriceData(
                            price=price,
                            unit=meta[3] if meta else "تومان",
                            name_fa=meta[1] if meta else code,
                            name_en=meta[2] if meta else code,
                            change_pct=_to_float(str(indicator.get("change", 0))) if indicator.get("change") else None,
                            source=PriceSource.TGJU,
                        )

            if out:
                logger.info(f"TGJU: ✅ {len(out)} symbols via {proxy_label}")
                return out

        except Exception as e:
            logger.warning(f"TGJU: {proxy_label} failed: {str(e)[:100]}")
            continue

    logger.warning("TGJU: all proxy variants failed")
    return out


async def _fetch_do_l4_all(proxy_variants: List[Optional[dict]]) -> Dict[str, PriceData]:
    """Fetch ALL DO_L4 symbols in one request."""
    out = {}

    for proxies in proxy_variants:
        try:
            proxy_label = proxies["http"] if proxies else "direct"
            logger.info(f"DO_L4: trying {proxy_label}...")
            r = await asyncio.get_event_loop().run_in_executor(
                _EXECUTOR,
                lambda: requests.get("https://t.me/s/DO_L4", headers=_HEADERS, timeout=8, proxies=proxies)
            )
            r.raise_for_status()
            html_text = r.text

            msgs = re.findall(r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', html_text, re.DOTALL)
            price_msg = None
            for raw in reversed(msgs):
                msg = re.sub(r'<br\s*/?>', '\n', raw)
                msg = re.sub(r'<[^>]+>', '', msg)
                msg = html.unescape(msg).strip()
                if "نرخ فروش" in msg and "دلار:" in msg and len(msg) > 500:
                    price_msg = msg
                    break

            if not price_msg:
                logger.warning(f"DO_L4: no price message found in {proxy_label}")
                continue

            for line in price_msg.splitlines():
                line = line.strip()
                if not line or line.startswith(("نرخ فروش", "⌚", "آخرین", "بروزرسانی")):
                    continue
                if line.startswith("💰") and "@" in line:
                    continue

                m = re.match(
                    r'^\s*(?:[\U0001F300-\U0001FAFF\U00002600-\U000027BF🇦-🇿]{1,4}\s*)?'
                    r'(?P<name>[^:]+?)\s*:\s*'
                    r'(?P<price>[\d۰-۹٠-٩.,]+)'
                    r'(?P<rest>.*)$',
                    line, re.VERBOSE,
                )
                if not m:
                    continue

                price = _to_float(m.group("price"))
                if price is None:
                    continue

                rest = m.group("rest") or ""
                name = m.group("name").replace("‌", "").strip().lower()
                chg = None
                pct_m = re.search(r'%([\d۰-۹٠-٩.]+)([+\-])', rest)
                if pct_m:
                    chg = _to_float(pct_m.group(1))
                    if chg is not None and pct_m.group(2) == "-":
                        chg = -chg
                if chg is None and "بدون تغییر" in rest:
                    chg = 0.0

                unit = "تومان" if ("تومان" in rest or "هـ.تومان" in rest) else ("$" if "دلار" in rest else None)

                for needle, code in DO_L4_DISPATCH:
                    if needle.replace("‌", "") in name:
                        if code in out:
                            break
                        name_fa = _clean_name(m.group("name"))
                        meta = _get_meta(code)
                        out[code] = PriceData(
                            price=price,
                            unit=unit or (meta[3] if meta else "تومان"),
                            name_fa=name_fa,
                            name_en=meta[2] if meta else code,
                            change_pct=chg,
                            source=PriceSource.DO_L4,
                        )
                        break

            if out:
                logger.info(f"DO_L4: ✅ {len(out)} symbols via {proxy_label}")
                return out

        except Exception as e:
            logger.warning(f"DO_L4: {proxy_label} failed: {str(e)[:100]}")
            continue

    logger.warning("DO_L4: all proxy variants failed")
    return out


async def _fetch_yahoo_all(proxy_variants: List[Optional[dict]]) -> Dict[str, PriceData]:
    """Fetch ALL Yahoo symbols in one batch - optimized parallel."""
    out = {}

    for proxies in proxy_variants:
        proxy_label = proxies["http"] if proxies else "direct"
        logger.info(f"Yahoo: trying {proxy_label}...")
        partial_out = {}

        # Fetch all tickers in parallel with timeout
        tasks = {}
        for code, ticker in YAHOO_TICKERS.items():
            if code in out:
                continue
            tasks[code] = asyncio.create_task(_fetch_yahoo_single(ticker, proxies))

        if tasks:
            # Wait for all with overall timeout
            try:
                done, pending = await asyncio.wait(
                    tasks.values(), timeout=20, return_when=asyncio.ALL_COMPLETED
                )
                # Cancel any pending
                for p in pending:
                    p.cancel()

                # Map results back
                code_list = list(tasks.keys())
                for i, task in enumerate(done):
                    code = code_list[i]
                    try:
                        result = task.result()
                        if result and not isinstance(result, Exception):
                            meta = _get_meta(code)
                            partial_out[code] = PriceData(
                                price=result["price"],
                                unit="$",
                                name_fa=meta[1] if meta else code,
                                name_en=meta[2] if meta else code,
                                change_pct=result["change_pct"],
                                source=PriceSource.YAHOO,
                            )
                    except Exception:
                        pass
            except asyncio.TimeoutError:
                logger.warning(f"Yahoo: timeout for {proxy_label}")
                for t in tasks.values():
                    t.cancel()

        if partial_out:
            out.update(partial_out)
            logger.info(f"Yahoo: ✅ {len(partial_out)} new symbols via {proxy_label}")
            if len(out) >= len(YAHOO_TICKERS) - 1:
                return out

    logger.warning(f"Yahoo: total {len(out)} symbols from all variants")
    return out


async def _fetch_yahoo_single(ticker: str, proxies: Optional[dict]) -> Optional[dict]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
    for attempt in range(2):
        try:
            r = await asyncio.get_event_loop().run_in_executor(
                _EXECUTOR,
                lambda: requests.get(url, headers=_HEADERS, timeout=10, proxies=proxies)
            )
            if r.status_code == 429:
                await asyncio.sleep(2)
                continue
            r.raise_for_status()
            data = r.json()
            result = data.get("chart", {}).get("result", [None])[0]
            if not result:
                return None
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            prev = meta.get("chartPreviousClose") or meta.get("previousClose")
            if price is None:
                return None
            chg = None
            if prev and prev > 0:
                chg = ((price - prev) / prev) * 100
            return {"price": float(price), "change_pct": chg}
        except Exception as e:
            if attempt < 1:
                await asyncio.sleep(0.5)
            else:
                logger.debug(f"Yahoo {ticker} failed: {str(e)[:60]}")
    return None


async def _fetch_coingecko_all(proxy_variants: List[Optional[dict]]) -> Dict[str, PriceData]:
    """Fetch ALL CoinGecko symbols in one request with rate limiting."""
    global _COINGECKO_LAST_CALL
    settings = get_settings()
    out = {}

    # Rate limiting
    now = time.time()
    if now - _COINGECKO_LAST_CALL < _COINGECKO_MIN_INTERVAL:
        wait_time = _COINGECKO_MIN_INTERVAL - (now - _COINGECKO_LAST_CALL)
        logger.debug(f"CoinGecko: rate limited, waiting {wait_time:.1f}s")
        await asyncio.sleep(wait_time)

    cg_symbols = {k: v for k, v in COINGECKO_IDS.items() if k in ["BTC", "ETH", "SOL", "USDT", "XAU", "XAG"]}
    ids = ",".join(cg_symbols.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true"
    headers = _HEADERS.copy()
    if settings.coingecko_api_key:
        headers["x-cg-pro-api-key"] = settings.coingecko_api_key

    for proxies in proxy_variants:
        try:
            proxy_label = proxies["http"] if proxies else "direct"
            logger.info(f"CoinGecko: trying {proxy_label}...")
            _COINGECKO_LAST_CALL = time.time()

            r = await asyncio.get_event_loop().run_in_executor(
                _EXECUTOR,
                lambda: requests.get(url, headers=headers, timeout=10, proxies=proxies)
            )
            if r.status_code == 429:
                logger.warning("CoinGecko: rate limited (429)")
                await asyncio.sleep(5)
                continue
            r.raise_for_status()
            data = r.json()

            for code, cg_id in cg_symbols.items():
                if code in out:
                    continue
                cg_data = data.get(cg_id)
                if cg_data and "usd" in cg_data:
                    price = float(cg_data["usd"])
                    chg = cg_data.get("usd_24h_change")
                    meta = _get_meta(code)
                    out[code] = PriceData(
                        price=price,
                        unit="$",
                        name_fa=meta[1] if meta else code,
                        name_en=meta[2] if meta else code,
                        change_pct=chg,
                        source=PriceSource.COINGECKO,
                    )

            if out:
                logger.info(f"CoinGecko: ✅ {len(out)} symbols via {proxy_label}")
                return out

        except Exception as e:
            logger.warning(f"CoinGecko: {proxy_label} failed: {str(e)[:100]}")
            continue

    logger.warning("CoinGecko: all proxy variants failed")
    return out


async def _fetch_tsetmc_all(proxy_variants: List[Optional[dict]]) -> Dict[str, PriceData]:
    """Fetch from TSETMC - placeholder."""
    settings = get_settings()
    if not settings.tsetmc_api_key:
        return {}
    return {}


# ─── Source Map ───────────────────────────────────────────────────────────────
_SOURCE_FETCHERS = {
    PriceSource.TGJU: _fetch_tgju_all,
    PriceSource.DO_L4: _fetch_do_l4_all,
    PriceSource.YAHOO: _fetch_yahoo_all,
    PriceSource.COINGECKO: _fetch_coingecko_all,
    PriceSource.TSETMC: _fetch_tsetmc_all,
}


# ─── Main Fetch Function ─────────────────────────────────────────────────────
async def fetch_all_prices() -> Dict[str, Any]:
    """
    Fetch all prices using priority-based source selection.
    Strategy:
    1. Fetch ALL sources in parallel (one call per source)
    2. For each symbol, pick the highest-priority source that succeeded
    3. Cache for 5 minutes
    """
    settings = get_settings()

    now = time.time()
    if _CACHE["data"] and (now - _CACHE["ts"]) < _CACHE["ttl"]:
        logger.debug("Returning cached prices")
        return _CACHE["data"]

    proxy_variants = settings.proxy_variants
    logger.info(f"Will try {len(proxy_variants)} proxy variants")

    # Get priority sources from settings
    priority_sources = []
    for src_name in settings.price_source_priority:
        try:
            priority_sources.append(PriceSource(src_name))
        except ValueError:
            logger.warning(f"Unknown price source: {src_name}")

    # ─── Step 1: Fetch ALL sources in parallel (ONE call per source) ───
    logger.info("Fetching all sources in parallel...")
    fetch_tasks = {}
    for src in priority_sources:
        fetcher = _SOURCE_FETCHERS.get(src)
        if fetcher:
            fetch_tasks[src] = asyncio.create_task(fetcher(proxy_variants))

    # Wait for all sources with overall timeout
    source_results = {}
    if fetch_tasks:
        try:
            done, pending = await asyncio.wait(
                fetch_tasks.values(), timeout=35, return_when=asyncio.ALL_COMPLETED
            )
            for p in pending:
                p.cancel()
                try:
                    await p
                except asyncio.CancelledError:
                    pass

            # Map results
            src_list = list(fetch_tasks.keys())
            for i, task in enumerate(done):
                src = src_list[i]
                try:
                    result = task.result()
                    source_results[src] = result if result else {}
                    logger.info(f"{src.value}: got {len(source_results[src])} symbols")
                except Exception as e:
                    logger.warning(f"{src.value}: failed: {e}")
                    source_results[src] = {}
        except asyncio.TimeoutError:
            logger.warning("Overall fetch timeout after 35s")
            for t in fetch_tasks.values():
                t.cancel()

    # ─── Step 2: Merge with priority ───
    final_prices: Dict[str, PriceData] = {}
    source_stats: Dict[str, int] = {}

    for symbol_meta in SYMBOLS:
        code = symbol_meta[0]
        primary_src = symbol_meta[4]
        fallback_srcs = symbol_meta[5]

        # Build ordered source list for this symbol
        symbol_sources = [primary_src] + fallback_srcs
        for src in priority_sources:
            if src not in symbol_sources:
                symbol_sources.append(src)

        # Try each source for this symbol
        for src in symbol_sources:
            if code in final_prices:
                break
            if src in source_results and code in source_results[src]:
                final_prices[code] = source_results[src][code]
                source_stats[src.value] = source_stats.get(src.value, 0) + 1
                logger.debug(f"{code}: got from {src.value}")
                break

    # ─── Step 3: Enrich $ prices with toman equivalent ───
    usd_price = final_prices.get("USD", {}).price if "USD" in final_prices else None
    if usd_price and usd_price > 0:
        for code, p in final_prices.items():
            if p.unit == "$" and p.toman_price is None:
                p.toman_price = p.price * usd_price

    # ─── Step 4: Convert to serializable format ───
    result_prices = {}
    for code, p in final_prices.items():
        result_prices[code] = {
            "price": p.price,
            "unit": p.unit,
            "name_fa": p.name_fa,
            "name_en": p.name_en,
            "change_pct": p.change_pct,
            "toman_price": p.toman_price,
            "source": p.source.value,
        }

    source_summary = ", ".join(f"{src}({cnt})" for src, cnt in source_stats.items())
    result = {
        "prices": result_prices,
        "fetched_at": datetime.now(),
        "source": source_summary,
        "total": len(result_prices),
    }

    _CACHE["data"] = result
    _CACHE["ts"] = now
    logger.info(f"Total: {len(result_prices)} prices from {source_summary}")
    return result


def clear_cache():
    _CACHE["data"] = None
    _CACHE["ts"] = 0.0


# ─── Standalone test ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    t0 = time.time()
    result = asyncio.run(fetch_all_prices())
    print(f"\nFetched {result['total']} prices in {time.time()-t0:.1f}s")
    print(f"Source: {result['source']}")
    for code, p in result["prices"].items():
        src = p.get("source", "?")
        chg = p.get("change_pct")
        chg_str = f" ({chg:+.2f}%)" if chg is not None else ""
        print(f"  {code:10} {p['name_fa']:15} {p['price']:>15,.2f} {p['unit']}{chg_str} [{src}]")