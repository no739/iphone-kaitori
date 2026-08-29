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
    from .shops import SkipShop
    now = datetime.now(JST)
    now_iso = now.isoformat(timespec="seconds")
    prices, status, skipped = {}, {}, set()
    observed = {}  # sid -> その価格を実際に確認した時刻(取得失敗ならNone)
    unknowns = set()  # 未対応の新機種らしき商品 (token, サンプル名)
    for s in shops:
        sid = s["id"]
        if s.get("partial"):
            # Cloudflare等でCIから取得できない業者: ローカルMacがpushした
            # docs/data/partial/<id>.json を読む(scraper.partial が生成)
            snap = load_json(os.path.join(DATA, "partial", f"{sid}.json"), {})
            if snap.get("prices"):
                fresh = False
                try:
                    age = now - datetime.fromisoformat(snap["updated"])
                    fresh = age < timedelta(hours=4)
                except (KeyError, ValueError):
                    pass
                prices[sid] = snap["prices"]
                observed[sid] = snap.get("updated")
                status[sid] = {"ok": fresh,
                               "error": None if fresh
                               else "ローカル取得が4時間以上止まっています(Macスリープ中?)"}
                print(f"[{'ok' if fresh else 'NG'}] {s['name']}: "
                      f"partial {snap.get('updated', '?')}")
            else:
                prices[sid] = {}
                status[sid] = {"ok": False, "error": "ローカル取得データがまだありません"}
                print(f"[NG] {s['name']}: partialデータなし")
            continue
        try:
            best, _, unk = collect(sid)
            unknowns |= unk
            if not best:
                raise RuntimeError("商品を1件も抽出できませんでした(ページ構造変更の可能性)")
            prices[sid] = best
            observed[sid] = now_iso
            status[sid] = {"ok": True, "error": None}
            print(f"[ok] {s['name']}: {len(best)} items")
        except SkipShop as e:
            skipped.add(sid)
            print(f"[skip] {s['name']}: {e}")
        except Exception as e:  # noqa: BLE001
            prices[sid] = {}
            observed[sid] = None
            status[sid] = {"ok": False, "error": str(e)[:300]}
            print(f"[NG] {s['name']}: {e}")
    return prices, status, skipped, observed, unknowns


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


def _fmt_ts(iso):
    try:
        return datetime.fromisoformat(iso).strftime("%m/%d %H:%M")
    except (TypeError, ValueError):
        return None


def build_change_message(nz, shops_by_id, changes, now, prev_updated=None,
                         prices=None, my_shops=None):
    """業者×機種×容量ごとに1ブロックの変更速報。
    「どの業者が・どの機種の何色を・いくらからいくらに」を明示する。"""
    lines = [f"📱 **買取価格が動きました** ({now.strftime('%m/%d %H:%M')} の巡回)"]
    ups = sum(1 for c in changes if c["new"] > c["old"])
    downs = len(changes) - ups
    if len(changes) > 1:
        lines.append(f"🟢 値上げ{ups}件 / 🔴 値下げ{downs}件")
    lines.append("")

    def model_label(key):
        sid, cap = key.split("-")[:2]
        sr = next(s for s in nz.series if s["id"] == sid)
        cap_i = int(cap)
        cap_s = f"{cap_i // 1024}TB" if cap_i >= 1024 else f"{cap_i}GB"
        return f"{sr['name']} {cap_s}"

    def color_label(key):
        parts = key.split("-")
        if len(parts) < 3:
            return None
        sr = next(s for s in nz.series if s["id"] == parts[0])
        return (sr.get("colors") or {}).get(parts[2], parts[2])

    # 業者 × 機種容量 × 差額 でまとめる(同じ動きのカラーは1ブロックに)
    groups = {}
    for c in changes:
        base = "-".join(c["key"].split("-")[:2])
        gk = (c["shop"], base, c["old"], c["new"])
        groups.setdefault(gk, []).append(c)

    order = [f"{s['id']}-{int(c)}" for s in nz.series for c in s["capacities"]]

    def sort_key(gk):
        _shop, base, old, new = gk
        return (order.index(base) if base in order else 999, -(new - old))

    for gk in sorted(groups, key=sort_key):
        shop, base, old, new = gk
        cs = groups[gk]
        d = new - old
        arrow = "🟢▲" if d > 0 else "🔴▼"
        shop_name = shops_by_id.get(shop, {}).get("name", shop)
        colors = [color_label(c["key"]) for c in cs]
        colors = [c for c in colors if c]
        model = model_label(cs[0]["key"])
        head = f"{arrow} **{shop_name}**"
        body = f"　{model}" + (f" **{'・'.join(colors)}**" if colors else "")
        money = f"　**{fmt_yen(old)} → {fmt_yen(new)}**({d:+,})" + ("🔥" if d > 0 else "")
        lines.append(head)
        lines.append(body)
        lines.append(money)
        def short(iso, ref):
            """同じ日なら時刻だけ、日をまたぐ時は日付も付ける。"""
            try:
                d = datetime.fromisoformat(iso)
            except (TypeError, ValueError):
                return None
            return d.strftime("%H:%M" if d.date() == ref.date() else "%m/%d %H:%M")
        pt = short(cs[0].get("prev_ts") or prev_updated, now)
        nt = short(cs[0].get("ts"), now) or now.strftime("%H:%M")
        if pt:
            lines.append(f"　⏰ {pt}時点 → {nt}時点")
        lines.append("")

    lines.append("―――")
    # 変更が1件だけなら、その機種の今の最高値も添える(売り時の判断用)
    if len(groups) == 1 and prices:
        base = next(iter(groups))[1]
        best, best_shop = None, None
        for sid, shop_prices in prices.items():
            if my_shops and sid not in my_shops:
                continue
            for k, v in shop_prices.items():
                if k.startswith(base) and (best is None or v > best):
                    best, best_shop = v, sid
        if best:
            bn = shops_by_id.get(best_shop, {}).get("name", best_shop)
            label = "いま★業者の中の最高値" if my_shops else "いまの最高値"
            lines.append(f"📊 {label}: **{fmt_yen(best)}**({bn})")
    lines.append("全変更はサイトの「最近の変更」へ")
    return "\n".join(lines)


