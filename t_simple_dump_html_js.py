import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(ignore_https_errors=True) # セキュリティリスクに注意！
        page = await context.new_page()
        await page.goto("https://www.google.com")

        # ページが完全にロードされるのを待機
        await page.wait_for_load_state("load")

        # JavaScript ファイルをロード
        with open("script.js", "r", encoding="utf-8") as f:
            js_code = f.read()

        # ページに JavaScript コードを注入
        try:
            await page.evaluate(js_code)
            print("[Python] JavaScript code injected successfully.")  # 確認用ログ
        except Exception as e:
            print(f"[Python] JavaScript injection failed: {e}")
            await browser.close()
            return

        print("[Python] Start interacting with Google...")
        print("[Python] Waiting 5 seconds...")

        await page.wait_for_timeout(5000)

        # イベントセレクターを取得
        event_types = ['click', 'mousedown', 'mouseup', 'focus', 'blur', 'keydown', 'keyup'];
        for event_type in event_types:
            selector = await page.evaluate(f"window.{event_type}Selector");
            if selector:
                print(f"[Python] Last {event_type} event on: {selector}")
            else:
                print(f"[Python] No {event_type} event detected")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(main())