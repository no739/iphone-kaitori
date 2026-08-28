"""エノキング: 目に見えるブラウザを開くので、ユーザー自身がログインする。
成功したらログイン状態(セッション)を enoking_state.json に保存し、
以後のスクレイパーはパスワード不要でそのセッションを使い回す。

使い方: python3 -m scraper.enoking_setup
"""
import os

from playwright.sync_api import sync_playwright

ROOT = os.path.join(os.path.dirname(__file__), "..")
STATE = os.path.join(ROOT, "enoking_state.json")


def logged_in(page):
    try:
        for c in page.context.cookies():
            if "session-token" in c["name"]:
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def main():
    with sync_playwright() as pw:
        from .fetch import UA
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(locale="ja-JP", user_agent=UA,
                                  viewport={"width": 1100, "height": 800})
        page = ctx.new_page()
        page.goto("https://newenoking-kaitori.com/login")
        print("ブラウザ画面でエノキングにログインしてください(最大5分待ちます)…")
        for _ in range(300):
            page.wait_for_timeout(1000)
            if logged_in(page):
                page.wait_for_timeout(2000)
                ctx.storage_state(path=STATE)
                print("✅ ログイン状態を保存しました:", STATE)
                browser.close()
                return 0
        print("⏰ 5分以内にログインが確認できませんでした。もう一度実行してください。")
        browser.close()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
