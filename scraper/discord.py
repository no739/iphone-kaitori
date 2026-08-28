"""Discord Webhook 通知。

Webhook URL は環境変数 DISCORD_WEBHOOKS(改行/カンマ区切りで複数可)か、
リポジトリ直下の webhooks.txt(gitignore済み・1行1URL)から読む。
"""
import json
import os
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")


def fetch_prefs():
    """サイトで登録された「マイ端末/★業者」(家族共有)を取得。
    失敗・未登録時は空リスト=絞り込みなしで従来どおり全件通知。"""
    reg = os.environ.get("WEBHOOK_REGISTRY_URL", "").strip()
    if not reg:
        return [], []
    try:
        req = urllib.request.Request(f"{reg}?action=prefs",
                                     headers={"User-Agent": "kaitori-bot"})
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read().decode())
        if j.get("ok"):
            return list(j.get("devices") or []), list(j.get("shops") or [])
    except Exception as e:  # noqa: BLE001
        print("prefs取得失敗(絞り込みなしで続行):", e)
    return [], []


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


def send(content=None, embeds=None):
    """全Webhookへ送信。2000字制限があるので content は分割する。"""
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