def best_by_key(prices, shop_filter=None):
    """{item_key: (最高値, [shop_id...])} カラー(item_key)単位。同額は全社返す。"""
    out = {}
    for sid, cur in prices.items():
        if shop_filter and sid not in shop_filter:
            continue
        for key, p in cur.items():
            if key not in out or p > out[key][0]:
                out[key] = (p, [sid])
            elif p == out[key][0]:
                out[key][1].append(sid)
    return out


def build_daily_report(nz, shops_by_id, prices, y_prices, now,
                       my_devices=None, my_shops=None):
    """毎朝のレポート。マイ端末をカラーごとに、★業者ごとの価格を並べる。"""
    shops = list(my_shops) if my_shops else list(prices.keys())
    title = f"☀️ **今日の買取価格一覧** ({now.strftime('%Y/%m/%d')} 11:00 時点)"
    sub = []
    if my_devices:
        sub.append("📱 マイ端末")
    if my_shops:
        sub.append("★お気に入り業者")
    if sub:
        title += "\n" + " × ".join(sub) + "の価格です"
    lines = [title, ""]

    for sr in nz.series:
        for cap in sr["capacities"]:
            cap_i = int(cap)
            cap_s = f"{cap_i // 1024}TB" if cap_i >= 1024 else f"{cap_i}GB"
            colors = list((sr.get("colors") or {}).keys()) or [None]
            block = []
            for col in colors:
                key = f"{sr['id']}-{cap_i}" + (f"-{col}" if col else "")
                if my_devices and key not in my_devices:
                    continue
                # ★業者ごとの価格(高い順)
                rows = []
                for sid in shops:
                    p = prices.get(sid, {}).get(key)
                    if p is None:
                        continue
                    rows.append((p, sid))
                if not rows:
                    continue
                rows.sort(key=lambda t: -t[0])
                cname = (sr.get("colors") or {}).get(col, col) if col else None
                if cname:
                    block.append(f"　**{cname}**")
                # 単独トップの時だけ👑(同額で並んでいる時は付けない)
                top_alone = len(rows) > 1 and rows[0][0] > rows[1][0]
                for i, (p, sid) in enumerate(rows):
                    name = shops_by_id.get(sid, {}).get("name", sid)
                    mark = "👑" if i == 0 and top_alone else "　"
                    line = f"　{mark}{name} **{fmt_yen(p)}**"
                    yp = (y_prices or {}).get(sid, {}).get(key)
                    if yp is not None:
                        d = p - yp
                        if d > 0:
                            line += f"  前日比 🟢▲+{d:,}円🔥"
                        elif d < 0:
                            line += f"  前日比 🔴▼{d:,}円"
                        else:
                            line += "  前日比 ⚪±0"
                    block.append(line)
            if block:
                lines.append(f"**{sr['name']} {cap_s}**")
                lines.extend(block)
                lines.append("")

    ng = [s for s in shops_by_id.values() if not s.get("_ok", True)]
    if ng:
        lines.append("⚠️ 価格取得に失敗した業者: "
                     + "、".join(s["name"] for s in ng))
        lines.append("(その業者は前回取得できた価格を表示しています)")
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

    prices, status, skipped, observed, unknowns = scrape_all(shops)
    shops = [s for s in shops if s["id"] not in skipped]
    shops_by_id = {s["id"]: s for s in shops}

    # 業者ごとの「価格を最後に実際に確認した時刻」。取得失敗時は前回値を引き継ぐ
    prev_shop_updated = prev.get("shop_updated", {})
    shop_updated = {}
    for s in shops:
        sid = s["id"]
        shop_updated[sid] = (observed.get(sid)
                             or prev_shop_updated.get(sid)
                             or prev.get("updated"))

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

    # ---- 変更レコードに観測時刻を付与(通知・履歴の両方で使う) ----
    prev_updated = prev.get("updated")
    ts = now.isoformat(timespec="seconds")
    for c in changes:
        # 新価格を確認した時刻(Mac取得分は実際の取得時刻)
        c["ts"] = shop_updated.get(c["shop"]) or ts
        # 旧価格を最後に確認した時刻(業者ごと。失敗が続いていた業者は古くなる)
        pt = prev_shop_updated.get(c["shop"]) or prev_updated
        if pt:
            c["prev_ts"] = pt

    # ---- 通知 ----
    # サイトで登録された「マイ端末/★業者」(家族共有)。未登録なら全件通知
    my_devices, my_shops = discord.fetch_prefs()
    notify_changes = changes
    if my_devices:
        notify_changes = [c for c in notify_changes if c["key"] in my_devices]
    if my_shops:
        notify_changes = [c for c in notify_changes if c["shop"] in my_shops]
    if notify_changes:
        msg = build_change_message(nz, shops_by_id, notify_changes, now,
                                   prev_updated, prices, my_shops)
        if my_devices or my_shops:
            msg += "\n(📱マイ端末×★業者に絞って通知中。全変更はサイトの「最近の変更」へ)"
        discord.send(msg, mention=True)
    elif changes:
        print(f"変更{len(changes)}件はすべてマイ端末×★業者の対象外のため通知なし")
    # 未対応の新機種を検知したら一度だけ通知(検知済みリストで重複防止)
    if unknowns:
        seen_path = os.path.join(DATA, "new_models_seen.json")
        seen = set(load_json(seen_path, []))
        fresh = sorted({(t, s2) for t, s2 in unknowns if t not in seen})
        if fresh:
            lines = ["🔥🔥🔥 **新機種を検知しました!** 🔥🔥🔥", ""]
            lines.append("📱 業者サイトにこんな商品が出はじめています:")
            for _t, sample in fresh[:6]:
                lines.append(f"　✨ {sample}")
            lines.append("")
            lines.append("👉 比較表に追加するには、Claudeに「**新機種に対応して**」と伝えてください")
            discord.send("\n".join(lines), mention=True)
            save_json(seen_path, sorted(seen | {t for t, _ in fresh}))

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
                                        y_snap.get("prices", {}), now,
                                        my_devices, my_shops), mention=True)
        save_json(os.path.join(daily_dir, f"{today_str}.json"),
                  {"date": today_str, "prices": prices})

    # ---- 保存 (サイト用データ) ----
    changes_log = load_json(os.path.join(DATA, "changes.json"), [])
    changes_log = (changes + changes_log)[:300]

    daily_files = sorted(os.listdir(daily_dir))[-30:] if os.path.isdir(daily_dir) else []
    y_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    y_snap = load_json(os.path.join(daily_dir, f"{y_str}.json"), {})
    if not y_snap.get("prices"):
        # 前日データがまだ無い間(運用初日)は当日朝のスナップショットで代用
        y_snap = load_json(os.path.join(daily_dir, f"{today_str}.json"), {})

    save_json(latest_path, {
        "updated": ts,
        "series": nz.series,
        "shops": [{"id": s["id"], "name": s["name"], "url": s["url"],
                   "ok": status[s["id"]]["ok"],
                   "error": status[s["id"]]["error"]} for s in shops],
        "prices": prices,
        "prev": prev_prices,
        "shop_updated": shop_updated,
        "yesterday": y_snap.get("prices", {}),
        "daily_files": daily_files,
    })
    save_json(os.path.join(DATA, "changes.json"), changes_log)
    print(f"done: changes={len(changes)} daily={daily}")


if __name__ == "__main__":
    main()
