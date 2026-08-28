"""HTTP取得まわりの共通処理。"""
import time
import requests

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://www.google.com/",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "cross-site",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Not/A)Brand";v="8", "Chromium";v="126", "Google Chrome";v="126"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}


def get(url, session=None, retries=3, wait=5, timeout=40, **kw):
    """GETして requests.Response を返す。失敗はリトライ。"""
    sess = session or requests.Session()
    last = None
    for i in range(retries):
        try:
            r = sess.get(url, headers=HEADERS, timeout=timeout, **kw)
            if r.status_code == 200:
                return r
            last = RuntimeError(f"HTTP {r.status_code} for {url}")
        except Exception as e:  # noqa: BLE001
            last = e
        time.sleep(wait * (i + 1))
    raise last


_PW = {"browser": None, "ctx": None}


def playwright_ctx():
    """共有のヘッドレスChromiumコンテキスト(遅延起動)。"""
    from playwright.sync_api import sync_playwright
    if _PW["browser"] is None:
        _PW["pw"] = sync_playwright().start()
        _PW["browser"] = _PW["pw"].chromium.launch(headless=True)
        _PW["ctx"] = _PW["browser"].new_context(
            user_agent=UA, locale="ja-JP",
            viewport={"width": 1280, "height": 900})
        # 画像・フォント・動画は読まない(高速化とタイムアウト回避)
        _PW["ctx"].route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in ("image", "font", "media")
            else route.continue_())
    return _PW["ctx"]


def _playwright_html(url):
    """403等でHTTP取得できないサイト向け: ヘッドレスChromiumで取得。"""
    last = None
    for attempt in range(2):
        page = playwright_ctx().new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)  # Cloudflare等のJSチャレンジ待ち
            return page.content()
        except Exception as e:  # noqa: BLE001
            last = e
        finally:
            page.close()
        time.sleep(3)
    raise last


def get_html(url, **kw):
    try:
        r = get(url, **kw)
        r.encoding = r.apparent_encoding or r.encoding
        return r.text
    except Exception:
        # ブロックされた場合は実ブラウザで再試行(playwright未導入ならそのまま失敗)
        try:
            import playwright  # noqa: F401
        except ImportError:
            raise
        return _playwright_html(url)
