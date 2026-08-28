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


def get_html(url, **kw):
    r = get(url, **kw)
    r.encoding = r.apparent_encoding or r.encoding
    return r.text
