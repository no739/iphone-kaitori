"""開発用: 1店舗ぶん実行して正規化結果を表示する。
使い方: python3 -m scraper.runner <shop_id>
"""
import sys

from . import shops
from .normalize import Normalizer, detect_new_model, looks_unopened, yen


def collect(shop_id):
    """shop関数を実行し ({item_key: price}, 採用リスト, 未対応新機種) を返す。"""
    nz = Normalizer()
    fn = shops.REGISTRY[shop_id]
    best = {}
    kept = []
    unknowns = set()  # (token, サンプル商品名)
    for of in fn():
        price = of["price"] if isinstance(of["price"], int) else yen(of["price"])
        if not price:
            continue
        text = of["text"]
        if not looks_unopened(of.get("cond", text)):
            continue
        parsed = nz.parse(text)
        if not parsed:
            tok = detect_new_model(text)
            if tok:
                unknowns.add((tok, text[:60]))
            continue
        sid, cap, color = parsed
        ov = of.get("ov") or {}
        if color is None and nz.colored[sid] and ov:
            # カラー不明の1件+色減額表記 → 機種のカラーごとに価格を割り当て
            for c in nz.colors[sid]:
                p2 = price
                for word, pv in ov.items():
                    if nz.word_is_color(sid, c, word):
                        p2 = pv
                key = f"{sid}-{cap}-{c}"
                if p2 > best.get(key, 0):
                    best[key] = p2
        else:
            for key in nz.keys_for(sid, cap, color):
                if price > best.get(key, 0):
                    best[key] = price
        kept.append((text[:70], price))
    return best, kept, unknowns


if __name__ == "__main__":
    sid = sys.argv[1]
    best, kept, _unknowns = collect(sid)
    print(f"== {sid}: {len(kept)} offers -> {len(best)} keys")
    for k in sorted(best):
        print(f"  {k:24s} {best[k]:>9,}")
