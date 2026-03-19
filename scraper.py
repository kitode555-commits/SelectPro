#!/usr/bin/env python3
"""Amazon product scraper backend — replaces RapidAPI with direct scraping."""

import json, re, random, time, logging, urllib.parse
from flask import Flask, request, jsonify
from flask_cors import CORS
from bs4 import BeautifulSoup
import requests

import os
app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scraper")

# --------------- config ---------------
DOMAINS = {
    "US": "www.amazon.com", "UK": "www.amazon.co.uk", "DE": "www.amazon.de",
    "JP": "www.amazon.co.jp", "CA": "www.amazon.ca", "FR": "www.amazon.fr",
    "IT": "www.amazon.it", "ES": "www.amazon.es",
}
CURRENCIES = {
    "US": "$", "UK": "£", "DE": "€", "JP": "¥",
    "CA": "CA$", "FR": "€", "IT": "€", "ES": "€",
}
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

def _headers(country="US"):
    domain = DOMAINS.get(country, "www.amazon.com")
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": f"https://{domain}/",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }

session = requests.Session()
session.headers.update({"User-Agent": random.choice(USER_AGENTS)})

def _get(url, country="US", retries=2):
    """Fetch a URL with retry logic."""
    for attempt in range(retries + 1):
        try:
            resp = session.get(url, headers=_headers(country), timeout=15)
            if resp.status_code == 200:
                return resp.text
            if resp.status_code == 503 and attempt < retries:
                time.sleep(random.uniform(2, 4))
                continue
            log.warning(f"HTTP {resp.status_code} for {url}")
            return None
        except Exception as e:
            log.error(f"Request error: {e}")
            if attempt < retries:
                time.sleep(random.uniform(1, 3))
    return None


# --------------- parsers ---------------

def _clean_text(el):
    return el.get_text(strip=True) if el else ""

def _parse_price(text):
    """Extract numeric price from text like '$29.99' or '29,99 €'."""
    if not text:
        return ""
    m = re.search(r'[\d.,]+', text.replace('\xa0', ''))
    if m:
        raw = m.group().replace(',', '.')
        # handle European double-dot: "1.299.00" -> "1299.00"
        parts = raw.split('.')
        if len(parts) > 2:
            raw = ''.join(parts[:-1]) + '.' + parts[-1]
        return raw
    return ""

def parse_search_results(html, country="US"):
    """Parse Amazon search results page."""
    soup = BeautifulSoup(html, "lxml")
    products = []
    items = soup.select('[data-component-type="s-search-result"]')
    for item in items:
        try:
            asin = item.get("data-asin", "")
            if not asin:
                continue

            # title
            title_el = item.select_one("h2 a span") or item.select_one("h2 span")
            title = _clean_text(title_el)

            # url
            link_el = item.select_one("h2 a")
            product_url = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                domain = DOMAINS.get(country, "www.amazon.com")
                product_url = href if href.startswith("http") else f"https://{domain}{href}"

            # image
            img_el = item.select_one("img.s-image")
            image = img_el["src"] if img_el and img_el.get("src") else ""

            # price
            price_el = item.select_one(".a-price .a-offscreen") or item.select_one(".a-price")
            price_text = _clean_text(price_el)
            price_num = _parse_price(price_text)

            # rating
            rating_el = item.select_one('[aria-label*="out of"]') or item.select_one('.a-icon-alt')
            rating_text = _clean_text(rating_el) if rating_el else ""
            rating = ""
            if rating_text:
                rm = re.search(r'([\d.]+)\s*(out of|von|sur|de|di)', rating_text)
                if rm:
                    rating = rm.group(1)
                else:
                    rm2 = re.search(r'([\d.]+)', rating_text)
                    if rm2:
                        rating = rm2.group(1)

            # review count
            reviews_el = item.select_one('[aria-label*="out of"] + span') or item.select_one('.a-size-base.s-underline-text')
            reviews_text = _clean_text(reviews_el) if reviews_el else ""
            reviews = re.sub(r'[^\d]', '', reviews_text) if reviews_text else "0"
            # fallback: look for a link with review count
            if reviews == "0":
                for a in item.select('a'):
                    href = a.get('href', '')
                    if 'customerReviews' in href or '#reviews' in href:
                        t = _clean_text(a)
                        n = re.sub(r'[^\d]', '', t)
                        if n and int(n) > 0:
                            reviews = n
                            break

            # sales / BSR snippet
            sales_el = item.select_one('.a-row.a-size-base .a-color-secondary')
            sales_volume = _clean_text(sales_el) if sales_el else ""

            currency = CURRENCIES.get(country, "$")
            display_price = f"{currency}{price_num}" if price_num else price_text

            products.append({
                "asin": asin,
                "product_title": title,
                "product_url": product_url,
                "product_photo": image,
                "product_price": display_price,
                "product_price_raw": float(price_num) if price_num else 0,
                "product_star_rating": rating,
                "product_num_ratings": int(reviews) if reviews else 0,
                "sales_volume": sales_volume,
            })
        except Exception as e:
            log.warning(f"Parse error on item: {e}")
            continue

    return products


