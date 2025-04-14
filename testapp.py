import time
import random
from playwright.sync_api import sync_playwright, Page, expect
from playwright_stealth import stealth_sync # playwright-stealthをインポート

def human_like_delay(min_seconds=0.5, max_seconds=1.5):
    """人間らしいランダムな遅延を生成"""
    time.sleep(random.uniform(min_seconds, max_seconds))

def run(page: Page):
    """指定されたページでGoogle検索とクリック操作を実行"""
    search_term = "あああ"
    search_box_selector = "#APjFqb"
    #first_result_selector = "#rso > div:nth-child(1) > div.A6K0A > div > div > div.kb0PBd.ieodic.jGGQ5e > div > div:nth-child(2) > div > div > span > a"
    first_result_selector = "#rso > div:nth-child(3) > div > div > div.kb0PBd.A9Y9g.jGGQ5e > div > div:nth-child(2) > div > div > span > a"

    print("1. Googleにアクセスします...")
    page.goto("https://www.google.co.jp/", wait_until="networkidle")
    human_like_delay(1, 2)

    print(f"2. 検索ボックス ({search_box_selector}) を探しています...")
    search_box = page.locator(search_box_selector)
    expect(search_box).to_be_visible(timeout=10000)

    print("3. 検索ボックスをクリック（より人間らしく）...")
    search_box.click()
    human_like_delay(0.3, 0.8)

    print(f"4. '{search_term}' を人間のように入力します...")
    search_box.type(search_term, delay=random.uniform(80, 250))
    human_like_delay(0.5, 1.0)

    print("5. Enterキーを押します...")
    search_box.press("Enter")

    print("6. 検索結果が表示されるのを待ちます...")
    page.wait_for_selector(first_result_selector, state="visible", timeout=15000)
    human_like_delay(1, 2.5)

    print(f"7. 最初の結果 ({first_result_selector}) を探しています...")
    first_result_link = page.locator(first_result_selector)
    expect(first_result_link).to_be_visible(timeout=10000)

    print("8. 結果リンクの上にマウスカーソルを移動（より人間らしく）...")
    try:
        first_result_link.hover()
        human_like_delay(0.4, 0.9)
    except Exception as e:
        print(f"  警告: ホバー中にエラーが発生しました: {e}。クリックは試行します。")

    print("9. 最初の結果をクリックします...")
    first_result_link.click()

    print("10. ページ遷移を待ちます...")
    page.wait_for_load_state("networkidle", timeout=20000)
    print("   新しいページがロードされました。")
    human_like_delay(1.5, 3)

    print("操作完了！")


# --- メイン処理 ---
with sync_playwright() as p:
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36" # 例

    browser = p.chromium.launch(headless=True,
                                 args=["--start-maximized"],
                                 slow_mo=random.uniform(50, 150)
                                )
    context = browser.new_context(
        user_agent=user_agent,
        viewport={'width': 1920, 'height': 1080},
        locale='ja-JP',
        timezone_id='Asia/Tokyo',
        no_viewport=True
    )

    page = context.new_page()

    # --- ここで stealth_sync を適用 ---
    print("Stealth機能を適用します...")
    stealth_sync(page)
    # ------------------------------------

    try:
        run(page)
    except Exception as e:
        print(f"エラーが発生しました: {e}")
        try:
            page.screenshot(path="error_screenshot.png")
            print("エラー発生時のスクリーンショットを 'error_screenshot.png' として保存しました。")
        except Exception as se:
            print(f"スクリーンショットの保存中にエラーが発生しました: {se}")
    finally:
        print("ブラウザを閉じます...")
        human_like_delay(2, 4)
        browser.close()