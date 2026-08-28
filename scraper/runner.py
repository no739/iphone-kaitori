"""開発用: 1店舗ぶん実行して正規化結果を表示する。
使い方: python3 -m scraper.runner <shop_id>
"""
import sys

from . import shops
from .normalize import Normalizer, looks_unopened, yen


def collect(shop_id):
    """shop関数を実行し {item_key: price} を返す。"""
    nz = Normalizer()
    fn = shops.REGISTRY[shop_id]
    best = {}
    kept = []
    for of in fn():
        price = of["price"] if isinstance(of["price"], int) else yen(of["price"])
        if not price:
            continue
        text = of["text"]
        if not looks_unopened(of.get("cond", text)):
            continue
        parsed = nz.parse(text)
        if not parsed:
            continue
        sid, cap, color = parsed
        for key in nz.keys_for(sid, cap, color):
            if price > best.get(key, 0):
                best[key] = price
        kept.append((text[:70], price))
    return best, kept


if __name__ == "__main__":
    sid = sys.argv[1]
    best, kept = collect(sid)
    print(f"== {sid}: {len(kept)} offers -> {len(best)} keys")
    for k in sorted(best):
        print(f"  {k:24s} {best[k]:>9,}")
