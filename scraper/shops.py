"""各買取業者のスクレイパー。

各関数は offers のリストを返す:
    {"text": 商品名+状態がわかる文字列, "price": int}
text は normalize.looks_unopened() と Normalizer.parse() にかけられる。
新品未開封・SIMフリー相当だけを拾うのが理想だが、迷ったら広めに返してよい
(同一item keyには最高値が採用される。中古/開封済は text に含まれる語で弾かれる)。
"""
import json
import re

from bs4 import BeautifulSoup

from .fetch import get, get_html

REGISTRY = {}


class SkipShop(Exception):
    """認証情報未設定などで今回はこの業者をスキップする(エラー扱いにしない)。"""


def shop(shop_id):
    def deco(fn):
        REGISTRY[shop_id] = fn
        return fn
    return deco


from .normalize import COLOR_WORDS  # noqa: E402

_ALL_COLOR_WORDS = sorted({w for ws in COLOR_WORDS.values() for w in ws},
                          key=len, reverse=True)
_ALT = "|".join(re.escape(w) for w in _ALL_COLOR_WORDS)
_DEDUCT_RE = re.compile(
    rf"((?:{_ALT})(?:\s*[/・]\s*(?:{_ALT}))*)\s*[-−▲]\s*([0-9,]+)", re.I)
_WORD_RE = re.compile(_ALT, re.I)
_ABS_RE = re.compile(rf"({_ALT})\s+([0-9]{{2,3}},[0-9]{{3}})", re.I)


def expand_color_deductions(text, base_price):
    """カラー別の価格差表記(「橙-6500」=減額 /「橙 178,000」=絶対額)を検出し、
    offer に ov(カラー語→そのカラーの価格)として付与する。
    どのカラーに対応するかの解決は runner 側(機種のカラー定義に基づく)。"""
    ov = {}
    for m in _DEDUCT_RE.finditer(text):
        # 「ブルー/ブラック -2,000」のような複数色まとめ表記にも対応
        for word in _WORD_RE.findall(m.group(1)):
            ov[word] = base_price - int(m.group(2).replace(",", ""))
    for m in _ABS_RE.finditer(text):
        ov[m.group(1)] = int(m.group(2).replace(",", ""))
    clean = _ABS_RE.sub("", _DEDUCT_RE.sub("", text)).strip()
    offer = {"text": clean, "price": base_price}
    if ov:
        offer["ov"] = ov
    return [offer]


def _soup(html):
    return BeautifulSoup(html, "lxml")


# ---------------------------------------------------------------- 海峡通信
@shop("kaikyo")
def kaikyo():
    """モバイル一番(海峡通信)。カテゴリページに新品/中古価格のカード。"""
    offers = []
    for cat in ("37", "36", "35", "34"):  # ProMax / Pro / Air / 17
        html = get_html(f"https://www.mobile-ichiban.com/Prod/1/01/{cat}")
        soup = _soup(html)
        for lb in soup.select("label[id^=NewPrice_]"):
            node = lb
            title = None
            for _ in range(8):
                node = node.parent
                if node is None:
                    break
                t = node.find(attrs={"title": re.compile("iPhone")})
                if t:
                    title = t
                    break
            if not title or node is None:
                continue
            text = re.sub(r"\s+", " ", node.get_text(" ", strip=True))
            # カード全文には「simfree未開封」「橙-6500」等の条件が含まれる
            from .normalize import yen
            price = yen(lb.get_text(strip=True))
            if not price:
                continue
            title = text.split("新品")[0].strip()
            offers.extend(expand_color_deductions(title, price))
    return offers


# ---------------------------------------------------------------- 買取エノキング
def _enoking_creds():
    import os
    email = os.environ.get("ENOKING_EMAIL")
    password = os.environ.get("ENOKING_PASSWORD")
    if not (email and password):
        path = os.path.join(os.path.dirname(__file__), "..", "enoking_login.txt")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            if len(lines) >= 2:
                email, password = lines[0], lines[1]
    return email, password


