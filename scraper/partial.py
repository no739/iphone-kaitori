"""ローカルMac用: CIから取得できない業者(Cloudflareブロック等)だけを取得して
docs/data/partial/<shop>.json に書き、GitHubへpushする。

launchdから 10:50/12:50/14:50/16:50/18:50 (JST) に実行される想定。
Macがスリープ中だった回は、次に起きたタイミングでまとめて1回実行される。
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from .main import DATA, ROOT, load_json, save_json
from .runner import collect
from .shops import SkipShop

JST = timezone(timedelta(hours=9))
TARGETS = ["enoking", "mobasute", "kaikyo"]  # CIから弾かれ/不安定な業者はMacから取得


def main():
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

    if not changed:
        print("push対象なし")
        return

    def git(*args):
        return subprocess.run(["git", "-C", ROOT, *args],
                              capture_output=True, text=True)

    git("add", "docs/data/partial")
    if git("diff", "--cached", "--quiet").returncode == 0:
        print("差分なし、pushスキップ")
        return
    git("-c", "user.name=kaitori-local", "-c", "user.email=local@local",
        "commit", "-m", f"ローカル価格更新(エノキング/モバステ) {now[:16]}")
    git("pull", "--rebase", "-X", "theirs", "origin", "main")
    r = git("push", "origin", "main")
    print("push:", "OK" if r.returncode == 0 else r.stderr[:300])

    # 保険: GitHubのcronが飛んだ時のために、こちらから巡回を起動しておく
    # (cronも動いた場合は2回目が「変更なし」になるだけで実害はない)
    try:
        t = subprocess.run(["gh", "workflow", "run", "買取価格チェック",
                            "-R", "no739/iphone-kaitori"],
                           capture_output=True, text=True, timeout=60,
                           env={**os.environ,
                                "PATH": os.environ.get("PATH", "") +
                                ":/usr/local/bin:/opt/homebrew/bin"})
        print("CI起動:", "OK" if t.returncode == 0 else t.stderr[:200])
    except Exception as e:  # noqa: BLE001
        print("CI起動失敗(無視):", e)


if __name__ == "__main__":
    sys.exit(main())