def parse_product_details(html, country="US"):
    """Parse an Amazon product detail page."""
    soup = BeautifulSoup(html, "lxml")
    data = {}

    # title
    title_el = soup.select_one("#productTitle")
    data["product_title"] = _clean_text(title_el)

    # main image
    img_el = soup.select_one("#landingImage") or soup.select_one("#imgBlkFront")
    data["product_photo"] = ""
    if img_el:
        # try data-old-hires first, then src
        data["product_photo"] = img_el.get("data-old-hires", "") or img_el.get("src", "")

    # price
    price_el = (soup.select_one("#priceblock_ourprice") or
                soup.select_one("#priceblock_dealprice") or
                soup.select_one(".a-price .a-offscreen") or
                soup.select_one("#corePrice_feature_div .a-offscreen") or
                soup.select_one("#price_inside_buybox"))
    price_text = _clean_text(price_el)
    price_num = _parse_price(price_text)
    currency = CURRENCIES.get(country, "$")
    data["product_price"] = f"{currency}{price_num}" if price_num else price_text

    # rating
    rating_el = soup.select_one('#acrPopover [aria-label]') or soup.select_one('.a-icon-alt')
    rating_text = ""
    if rating_el:
        rating_text = rating_el.get("aria-label", "") or _clean_text(rating_el)
    rm = re.search(r'([\d.]+)', rating_text)
    data["product_star_rating"] = rm.group(1) if rm else ""

    # review count
    reviews_el = soup.select_one("#acrCustomerReviewText")
    reviews_text = _clean_text(reviews_el)
    rn = re.sub(r'[^\d]', '', reviews_text)
    data["product_num_ratings"] = int(rn) if rn else 0

    # brand / byline
    byline_el = soup.select_one("#bylineInfo") or soup.select_one("#brand")
    data["product_byline"] = _clean_text(byline_el)

    # availability
    avail_el = soup.select_one("#availability span")
    data["product_availability"] = _clean_text(avail_el)

    # sales rank / BSR
    bsr_text = ""
    rank_el = soup.select_one("#SalesRank") or soup.select_one("#detailBulletsWrapper_feature_div")
    if rank_el:
        bsr_text = _clean_text(rank_el)
    else:
        for row in soup.select("tr"):
            th = row.select_one("th")
            td = row.select_one("td")
            if th and ("rank" in _clean_text(th).lower() or "排名" in _clean_text(th)):
                bsr_text = _clean_text(td)
                break
    bsr_match = re.search(r'#?([\d,]+)', bsr_text)
    data["sales_volume"] = f"#{bsr_match.group(1)}" if bsr_match else ""

    # bullet points / features
    features = []
    for li in soup.select("#feature-bullets li span.a-list-item"):
        t = _clean_text(li)
        if t and len(t) > 5:
            features.append(t)
    data["product_features"] = features[:8]

    # description
    desc_el = soup.select_one("#productDescription")
    data["product_description"] = _clean_text(desc_el) if desc_el else ""

    return data