@shop("enoking")
def enoking():
    """会員ログイン必須(Cloudflare保護)。Playwrightでログインして取得。
    認証情報は ENOKING_EMAIL / ENOKING_PASSWORD か enoking_login.txt(1行目メール, 2行目パスワード)。
    """
    email, password = _enoking_creds()
    if not (email and password):
        raise SkipShop("エノキングの認証情報が未設定")
    from .fetch import playwright_ctx
    cats = {  # カテゴリID(サイトのナビから取得)
        "17promax": "ef369420-782f-4343-a8fe-12cc875842ab",
        "17pro": "a97f234d-feb0-4c64-ad65-1c1174f43f12",
        "17": "1f133399-5164-42a5-83e5-370e3e882f0f",
        "17air": "0bb59efb-68a5-4b2e-b380-fca0fc593a88",
    }
    ctx = playwright_ctx()
    page = ctx.new_page()
    offers = []
    try:
        # ---- ログイン(既にセッションがあればスキップされる) ----
        page.goto("https://newenoking-kaitori.com/login",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)
        if page.locator("#email").count():
            page.fill("#email", email)
            page.fill("#password", password)
            page.click("button:has-text('ログイン')")
            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:  # noqa: BLE001
                pass
            page.wait_for_timeout(3000)
            body = page.content()
            if "パスワード" in body and page.locator("#email").count():
                # まだログインフォームが出ている = 失敗
                err = ""
                for sel in (".error", "[class*=error]", "[role=alert]"):
                    if page.locator(sel).count():
                        err = page.locator(sel).first.inner_text()[:100]
                        break
                raise RuntimeError(f"エノキングにログインできませんでした"
                                   f"(url={page.url} エラー表示={err or 'なし'})")
        # ---- カテゴリごとに価格取得 ----
        for cat_id in cats.values():
            page.goto(f"https://newenoking-kaitori.com/products?cat={cat_id}",
                      wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3000)
            soup = _soup(page.content())
            for el in soup.find_all(string=re.compile(r"iPhone ?(17|Air)")):
                name = el.strip()
                if len(name) < 12 or "GB" not in name and "TB" not in name:
                    continue
                # ¥金額をちょうど1つ含む最近傍の祖先をカードとみなす(ホムラ方式)
                node, price = el.parent, None
                for _ in range(7):
                    if node is None:
                        break
                    amounts = re.findall(r"[¥￥]\s*([0-9][0-9,]{4,})|([0-9]{2,3},[0-9]{3})\s*円",
                                         node.get_text(" ", strip=True))
                    flat = [a or b for a, b in amounts]
                    if len(flat) == 1:
                        price = flat[0]
                        break
                    if len(flat) > 1:
                        break
                    node = node.parent
                if price:
                    offers.append({"text": name, "price": price + "円",
                                   "cond": "新品未開封"})
        if not offers:
            # 診断情報付きで失敗させる(ログインは通ったが価格が見えない等)
            html = page.content()
            n_names = len(re.findall(r"iPhone ?17|iPhone ?Air", html))
            raise RuntimeError(
                f"エノキング: 価格を抽出できず (url={page.url} "
                f"商品名={n_names}件 問い合わせ表示={'問い合わせ' in html} "
                f"ログアウト表示={'ログアウト' in html})")
    finally:
        page.close()
    return offers


# ---------------------------------------------------------------- 買取一丁目
@shop("ichome")
def ichome():
    """SPAだがJSON API(/api/keitai/listPage)が直接叩ける。"""
    offers = []
    for kw in ("iPhone 17", "iPhone Air"):
        import urllib.parse
        url = ("https://www.1-chome.com/api/keitai/listPage?accCode=&page=1&size=200"
               f"&keyword={urllib.parse.quote(kw)}&isImpo=false&isCampaign=false"
               "&cateCode=RGNg976kptBN7UjF&kbNames=&cateName=&isImpoCate=false")
        data = get(url).json()
        for item in data.get("data", {}).get("content", []):
            if item.get("kbName") not in (None, "新品"):
                continue
            title = item.get("title", "")
            desc = item.get("kbDesc") or ""
            for det in item.get("goodsKbDetails", []):
                if det.get("kbDetailName") != "未開封":
                    continue
                p = det.get("kbDetailPrice") or det.get("maxPrice")
                if not p:
                    continue
                for of in expand_color_deductions(f"{title} {desc}", int(p)):
                    of["cond"] = "未開封"
                    offers.append(of)
    return offers


