"""Discord Webhook 通知。

Webhook URL は環境変数 DISCORD_WEBHOOKS(改行/カンマ区切りで複数可)か、
リポジトリ直下の webhooks.txt(gitignore済み・1行1URL)から読む。
"""
import json
import os
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")


def fetch_prefs(cache_path=None):
    """サイトで登録された「マイ端末/★業者」(家族共有)を取得する。

    戻り値は (devices, shops, ok)。取得できた内容は cache_path に保存し、
    次に取得へ失敗した時はそれを使う。取得も復元もできなければ ok=False を返し、
    呼び出し側は通知を見送る(絞り込めないまま全件通知しないため)。
    """
    reg = os.environ.get("WEBHOOK_REGISTRY_URL", "").strip()
    if not reg:
        return [], [], True  # 登録所を使わない構成では従来どおり絞り込みなし

    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(f"{reg}?action=prefs",
                                         headers={"User-Agent": "kaitori-bot"})
            with urllib.request.urlopen(req, timeout=45) as r:
                j = json.loads(r.read().decode())
            if j.get("ok"):
                devices = list(j.get("devices") or [])
                shops = list(j.get("shops") or [])
                if cache_path:
                    try:
                        with open(cache_path, "w", encoding="utf-8") as f:
                            json.dump({"devices": devices, "shops": shops},
                                      f, ensure_ascii=False, indent=1)
                    except OSError:
                        pass
                return devices, shops, True
            last_err = j
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < 2:
                time.sleep(3)
    print("prefs取得失敗:", last_err)

    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as f:
                c = json.load(f)
            print("  → 前回取得した設定で絞り込む")
            return list(c.get("devices") or []), list(c.get("shops") or []), True
        except (OSError, ValueError):
            pass
    return [], [], False


def registry_urls():
    """サイトから各自登録されたWebhook(Apps Script登録所)を取得。"""
    import urllib.parse
    reg = os.environ.get("WEBHOOK_REGISTRY_URL", "").strip()
    key = os.environ.get("WEBHOOK_REGISTRY_KEY", "").strip()
    if not (reg and key):
        return []
    try:
        url = f"{reg}?key={urllib.parse.quote(key)}"
        req = urllib.request.Request(url, headers={"User-Agent": "kaitori-hikaku-bot"})
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.load(r)
        return [u for u in data.get("webhooks", [])
                if isinstance(u, str) and u.startswith("https://")]
    except Exception as e:  # noqa: BLE001
        print(f"[discord] 登録所からの取得失敗: {e}")
        return []


def webhook_urls():
    urls = []
    env = os.environ.get("DISCORD_WEBHOOKS", "")
    raw = env.replace(",", "\n")
    path = os.path.join(ROOT, "webhooks.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            raw += "\n" + f.read()
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("https://") and "discord" in line:
            urls.append(line)
    urls.extend(registry_urls())
    return list(dict.fromkeys(urls))


def send(content=None, embeds=None, mention=False):
    """全Webhookへ送信。2000字制限があるので content は分割する。
    mention=True で先頭に@everyoneを付け、赤バッジ+プッシュ通知を発生させる。"""
    if mention and content:
        content = "@everyone\n" + content
    urls = webhook_urls()
    if not urls:
        print("[discord] webhook未設定のため通知スキップ")
        return
    chunks = [None]
    if content:
        chunks = []
        buf = ""
        for line in content.splitlines(keepends=True):
            if len(buf) + len(line) > 1900:
                chunks.append(buf)
                buf = ""
            buf += line
        if buf:
            chunks.append(buf)
    for url in urls:
        for i, chunk in enumerate(chunks):
            payload = {}
            if chunk:
                payload["content"] = chunk
                payload["allowed_mentions"] = {"parse": ["everyone"]}
            if embeds and i == 0:
                payload["embeds"] = embeds
            if not payload:
                continue
            req = urllib.request.Request(
                url, data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json",
                         "User-Agent": "kaitori-hikaku-bot"})
            try:
                urllib.request.urlopen(req, timeout=20)
            except Exception as e:  # noqa: BLE001
                print(f"[discord] 送信失敗 {url[:60]}...: {e}")
            time.sleep(0.6)  # rate limit回避
