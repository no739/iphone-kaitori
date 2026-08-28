"""商品名の文字列を (series_id, 容量GB, カラー) に正規化する。

config/models.json の series 定義から別名テーブルを組み立てるので、
新機種は models.json に追記するだけで認識されるようになる。
"""
import json
import os
import re
import unicodedata

_CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "models.json")

COLOR_WORDS = {
    "orange": ["コズミックオレンジ", "オレンジ", "orange", "橙"],
    "blue": ["ディープブルー", "ブルー", "blue", "青"],
    "silver": ["シルバー", "silver", "銀"],
}


def load_models():
    with open(_CONFIG, encoding="utf-8") as f:
        return json.load(f)["series"]


def _clean(s):
    s = unicodedata.normalize("NFKC", s)
    s = s.lower()
    s = re.sub(r"[\s　/・()\[\]【】()-]+", "", s)
    return s


class Normalizer:
    def __init__(self):
        self.series = load_models()
        # 別名 → series_id。長い別名から先に照合する。
        self.aliases = []
        for sr in self.series:
            sid = sr["id"]
            base = _clean(sr["name"])          # 例: iphone17promax
            noiphone = base.replace("iphone", "")  # 例: 17promax
            names = {base, noiphone, sid}
            if "air" in sid:
                names.add("iphoneair")  # 「iPhone Air」表記の店がある
            for al in sr.get("aliases", []):
                names.add(_clean(al))
            for n in names:
                if n and not n.isdigit():  # 「17」のような数字のみは価格等に誤マッチするので除外
                    self.aliases.append((n, sid))
        self.aliases.sort(key=lambda t: -len(t[0]))
        self.caps = {sid: set(int(k) for k in sr["capacities"])
                     for sid, sr in ((s["id"], s) for s in self.series)}
        self.colored = {s["id"]: bool(s["colors"]) for s in self.series}
        self.colors = {s["id"]: list(s["colors"].keys()) for s in self.series}

    def parse(self, raw):
        """rawな商品名 → (series_id, cap_gb, color|None)。対象外は None。"""
        s = _clean(raw)
        # 容量は空白を保った文字列から取る(「17 256GB」が「17256gb」に化けるのを防ぐ)
        s_sp = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", raw).lower())
        sid = None
        for alias, _sid in self.aliases:
            # 直後に英字が続く場合は別機種(例: iphone17 が iphone17e に部分一致)。
            # 直前が数字の場合も誤マッチ(例: 価格 170,000 の中の 17pro 等)なので弾く。
            if re.search(r"(?<![0-9])" + re.escape(alias) + r"(?![a-z])", s):
                sid = _sid
                break
        if sid is None:
            return None
        m = re.search(r"(\d+(?:\.\d+)?)\s*(tb|gb|g\b|t\b)", s_sp)
        if not m:
            return None
        unit = m.group(2)
        cap = int(float(m.group(1)) * (1024 if unit in ("tb", "t") else 1))
        if cap not in self.caps.get(sid, set()):
            return None
        color = None
        if self.colored[sid]:
            for c, words in COLOR_WORDS.items():
                if any(_clean(w) in s for w in words):
                    color = c
                    break
        return sid, cap, color

    def keys_for(self, sid, cap, color):
        """この商品が該当する item key のリスト。
        カラー追跡対象の機種でカラー不明(=全色共通価格)なら全色ぶん返す。"""
        if self.colored[sid]:
            cols = [color] if color else self.colors[sid]
            return [f"{sid}-{cap}-{c}" for c in cols]
        return [f"{sid}-{cap}"]

    def all_keys(self):
        keys = []
        for sr in self.series:
            sid = sr["id"]
            for cap in sr["capacities"]:
                if self.colored[sid]:
                    for c in self.colors[sid]:
                        keys.append(f"{sid}-{int(cap)}-{c}")
                else:
                    keys.append(f"{sid}-{int(cap)}")
        return keys


PRICE_RE = re.compile(r"([0-9][0-9,]{4,})\s*円?")


def yen(text):
    """文字列から金額(int)を取り出す。見つからなければ None。"""
    t = unicodedata.normalize("NFKC", str(text))
    m = PRICE_RE.search(t.replace("¥", "").replace("￥", ""))
    if not m:
        return None
    v = int(m.group(1).replace(",", ""))
    return v if 1000 <= v <= 2_000_000 else None


NEW_OK = re.compile(r"未開封|未使用")
NEW_NG = re.compile(r"開封|中古|ジャンク|判定[×x]|利用制限[×x]")  # 「未開封」は除去済みの前提


def looks_unopened(text):
    t = unicodedata.normalize("NFKC", text)
    if NEW_NG.search(t.replace("未開封", "")):
        return False
    return bool(NEW_OK.search(t) or "新品" in t)