# ---------------------------------------------------------------- トゥインクルモバイル
@shop("twinkle")
def twinkle():
    """Next.js SSR。カテゴリ1(iPhone)の一覧テキストから 未開封品 価格を抽出。"""
    html = get_html("https://twinkle-mobile.com/shop/products/category/0/1?page=1&pageSize=200")
    soup = _soup(html)
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    offers = []
    for m in re.finditer(
            r"(iPhone[^:]{3,60}?)\s*JANコード:\s*\d+\s*(?:買取強化\s*)?未開封品\s*([\d,]+)\s*円",
            text):
        offers.append({"text": m.group(1).strip(), "price": m.group(2) + "円",
                       "cond": "未開封"})
    return offers


# ---------------------------------------------------------------- 森森買取
@shop("morimori")
def morimori():
    """機種別カテゴリの div.product-item。通常買取価格を採用。"""
    import time
    offers = []
    for cat in ("0301066", "0301065", "0301064", "0301063"):  # PM/Pro/Air/17
        for page in (1, 2, 3):
            time.sleep(1.2)  # 連続アクセスで接続拒否されることがあるため間隔を空ける
            url = f"https://www.morimori-kaitori.jp/category/{cat}?page={page}"
            soup = _soup(get_html(url))
            items = soup.select("div.product-item")
            for it in items:
                txt = re.sub(r"\s+", " ", it.get_text(" ", strip=True))
                m = re.search(r"通常買取価格[::]\s*([\d,]+)円", txt)
                if not m or "iPhone" not in txt:
                    continue
                name = txt.split("JAN")[0].split("通常買取価格")[0]
                offers.append({"text": name, "price": m.group(1) + "円",
                               "cond": "新品未開封"})
            if not items:
                break
    return offers


# ---------------------------------------------------------------- モバイルミックス
@shop("mix")
def mix():
    """category=7(iPhone未開封)。td.product+td.price の行、次行に 未開封/色。"""
    soup = _soup(get_html("https://mobile-mix.jp/?category=7"))
    offers = []
    for tr in soup.select("table.list tr"):
        prod = tr.select_one("td.product")
        price = tr.select_one("td.price")
        if not prod or not price:
            continue
        detail = ""
        nxt = tr.find_next_sibling("tr")
        if nxt and not nxt.select_one("td.product"):
            detail = nxt.get_text(" ", strip=True)
            detail = re.sub(r"買取申込.*", "", detail)
        colors = "" if ("全色" in detail or "全部" in detail) else detail.replace("未開封", "")
        from .normalize import yen
        p = yen(price.get_text(strip=True))
        if not p:
            continue
        for of in expand_color_deductions(f"{prod.get_text(' ', strip=True)} {colors}", p):
            of["cond"] = "未開封"
            offers.append(of)
    return offers


# ---------------------------------------------------------------- 買取商店
@shop("shouten")
def shouten():
    """/keitai ページに全機種のカード(カラー別)。新品/中古の2価格、先頭が新品。"""
    soup = _soup(get_html("https://www.kaitorishouten-co.jp/keitai"))
    offers = []
    seen = set()
    for el in soup.select("h4.item-title"):
        name = el.get_text(" ", strip=True)
        if "iPhone" not in name:
            continue
        node = el
        prices = []
        for _ in range(8):
            node = node.parent
            if node is None:
                break
            prices = node.select(".item-price")
            if prices:
                break
        if not prices:
            continue
        from .normalize import yen
        p = yen(prices[0].get_text(strip=True))
        if not p or (name, p) in seen:
            continue
        seen.add((name, p))
        offers.append({"text": name, "price": p, "cond": "新品未開封"})
    return offers


