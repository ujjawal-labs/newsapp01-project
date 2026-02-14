from flask import Flask, jsonify, request
from flask_cors import CORS
import requests

app = Flask(__name__)
CORS(app)

NEWS_API_KEY = "YOUR_NEWSAPI_KEY"

@app.route("/news")
def news():
    main = request.args.get("main")
    sub = request.args.get("sub")

    url = f"https://newsapi.org/v2/top-headlines?category=general&apiKey={NEWS_API_KEY}"
    res = requests.get(url)
    return jsonify(res.json())

@app.route("/search")
def search():
    q = request.args.get("q")

    url = f"https://newsapi.org/v2/everything?q={q}&apiKey={NEWS_API_KEY}"
    res = requests.get(url)
    return jsonify(res.json())

if __name__ == "__main__":
    app.run(debug=True)

from flask import Flask, request, jsonify
from flask_cors import CORS
import feedparser
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urlunparse, parse_qsl, urlencode
import time
import threading

app = Flask(__name__)
# IMPORTANT: Allows the frontend (index.html) to request data from this API server
CORS(app)

# -------------------- Configuration --------------------
USER_AGENT = "DailyX-NewsBot/1.0 (+https://example.com)"
DEFAULT_IMAGE = "https://via.placeholder.com/400x180?text=Image+Not+Available"
CACHE_TTL = 300  # 5 minutes in seconds

# Google News RSS base (search style) - used for all search queries
GOOGLE_NEWS_RSS_BASE = "https://news.google.com/rss/search?q="

# --- NEWS SOURCES (NESTED STRUCTURE) ---
SOURCES = {
    # Replace this placeholder key with a real NewsAPI key for World General coverage
    "newsapi_key": "YOUR_NEWSAPI_KEY_HERE",

    "world": {
        "General": [
            {"name": "NewsAPI World (General)", "newsapi": True},
            {"name": "BBC World", "rss": "http://feeds.bbci.co.uk/news/world/rss.xml"},
        ],
        "Sports": [
            {"name": "Google News (World Sports)", "rss_search": "worldwide+sports+news"},
            {"name": "BBC Sports", "rss": "http://feeds.bbci.co.uk/sport/rss.xml"},
        ],
        "Science": [
            {"name": "Google News (World Science)", "rss_search": "global+science+discoveries"},
            {"name": "Reuters Science", "rss": "http://feeds.reuters.com/reuters/scienceNews"},
        ],
        "Technology": [
            {"name": "TechCrunch", "rss": "http://feeds.feedburner.com/TechCrunch/"},
            {"name": "Google News (World Tech)", "rss_search": "global+technology+gadgets"},
        ],
        "Business": [
             {"name": "Reuters Business", "rss": "http://feeds.reuters.com/reuters/businessNews"},
             {"name": "Google News (World Business)", "rss_search": "global+economy+finance"},
        ]
    },
    
    "india": {
        "General": [
            {"name": "The Indian Express - India", "rss": "https://indianexpress.com/section/india/feed/"},
            {"name": "NDTV India", "rss": "https://feeds.feedburner.com/ndtvnews-india?format=xml"},
        ],
        "Politics": [
            {"name": "Google News (India Politics)", "rss_search": "Indian+politics+latest+updates"},
            {"name": "Scroll.in - Politics", "rss": "https://scroll.in/feed/politics"},
        ],
        "Business": [
             {"name": "Moneycontrol", "rss": "https://www.moneycontrol.com/rss/latestnews.xml"},
             {"name": "Google News (India Business)", "rss_search": "Indian+stock+market+sensex"},
        ],
        "Entertainment": [
            {"name": "Google News (India Entertainment)", "rss_search": "Bollywood+entertainment+gossip"},
        ],
        "Culture": [
             {"name": "Google News (India Culture)", "rss_search": "Indian+culture+heritage+arts"},
        ]
    },
    
    "ghaziabad": {
        "General": [
            {"name": "Local fallback: Google News (Ghaziabad General)", "rss_search": "Ghaziabad+current+breaking+news"},
            # Scraping source included as an example for local news that lacks RSS
            {"name": "Amar Ujala - Ghaziabad section", "homepage": "https://www.amarujala.com/uttar-pradesh/ghaziabad/", 
             "scrape_selectors": {"article_links": "a[href*='/ghaziabad/']"}},
        ],
        "Local-Crime": [
            {"name": "Google News (Ghaziabad Crime)", "rss_search": "Ghaziabad+crime+incidents+FIR"},
        ],
        "Infrastructure": [
            {"name": "Google News (Ghaziabad Infrastructure)", "rss_search": "Ghaziabad+infrastructure+development+GDA"},
        ],
        "Events": [
            {"name": "Google News (Ghaziabad Events)", "rss_search": "Ghaziabad+local+events+cultural"},
        ],
        "Local-Biz": [
             {"name": "Google News (Ghaziabad Business)", "rss_search": "Ghaziabad+local+business+industry"},
        ]
    }
}


