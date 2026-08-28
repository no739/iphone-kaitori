"""巡回本体: 全業者をスクレイプ → 変更検知 → Discord通知 → データ保存。

使い方:
    python3 -m scraper.main            # 通常巡回(変更があった時だけ通知)
    python3 -m scraper.main --daily    # 巡回 + デイリーレポート送信(毎朝11時用)
    python3 -m scraper.main --dry      # 通知・保存なしで実行結果を表示
JST 11時の実行では --daily がなくても自動でデイリーレポートを送る。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

from . import discord
from .normalize import Normalizer
from .runner import collect

ROOT = os.path.join(os.path.dirname(__file__), "..")
DATA = os.path.join(ROOT, "docs", "data")
JST = timezone(timedelta(hours=9))

SERIES_LABEL_CACHE = {}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def load_shops():
    cfg = load_json(os.path.join(ROOT, "config", "shops.json"), {})
    return [s for s in cfg.get("shops", []) if s.get("enabled", True)]


def key_label(nz, key):
    if key in SERIES_LABEL_CACHE:
        return SERIES_LABEL_CACHE[key]
    parts = key.split("-")
    sid, cap = parts[0], int(parts[1])
    color = parts[2] if len(parts) > 2 else None
    sr = next(s for s in nz.series if s["id"] == sid)
    cap_s = f"{cap // 1024}TB" if cap >= 1024 else f"{cap}GB"
    lab = f"{sr['name']} {cap_s}"
    if color:
        lab += f" {sr['colors'][color]}"
    SERIES_LABEL_CACHE[key] = lab
    return lab


def scrape_all(shops):
    prices, status = {}, {}
    for s in shops:
        sid = s["id"]
        try:
            best, _ = collect(sid)
            if not best:
                raise RuntimeError("商品を1件も抽出できませんでした(ページ構造変更の可能性)")
            prices[sid] = best
            status[sid] = {"ok": True, "error": None}
            print(f"[ok] {s['name']}: {len(best)} items")
        except Exception as e:  # noqa: BLE001
            prices[sid] = {}
            status[sid] = {"ok": False, "error": str(e)[:300]}
            print(f"[NG] {s['name']}: {e}")
    return prices, status


def detect_changes(prev_prices, prices, status):
    changes = []
    for sid, cur in prices.items():
        if not status[sid]["ok"]:
            continue
        old = prev_prices.get(sid) or {}
        for key, p in cur.items():
            if key in old and old[key] != p:
                changes.append({"shop": sid, "key": key, "old": old[key], "new": p})
    return changes


def fmt_yen(v):
    return f"{v:,}円"


def build_change_message(nz, shops_by_id, changes, now):
    lines = [f"**📱 買取価格 変更検知** ({now.strftime('%m/%d %H:%M')})"]
    ups = sum(1 for c in changes if c["new"] > c["old"])
    downs = len(changes) - ups
    lines.append(f"変更 {len(changes)}件 (値上げ▲{ups} / 値下げ▼{downs})")
    lines.append("")
    for c in sorted(changes, key=lambda c: (c["key"], c["shop"]))[:80]:
        arrow = "▲" if c["new"] > c["old"] else "▼"
        diff = c["new"] - c["old"]
        lines.append(f"{arrow} {key_label(nz, c['key'])} | "
                     f"{shops_by_id[c['shop']]['name']} "
                     f"{fmt_yen(c['old'])} → **{fmt_yen(c['new'])}** ({diff:+,})")
    if len(changes) > 80:
        lines.append(f"…ほか {len(changes) - 80} 件(サイト参照)")
    return "\n".join(lines)


def best_by_capacity(nz, prices):
    """{series-cap: (最高値, shop_id)} カラーは最高値に集約。"""
    out = {}
    for sid, cur in prices.items():
        for key, p in cur.items():
            base = "-".join(key.split("-")[:2])
            if base not in out or p > out[base][0]:
                out[base] = (p, sid)
    return out


def build_daily_report(nz, shops_by_id, prices, y_prices, now):
    today = best_by_capacity(nz, prices)
    yesterday = best_by_capacity(nz, y_prices) if y_prices else {}
    lines = [f"**☀️ 今日の買取価格一覧** ({now.strftime('%Y/%m/%d')} 11:00 時点・各社最高値)"]
    lines.append("")
    for sr in nz.series:
        for cap in sr["capacities"]:
            base = f"{sr['id']}-{int(cap)}"
            if base not in today:
                continue
            p, sid = today[base]
            cap_i = int(cap)
            cap_s = f"{cap_i // 1024}TB" if cap_i >= 1024 else f"{cap_i}GB"
            line = (f"**{sr['name']} {cap_s}**  {fmt_yen(p)}"
                    f" ({shops_by_id[sid]['name']})")
            if base in yesterday:
                d = p - yesterday[base][0]
                mark = "▲" if d > 0 else ("▼" if d < 0 else "→")
                line += f"  前日比 {mark}{d:+,}円" if d else "  前日比 ±0"
            lines.append(line)
        lines.append("")
    ng = [s for s in shops_by_id.values() if not s.get("_ok", True)]
    if ng:
        lines.append("⚠️ 価格取得に失敗した業者: " + "、".join(s["name"] for s in ng))
    return "\n".join(lines)


def main():
    args = sys.argv[1:]
    dry = "--dry" in args
    now = datetime.now(JST)
    daily = "--daily" in args or now.hour == 11

    nz = Normalizer()
    shops = load_shops()
    shops_by_id = {s["id"]: s for s in shops}

    latest_path = os.path.join(DATA, "latest.json")
    prev = load_json(latest_path, {})
    prev_prices = prev.get("prices", {})
    prev_status = prev.get("status", {})

    prices, status = scrape_all(shops)

    # 失敗した業者は前回価格を引き継ぐ(サイトには「更新停止中」表示用のstatusを渡す)
    for sid in list(prices):
        if not status[sid]["ok"] and sid in prev_prices:
            prices[sid] = prev_prices[sid]

    changes = detect_changes(prev_prices, prices, status)
    new_failures = [sid for sid, st in status.items()
                    if not st["ok"] and prev_status.get(sid, {}).get("ok", True)]

    for s in shops:
        s["_ok"] = status[s["id"]]["ok"]

    if dry:
        print(f"changes: {len(changes)}")
        for c in changes[:20]:
            print("  ", c)
        print("new failures:", new_failures)
        return

    # ---- 通知 ----
    if changes:
        discord.send(build_change_message(nz, shops_by_id, changes, now))
    if new_failures:
        names = "、".join(shops_by_id[s]["name"] for s in new_failures)
        errs = "\n".join(f"- {shops_by_id[s]['name']}: {status[s]['error']}"
                         for s in new_failures)
        discord.send(f"⚠️ **価格取得に失敗しました**: {names}\n{errs}\n"
                     "(サイト構造が変わった可能性があります)")

    today_str = now.strftime("%Y-%m-%d")
    daily_dir = os.path.join(DATA, "daily")
    if daily:
        y_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        y_snap = load_json(os.path.join(daily_dir, f"{y_str}.json"), {})
        discord.send(build_daily_report(nz, shops_by_id, prices,
                                        y_snap.get("prices", {}), now))
        save_json(os.path.join(daily_dir, f"{today_str}.json"),
                  {"date": today_str, "prices": prices})

    # ---- 保存 (サイト用データ) ----
    changes_log = load_json(os.path.join(DATA, "changes.json"), [])
    ts = now.isoformat(timespec="seconds")
    for c in changes:
        c["ts"] = ts
    changes_log = (changes + changes_log)[:300]

    daily_files = sorted(os.listdir(daily_dir))[-30:] if os.path.isdir(daily_dir) else []
    y_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    y_snap = load_json(os.path.join(daily_dir, f"{y_str}.json"), {})

    save_json(latest_path, {
        "updated": ts,
        "series": nz.series,
        "shops": [{"id": s["id"], "name": s["name"], "url": s["url"],
                   "ok": status[s["id"]]["ok"],
                   "error": status[s["id"]]["error"]} for s in shops],
        "prices": prices,
        "prev": prev_prices,
        "yesterday": y_snap.get("prices", {}),
        "daily_files": daily_files,
    })
    save_json(os.path.join(DATA, "changes.json"), changes_log)
    print(f"done: changes={len(changes)} daily={daily}")


if __name__ == "__main__":
    main()