def parse_bestsellers(html, country="US"):
    """Parse Amazon Best Sellers page."""
    soup = BeautifulSoup(html, "lxml")
    products = []

    # New layout: zg-grid items
    items = soup.select('[id^="p13n-asin-index-"]') or soup.select('.zg-grid-general-faceout') or soup.select('.a-list-item .zg-item-immersion')
    if not items:
        # Try another selector for newer pages
        items = soup.select('[data-asin]')

    for idx, item in enumerate(items[:50]):
        try:
            asin = item.get("data-asin", "")
            if not asin:
                # try to extract from link
                link = item.select_one("a[href*='/dp/']")
                if link:
                    m = re.search(r'/dp/([A-Z0-9]{10})', link.get("href", ""))
                    if m:
                        asin = m.group(1)
            if not asin:
                continue

            # title
            title_el = item.select_one("a span div") or item.select_one("._cDEzb_p13n-sc-css-line-clamp-1_1Fn1y") or item.select_one(".p13n-sc-truncate") or item.select_one("a span")
            title = _clean_text(title_el)

            # image
            img_el = item.select_one("img")
            image = img_el.get("src", "") if img_el else ""

            # price
            price_el = item.select_one("._cDEzb_p13n-sc-price_3mJ9Z") or item.select_one(".p13n-sc-price") or item.select_one(".a-price .a-offscreen")
            price_text = _clean_text(price_el)
            price_num = _parse_price(price_text)
            currency = CURRENCIES.get(country, "$")
            display_price = f"{currency}{price_num}" if price_num else price_text

            # rating
            rating_el = item.select_one(".a-icon-alt") or item.select_one('[aria-label*="out of"]')
            rating_text = _clean_text(rating_el) if rating_el else (rating_el.get("aria-label", "") if rating_el else "")
            rm = re.search(r'([\d.]+)', rating_text)
            rating = rm.group(1) if rm else ""

            # review count
            reviews_el = item.select_one("a.a-size-small") or item.select_one(".a-size-small")
            reviews_text = _clean_text(reviews_el) if reviews_el else ""
            rn = re.sub(r'[^\d]', '', reviews_text)

            products.append({
                "asin": asin,
                "product_title": title,
                "product_photo": image,
                "product_price": display_price,
                "product_price_raw": float(price_num) if price_num else 0,
                "product_star_rating": rating,
                "product_num_ratings": int(rn) if rn else 0,
                "rank": idx + 1,
            })
        except Exception as e:
            log.warning(f"Bestseller parse error: {e}")
            continue

    return products


# --------------- category list (static, reliable) ---------------

CATEGORY_MAP = {
    "US": [
        {"id": "16225007011", "name": "Amazon Devices & Accessories"},
        {"id": "2619526011", "name": "Appliances"},
        {"id": "2617942011", "name": "Arts, Crafts & Sewing"},
        {"id": "15690151", "name": "Automotive"},
        {"id": "165797011", "name": "Baby Products"},
        {"id": "15690150", "name": "Beauty & Personal Care"},
        {"id": "283155", "name": "Books"},
        {"id": "2335752011", "name": "Camera & Photo Products"},
        {"id": "15684181", "name": "Cell Phones & Accessories"},
        {"id": "172282", "name": "Electronics"},
        {"id": "1040660", "name": "Grocery & Gourmet Food"},
        {"id": "3760911", "name": "Health & Household"},
        {"id": "1063498", "name": "Home & Kitchen"},
        {"id": "16310101", "name": "Industrial & Scientific"},
        {"id": "228013", "name": "Kitchen & Dining"},
        {"id": "1055398", "name": "Office Products"},
        {"id": "286168", "name": "Patio, Lawn & Garden"},
        {"id": "2619534011", "name": "Pet Supplies"},
        {"id": "10272111", "name": "Software"},
        {"id": "3375251", "name": "Sports & Outdoors"},
        {"id": "468642", "name": "Tools & Home Improvement"},
        {"id": "165793011", "name": "Toys & Games"},
        {"id": "15706941", "name": "Video Games"},
    ],
    "UK": [
        {"id": "77198031", "name": "Baby Products"},
        {"id": "11052671", "name": "Beauty"},
        {"id": "266239", "name": "Books"},
        {"id": "340834031", "name": "Computers & Accessories"},
        {"id": "560800", "name": "Electronics & Photo"},
        {"id": "11052681", "name": "Garden & Outdoors"},
        {"id": "3146281", "name": "Grocery"},
        {"id": "65801031", "name": "Health & Personal Care"},
        {"id": "3146201", "name": "Home & Kitchen"},
        {"id": "2563327011", "name": "Pet Supplies"},
        {"id": "319530011", "name": "Sports & Outdoors"},
        {"id": "712832", "name": "Toys & Games"},
    ],
    "DE": [
        {"id": "78689031", "name": "Baby"},
        {"id": "64257031", "name": "Baumarkt"},
        {"id": "340843031", "name": "Computer & Zubehör"},
        {"id": "569604", "name": "Elektronik & Foto"},
        {"id": "344162031", "name": "Garten"},
        {"id": "10925241", "name": "Haustier"},
        {"id": "3169011", "name": "Küche, Haushalt & Wohnen"},
        {"id": "327473011", "name": "Lebensmittel & Getränke"},
        {"id": "64187031", "name": "Spielzeug"},
        {"id": "16435121", "name": "Sport & Freizeit"},
    ],
    "JP": [
        {"id": "52374051", "name": "DIY・工具・ガーデン"},
        {"id": "2127212051", "name": "おもちゃ"},
        {"id": "2127209051", "name": "ゲーム"},
        {"id": "2127213051", "name": "スポーツ＆アウトドア"},
        {"id": "2127211051", "name": "パソコン・周辺機器"},
        {"id": "2128134051", "name": "ペット用品"},
        {"id": "2127210051", "name": "ホーム＆キッチン"},
        {"id": "2127214051", "name": "家電＆カメラ"},
        {"id": "52391051", "name": "食品・飲料・お酒"},
    ],
}
# Fill other countries with US defaults
for c in DOMAINS:
    if c not in CATEGORY_MAP:
        CATEGORY_MAP[c] = CATEGORY_MAP["US"]