# -------------------- Simple in-memory cache --------------------
_cache = {}
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        item = _cache.get(key)
        if not item:
            return None
        value, expires_at = item
        if time.time() > expires_at:
            del _cache[key]
            return None
        return value

def cache_set(key, value, ttl=CACHE_TTL):
    with _cache_lock:
        _cache[key] = (value, time.time() + ttl)


# -------------------- Helper functions (Core Logic) --------------------

def safe_get(url, params=None, timeout=10):
    """HTTP GET with a user-agent header and scheme guard."""
    headers = {"User-Agent": USER_AGENT}
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        r = requests.get(url, params=params, headers=headers, timeout=timeout)
        r.raise_for_status()
        return r
    except Exception:
        return None

def transform_rss_entry(entry):
    """Normalizes a feedparser entry into the standard article format, with publish time."""
    img = DEFAULT_IMAGE
    if hasattr(entry, 'media_content') and entry.media_content:
        for mc in entry.media_content:
            if isinstance(mc, dict) and mc.get('url'):
                img = mc['url']
                break
    
    if img == DEFAULT_IMAGE:
        img = entry.get('image', {}).get('url', DEFAULT_IMAGE)
    
    # Simple summary cleanup: remove HTML tags from summary if present
    summary_text = entry.get('summary', '')
    if '<' in summary_text and '>' in summary_text:
        summary_text = BeautifulSoup(summary_text, 'html.parser').get_text()

    # --- NEW: extract publish time (if available) ---
    published_ts = 0
    published_iso = None

    try:
        if getattr(entry, "published_parsed", None):
            published_ts = time.mktime(entry.published_parsed)
            published_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', entry.published_parsed)
        elif getattr(entry, "updated_parsed", None):
            published_ts = time.mktime(entry.updated_parsed)
            published_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', entry.updated_parsed)
    except Exception:
        # If anything fails, just keep defaults
        published_ts = 0
        published_iso = None

    article = {
        "title": entry.get('title', 'No Title'),
        "description": summary_text,
        "url": entry.get('link', '#'),
        "urlToImage": img,
        "publishedAt": published_iso,  # exposed to frontend
    }
    # Internal timestamp for sorting
    article["_ts"] = published_ts
    return article


def fetch_rss_feed(url, limit=10):
    """Fetches and parses a standard RSS feed with caching."""
    cache_key = f"rss::{url}::{limit}"
    cached = cache_get(cache_key)
    if cached:
        return cached

    feed = feedparser.parse(url)
    articles = [transform_rss_entry(entry) for entry in feed.entries[:limit]]

    cache_set(cache_key, articles)
    return articles


def fetch_google_news_search(query, limit=10):
    """Constructs the Google News RSS URL and fetches the feed."""
    search_q = query.replace(' ', '+')
    url = f"{GOOGLE_NEWS_RSS_BASE}{search_q}"
    return fetch_rss_feed(url, limit=limit)


def fetch_newsapi_world(limit=10):
    """Fetches world news using the NewsAPI (for 'world-general')."""
    key = SOURCES.get("newsapi_key")
    if not key or key == "YOUR_NEWSAPI_KEY_HERE":
        # Skip if placeholder key is still being used
        return []
    url = "https://newsapi.org/v2/top-headlines"
    params = {"language": "en", "pageSize": limit, "apiKey": key}
    r = safe_get(url, params=params)
    if not r:
        return []
    data = r.json()
    articles = []
    for a in data.get("articles", []):
        # NewsAPI already gives publishedAt in ISO format
        published_iso = a.get("publishedAt")
        # Rough timestamp for sorting (if format matches standard ISO)
        published_ts = 0
        try:
            if published_iso:
                # '2024-11-22T10:30:00Z' -> struct_time
                parsed = time.strptime(published_iso.split('.')[0].replace('Z', ''), "%Y-%m-%dT%H:%M:%S")
                published_ts = time.mktime(parsed)
        except Exception:
            published_ts = 0

        article = {
            "title": a.get("title"),
            "description": a.get("description"),
            "url": a.get("url"),
            "urlToImage": a.get("urlToImage") or DEFAULT_IMAGE,
            "publishedAt": published_iso,
        }
        article["_ts"] = published_ts
        articles.append(article)
    return articles