# ---------------------------------------------------------------- 買取ベストワン
@shop("best1")
def best1():
    """keitai.html の価格表: 機種 / 備考(色減額) / 新品価格 / 中古価格。"""
    soup = _soup(get_html("https://top1mobile.net/keitai.html"))
    offers = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 4:
            continue
        name = tds[0].get_text(" ", strip=True)
        if "iphone" not in name.lower():
            continue
        notes = tds[1].get_text(" ", strip=True)
        from .normalize import yen
        p = yen(tds[2].get_text(strip=True))
        if not p:
            continue
        deds = " ".join(m.group(0) for m in _DEDUCT_RE.finditer(notes))
        for of in expand_color_deductions(f"{name} {deds}".strip(), p):
            of["cond"] = "新品未開封"
            offers.append(of)
    return offers


# ---------------------------------------------------------------- 買取ホムラ
@shop("homura")
def homura():
    """Ransack検索(q[name_cont])で商品カード。【未開封】/【開封open】が名前に付く。"""
    import urllib.parse
    offers = []
    for query in ("iPhone 17", "iPhone Air"):
        for page in range(1, 8):
            params = {"q[name_cont]": query, "page": page}
            url = "https://kaitori-homura.com/products?" + urllib.parse.urlencode(params)
            soup = _soup(get_html(url))
            found = 0
            for h5 in soup.find_all("h5"):
                name = h5.get_text(" ", strip=True)
                if "iPhone" not in name:
                    continue
                # 同一商品がグリッド/リストの2ビューに出るため、
                # ¥金額をちょうど1つ含む最近傍の祖先だけを商品カードとみなす
                node, price = h5, None
                for _ in range(6):
                    node = node.parent
                    if node is None:
                        break
                    amounts = re.findall(r"[¥￥]\s*([\d,]+)", node.get_text(" ", strip=True))
                    if len(amounts) == 1:
                        price = amounts[0]
                        break
                    if len(amounts) > 1:
                        break
                if not price:
                    continue
                offers.append({"text": name, "price": price + "円"})
                found += 1
            if found < 40:
                break
    return offers


# ---------------------------------------------------------------- 買取スター
@shop("star")
def star():
    """prt-name(機種) / prt-name(備考: 開封価格+色減額) / prt-price(未開封価格)。"""
    soup = _soup(get_html("https://www.kaitoristar.com/mobile"))
    offers = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 3 or "iPhone" not in tds[0].get_text():
            continue
        name = tds[0].get_text(" ", strip=True)
        notes = tds[1].get_text(" ", strip=True)
        price_txt = tds[2].get_text(strip=True)
        from .normalize import yen
        p = yen(price_txt)
        if not p:
            continue
        deds = " ".join(m.group(0) for m in _DEDUCT_RE.finditer(notes))
        for of in expand_color_deductions(f"{name} {deds}".strip(), p):
            of["cond"] = "新品未開封"
            offers.append(of)
    return offers


# ---------------------------------------------------------------- ケータイゴッド
@shop("god")
def god():
    """price/index.php?ci=11&mi=N の #price_table。APPLEストア版が最高値。"""
    offers = []
    for mi in ("451", "452", "454", "453"):  # PM / Pro / 17 / Air
        html = get_html(f"https://keitai-god.com/price/index.php?ci=11&mi={mi}")
        for name, price in re.findall(
                r"<td>(iPhone[^<]+)</td>\s*<td>([\d,]+)円</td>", html):
            offers.append({"text": name.strip(), "price": price + "円",
                           "cond": "新品未開封"})
    return offers