# --------------- routes ---------------

@app.route("/api/search")
def api_search():
    query = request.args.get("query", "")
    country = request.args.get("country", "US")
    page = request.args.get("page", "1")
    if not query:
        return jsonify({"error": "Missing query"}), 400

    domain = DOMAINS.get(country, "www.amazon.com")
    url = f"https://{domain}/s?k={urllib.parse.quote(query)}&page={page}"
    log.info(f"Scraping search: {url}")

    html = _get(url, country)
    if not html:
        return jsonify({"error": "Failed to fetch Amazon — may be rate-limited. Try again."}), 502

    products = parse_search_results(html, country)
    return jsonify({"status": "OK", "data": {"products": products, "total": len(products)}})


@app.route("/api/product-details")
def api_product_details():
    asin = request.args.get("asin", "")
    country = request.args.get("country", "US")
    if not asin:
        return jsonify({"error": "Missing ASIN"}), 400

    domain = DOMAINS.get(country, "www.amazon.com")
    url = f"https://{domain}/dp/{asin}"
    log.info(f"Scraping product: {url}")

    html = _get(url, country)
    if not html:
        return jsonify({"error": "Failed to fetch product page"}), 502

    data = parse_product_details(html, country)
    data["asin"] = asin
    return jsonify({"status": "OK", "data": data})


@app.route("/api/best-sellers")
def api_best_sellers():
    category_id = request.args.get("category_id", "")
    country = request.args.get("country", "US")
    if not category_id:
        return jsonify({"error": "Missing category_id"}), 400

    domain = DOMAINS.get(country, "www.amazon.com")
    url = f"https://{domain}/gp/bestsellers/zgbs/{category_id}"
    # Alternative URL patterns
    alt_url = f"https://{domain}/Best-Sellers/zgbs/ref=zg_bs_nav_0?tf=1&node={category_id}"
    log.info(f"Scraping bestsellers: {url}")

    html = _get(url, country)
    if not html or "captcha" in html.lower():
        html = _get(alt_url, country)

    if not html:
        return jsonify({"error": "Failed to fetch bestsellers"}), 502

    products = parse_bestsellers(html, country)
    return jsonify({"status": "OK", "data": {"best_sellers": products, "total": len(products)}})


@app.route("/api/categories")
def api_categories():
    country = request.args.get("country", "US")
    cats = CATEGORY_MAP.get(country, CATEGORY_MAP["US"])
    return jsonify({"status": "OK", "data": cats})


@app.route("/api/health")
def health():
    return jsonify({"status": "OK", "message": "Scraper is running"})


@app.route("/")
def home():
    return jsonify({"status": "OK", "message": "Amazon Scraper API is running. Endpoints: /api/search, /api/product-details, /api/best-sellers, /api/categories, /api/health"})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port, debug=False)