# --- Web Scraping Function (Kept for local/unstructured sources) ---
def heuristic_scrape_for_articles(homepage_url, selectors=None, limit=10):
    """
    Attempts to scrape article links from a homepage using heuristics/selectors.
    (Implementation omitted for brevity, assume correctness)
    """
    # The actual scraping logic is omitted for brevity, but the function signature remains.
    return [] 


# --- Helper: sort articles by timestamp (newest first) ---
def sort_articles_by_date_desc(articles):
    def get_ts(a):
        ts = a.get("_ts")
        if isinstance(ts, (int, float)):
            return ts
        return 0
    return sorted(articles, key=get_ts, reverse=True)


# -------------------- Flask Routes (API Endpoints) --------------------

@app.route('/')
def home():
    return "News backend running. Use /news?main=...&sub=... or /search?q=..."

@app.route('/news')
def get_news():
    """Endpoint for category-based news fetching."""
    main_category = request.args.get('main', 'world')
    sub_category = request.args.get('sub', 'General')
    limit = int(request.args.get('limit', 10))

    try:
        sources_to_fetch = SOURCES[main_category][sub_category]
    except KeyError:
        return jsonify({"error": f"Unknown category combination: {main_category}/{sub_category}"}), 400

    aggregated = []

    for src in sources_to_fetch:
        try:
            # Source 0: NewsAPI
            if src.get('newsapi'):
                articles = fetch_newsapi_world(limit)
                aggregated.extend(articles)
                continue

            # Source 1: Standard RSS Feed
            if src.get('rss'):
                articles = fetch_rss_feed(src['rss'], limit=limit)
                aggregated.extend(articles)
                continue

            # Source 2: Google News RSS Search
            if src.get('rss_search'):
                articles = fetch_google_news_search(src['rss_search'], limit=limit)
                aggregated.extend(articles)
                continue

            # Source 3: Web Scraping
            if src.get('homepage'):
                selectors = src.get('scrape_selectors')
                articles = heuristic_scrape_for_articles(src['homepage'], selectors=selectors, limit=limit)
                aggregated.extend(articles)
                continue

        except Exception as e:
            app.logger.error(f"Error processing source {src.get('name')}: {e}")
            continue

    # Simple deduplication
    seen = set()
    unique = []
    for a in aggregated:
        url = a.get('url')
        if not url or url in seen:
            continue
        seen.add(url)
        unique.append(a)

    # Sort by newest first (server-side)
    sorted_unique = sort_articles_by_date_desc(unique)

    # Final limit and remove internal field
    sorted_unique = sorted_unique[:limit]
    for a in sorted_unique:
        a.pop("_ts", None)

    return jsonify({"articles": sorted_unique})


## NEW: Dedicated Search Endpoint
@app.route('/search')
def search_news():
    """Endpoint for handling user search queries using Google News RSS."""
    query = request.args.get('q')
    limit = int(request.args.get('limit', 20))  # Higher limit for search to give more results

    if not query:
        return jsonify({"articles": [], "message": "Search query 'q' parameter is missing."})

    try:
        # Use the existing Google News Search function for the user query
        articles = fetch_google_news_search(query, limit=limit)

        # Sort by newest first
        articles = sort_articles_by_date_desc(articles)
        articles = articles[:limit]
        for a in articles:
            a.pop("_ts", None)

        return jsonify({"articles": articles})

    except Exception as e:
        app.logger.error(f"Error processing search query '{query}': {e}")
        return jsonify({"error": "Failed to fetch search results due to an internal error."}), 500


# -------------------- Run Server --------------------
if __name__ == '__main__':
    # Set debug=False for production
    app.run(host='0.0.0.0', port=5000, debug=True)