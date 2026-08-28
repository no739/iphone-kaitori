"""Discord Webhook 通知。

Webhook URL は環境変数 DISCORD_WEBHOOKS(改行/カンマ区切りで複数可)か、
リポジトリ直下の webhooks.txt(gitignore済み・1行1URL)から読む。
"""
import json
import os
import time
import urllib.request

ROOT = os.path.join(os.path.dirname(__file__), "..")


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