# ---------------------------------------------------------------- ドラゴンモバイル
@shop("dragon")
def dragon():
    """sell.html リスト。.splist1=機種名, .hot4=新品/中古価格。"""
    offers = []
    for page in range(1, 5):
        url = f"https://mobileone.co.jp/sell.html?cat1=140&cat2=144&page={page}"
        soup = _soup(get_html(url))
        items = soup.select("div.list2 li")
        for li in items:
            name = li.select_one(".splist1")
            if not name:
                continue
            price = None
            for hot in li.select(".hot4"):
                t = hot.get_text(" ", strip=True)
                if "新品" in t:
                    price = t
                    break
            if not price:
                continue
            offers.append({"text": name.get_text(" ", strip=True) + " 新品未開封",
                           "price": price})
        if not items:
            break
    return offers


# ---------------------------------------------------------------- ゲストモバイル
@shop("guest")
def guest():
    """価格表テーブル: 機種 / 備考(色減額) / 新品未開封 / 新品未使用。"""
    url = "https://www.guestmobile.jp/iphone%e8%b2%b7%e5%8f%96%e4%be%a1%e6%a0%bc/"
    soup = _soup(get_html(url))
    offers = []
    for tr in soup.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) != 4:
            continue
        name = tds[0].get_text(" ", strip=True)
        if "iPhone" not in name:
            continue
        notes = tds[1].get_text(" ", strip=True)
        price = tds[2].get_text(strip=True)
        from .normalize import yen
        p = yen(price)
        if not p:
            continue
        offers.extend(expand_color_deductions(f"{name} 未開封 {notes}", p))
    return offers


# ---------------------------------------------------------------- 買取楽園
@shop("rakuen")
def rakuen():
    """WooCommerce。カード名に未開封/開封済みとカラー別絶対額が入る。"""
    offers = []
    for page in ("", "page/2/", "page/3/", "page/4/"):
        url = f"https://www.keitairakuen.com/product-category/keitai/iphone/{page}"
        soup = _soup(get_html(url))
        for card in soup.select("div.box-text"):
            txt = re.sub(r"\s+", " ", card.get_text(" ", strip=True))
            m = re.search(r"新品:\s*[¥￥]\s*([\d,]+)", txt)
            if not m or "iPhone" not in txt:
                continue
            base = int(m.group(1).replace(",", ""))
            title = txt.split("新品:")[0].replace("クイックビュー", "").strip()
            offers.extend(expand_color_deductions(title, base))
    return offers


# ---------------------------------------------------------------- 携帯空間
@shop("space")
def space():
    """product-card に未開封品価格。se=24(17シリーズ), 40(Air)。"""
    offers = []
    for se in ("24", "40"):
        soup = _soup(get_html(f"https://www.keitaispace.co.jp/product/?se={se}"))
        for card in soup.select(".product-card, .product-list-item, .product-row"):
            name = card.select_one(".product-name")
            price = card.select_one(".price_pro_news")
            if not name or not price:
                continue
            offers.append({"text": name.get_text(" ", strip=True) + " 未開封",
                           "price": price.get_text(strip=True)})
    return offers


# ---------------------------------------------------------------- 家電市場
@shop("kaden")
def kaden():
    """検索結果テーブル: プライム価格 / 新品価格 / 中古価格。新品価格を採用。"""
    import urllib.parse
    offers = []
    for q in ("iPhone 17", "iPhone Air"):
        url = "https://www.kaden-ichiba.com/item/key/" + urllib.parse.quote(q)
        soup = _soup(get_html(url))
        for tr in soup.find_all("tr"):
            name_txt = re.sub(r"\s+", " ", tr.get_text(" ", strip=True))
            if "iPhone" not in name_txt:
                continue
            dangers = [s.get_text(strip=True) for s in tr.select("span.text-danger")]
            # danger = [プライム, 新品] または [新品]
            price = dangers[1] if len(dangers) >= 2 else (dangers[0] if dangers else None)
            if not price:
                continue
            offers.append({"text": name_txt.split("円")[0] + " 新品未開封",
                           "price": price})
    return offers


