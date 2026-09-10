"""ローカルMac用: CIから取得できない業者(Cloudflareブロック等)だけを取得して
docs/data/partial/<shop>.json に書き、GitHubへpushする。

launchdから 10:50/12:50/14:50/16:50/18:50 (JST) に実行される想定。
Macがスリープ中だった回は、次に起きたタイミングでまとめて1回実行される。
"""
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .main import DATA, ROOT, load_json, save_json
from .runner import collect
from .shops import SkipShop

JST = timezone(timedelta(hours=9))
TARGETS = ["enoking", "mobasute", "kaikyo", "space", "god"]  # CIから弾かれる業者はMacから取得


def _git(*args):
    return subprocess.run(["git", "-C", ROOT, *args],
                          capture_output=True, text=True)


def _heal_repo():
    """同期が静かに死ぬ原因を毎回つぶしてから始める。
    - 中断されたrebaseの残骸(.git/rebase-merge)があると以降のpullが全部失敗する
    - detached HEAD だと push が通らない
    実際に2026-09-10まで10日間これでpushが止まり、サイトが古いままになった。"""
    if os.path.isdir(os.path.join(ROOT, ".git", "rebase-merge")) or \
       os.path.isdir(os.path.join(ROOT, ".git", "rebase-apply")):
        _git("rebase", "--abort")
        for d in ("rebase-merge", "rebase-apply"):
            shutil.rmtree(os.path.join(ROOT, ".git", d), ignore_errors=True)
        print("[heal] 中断されたrebaseの残骸を除去した")
    br = _git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if br != "main":
        _git("fetch", "-q", "origin")
        _git("checkout", "-q", "-B", "main", "origin/main")
        print(f"[heal] ブランチを main に戻した(前: {br})")


GH = shutil.which("gh") or "/Users/user/bin/gh"


def _trigger_ci():
    """GitHub側の巡回を起動(cronが飛んだ時の保険。失敗しても無視)。"""
    try:
        t = subprocess.run([GH, "workflow", "run", "買取価格チェック",
                            "-R", "no739/iphone-kaitori"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ,
                                "PATH": os.environ.get("PATH", "") +
                                ":/usr/local/bin:/opt/homebrew/bin"})
        print("CI起動:", "OK" if t.returncode == 0 else t.stderr[:200])
    except Exception as e:  # noqa: BLE001
        print("CI起動失敗(無視):", e)


def should_run(now=None):
    """9月は30分おき、それ以外の月は1時間おきに実行する。
    launchdは毎時 :02 と :32 に起動するので、9月以外は :32 の回を見送る。"""
    now = now or datetime.now(JST)
    if now.month == 9:
        return True
    return now.minute < 20


def main():
    now0 = datetime.now(JST)
    if not should_run(now0):
        print(f"[skip] {now0:%m/%d %H:%M} は対象外(9月以外は1時間おき)")
        return
    _heal_repo()
    _git("pull", "--rebase", "-X", "theirs", "origin", "main")  # 常に最新コードで実行
    now = datetime.now(JST).isoformat(timespec="seconds")
    changed = []
    for sid in TARGETS:
        path = os.path.join(DATA, "partial", f"{sid}.json")
        try:
            best, _, _unk = collect(sid)
            if not best:
                raise RuntimeError("0件")
            old = load_json(path, {})
            save_json(path, {"updated": now, "prices": best})
            changed.append(sid)
            diff = "変化あり" if old.get("prices") != best else "価格変化なし"
            print(f"[ok] {sid}: {len(best)} items ({diff})")
        except SkipShop as e:
            print(f"[skip] {sid}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"[NG] {sid}: {e}")

    if changed:
        _git("add", "docs/data/partial")
        if _git("diff", "--cached", "--quiet").returncode == 0:
            print("差分なし、pushスキップ")
        else:
            _git("-c", "user.name=kaitori-local", "-c", "user.email=local@local",
                 "commit", "-m", f"ローカル価格更新(エノキング/モバステ) {now[:16]}")
            _git("pull", "--rebase", "-X", "theirs", "origin", "main")
            r = _git("push", "origin", "main")
            if r.returncode != 0:
                # 失敗したら残骸を掃除して、リモートに乗せ直してから1回だけ再試行
                print("push失敗、自己修復して再試行:", r.stderr[:150])
                _heal_repo()
                _git("fetch", "-q", "origin")
                _git("rebase", "-X", "theirs", "origin/main")
                r = _git("push", "origin", "main")
            print("push:", "OK" if r.returncode == 0 else r.stderr[:300])
    else:
        print("push対象なし")

    # 価格に変化がなくてもCIは必ず起動する(cronが死んでいても巡回が止まらないように)
    _trigger_ci()


if __name__ == "__main__":
    sys.exit(main())