# ---------------------------------------------------------------- アキモバ
@shop("akimoba")
def akimoba():
    """softbank.html が実質SIMフリー買取価格の一覧テーブル。"""
    soup = _soup(get_html("https://www.akiba-mobile.co.jp/price/softbank.html"))
    offers = []
    for tr in soup.select("table.list tr"):
        tds = tr.find_all("td")
        if len(tds) != 3:
            continue
        offers.append({"text": f"{tds[0].get_text(' ', strip=True)} {tds[1].get_text(strip=True)}",
                       "price": tds[2].get_text(strip=True)})
    return offers


# ---------------------------------------------------------------- モバステ
@shop("mobasute")
def mobasute():
    """pastec.net。機種別ページの価格表に未開封品買取価格。"""
    offers = []
    for cid in ("555", "556", "557", "558"):  # 17Pro / 17ProMax / 17 / Air
        soup = _soup(get_html(f"https://pastec.net/iphone?series_child_id={cid}"))
        for cell in soup.select(".p-priceTable__inner"):
            name = cell.select_one(".p-priceTable__name span")
            price = cell.select_one(".price--unopened")
            if not name or not price:
                continue
            offers.append({"text": name.get_text(" ", strip=True) + " 未開封",
                           "price": price.get_text(strip=True)})
    return offers


# ---------------------------------------------------------------- 買取wiki
@shop("wiki")
def wiki():
    """西日暮里の買取wiki。/series/iphone/{page} に名前+買取価格。"""
    offers = []
    for page in ("", "2", "3", "4", "5"):
        url = f"https://iphonekaitori.tokyo/series/iphone/{page}".rstrip("/")
        soup = _soup(get_html(url))
        found = False
        for jia in soup.select("li.sub-pro-jia"):
            name = None
            for a in jia.find_all_previous("a", title=True, limit=6):
                t = a.get("title", "")
                if "iPhone" in t and "買取不可" not in t:
                    name = t
                    break
            if not name:
                continue
            offers.append({"text": name + " 未開封", "price": jia.get_text(strip=True)})
            found = True
        if not found:
            break
    return offers


# ---------------------------------------------------------------- 買取ルデヤ
@shop("rudeya")
def rudeya():
    """検索結果ページに product-card。名前に状態(未開封/SIMフリー)込み。"""
    offers = []
    for q in ("iPhone%2017", "iPhone%20Air"):
        for offset in (0, 20, 40, 60):
            url = f"https://kaitori-rudeya.com/search/index/{q}/-/-/-"
            if offset:
                url += f"/-/{offset}"
            soup = _soup(get_html(url))
            cards = soup.select("article.pgrid-card")
            for card in cards:
                name = card.select_one(".product-card-name-link")
                price_el = card.select_one(".product-card-price-value")
                if not name or not price_el:
                    continue
                offers.append({"text": name.get_text(" ", strip=True),
                               "price": price_el.get_text(strip=True)})
            if len(cards) < 20:
                break
    return offers


# ---------------------------------------------------------------- 買取ソムリエ
@shop("somurie")
def somurie():
    """Next.jsだがサーバーレンダリング済み。サブカテゴリごとに商品カード。"""
    offers = []
    for sub in ("3", "4", "5", "6"):  # 17 / 17 Pro / 17 Pro Max / Air
        html = get_html(f"https://somurie-kaitori.com/products?category=1&subcategory={sub}")
        soup = _soup(html)
        for p in soup.select("p.text-price-red"):
            if not re.match(r"^[\d,]+$", p.get_text(strip=True)):
                continue
            node = p
            txt = ""
            for _ in range(10):
                node = node.parent
                if node is None:
                    break
                txt = node.get_text(" ", strip=True)
                if "iPhone" in txt:
                    break
            price = int(p.get_text(strip=True).replace(",", ""))
            offers.append({"text": re.sub(r"\s+", " ", txt)[:200], "price": price})
    return offers
