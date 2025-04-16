import asyncio
import os
from playwright_stealth import stealth_async

import logging
from pathlib import Path
from playwright.async_api import async_playwright, Page, Locator, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
import traceback
import json # 結果をJSONで保存する場合

# --- 設定 ---
TARGET_SITES = {
    "google_search_top": "https://www.google.com/",
    "google_results": "https://www.google.com/search?q=playwright+test", # 例：実際の検索結果URL
    "news_article": "https://news.example.com/article123" # 例：テストに使いたいニュース記事URL（アクセス可能なもの）
    # 必要に応じて他のテスト対象サイトを追加
}
OUTPUT_RESULTS_FILE = "playwright_locator_test_results.json"
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
PAGE_LOAD_TIMEOUT = 30000
LOCATOR_TIMEOUT = 5000 # 各Locatorの存在確認タイムアウト (ミリ秒)
HIGHLIGHT_DURATION = 3000 # 要素をハイライト表示する時間 (ミリ秒)

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- テストする要素の情報と試すLocator ---
# 前のテストでLLMが抽出した情報や、手動で設定した情報を元に定義
ELEMENTS_TO_TEST = {
    "google_search_top": [
        {
            "name": "Search Textbox (by Role/Label)",
            "locators": [
                lambda page: page.get_by_role("textbox", name="検索"), # RoleとLabel
                lambda page: page.locator("#APjFqb") # ID (比較用)
            ]
        },
        {
            "name": "Search Button (by Role/Name)",
            "locators": [
                lambda page: page.get_by_role("button", name="Google 検索"), # RoleとName(テキスト)
                lambda page: page.locator('input[name="btnK"]') # 属性 (比較用)
            ]
        },
        {
            "name": "Feeling Lucky Button (by Role/Name)",
            "locators": [
                lambda page: page.get_by_role("button", name="I'm Feeling Lucky"),
                 lambda page: page.locator('input[name="btnI"]')
            ]
        }
    ],
    "google_results": [
        {
            "name": "Result 1 Link (by Text/Role)",
            "locators": [
                # テキストは変わりやすいので注意
                # lambda page: page.get_by_role("link", name="検索結果タイトル 1"), # 完全一致
                lambda page: page.get_by_role("link", name=r"/検索結果タイトル\s*1/"), # 正規表現（より柔軟）
                lambda page: page.locator("#rso .g").nth(0).locator('a[href]') # ★nth(0)と組み合わせ
            ]
        },
        {
            "name": "Result 2 Link (by nth)",
            "locators": [
                lambda page: page.locator("#rso .g").nth(1).get_by_role("link"), # ★nth(1)と組み合わせ
                lambda page: page.locator("#rso > div").nth(1).locator('a[href]') # nth(1)と別のセレクタ
            ]
        },
        {
            "name": "Next Page Link (by Role/Text)",
            "locators": [
                lambda page: page.get_by_role("link", name="次へ"),
                lambda page: page.locator("#pnnext") # ID (比較用)
            ]
        }
    ],
    "news_article": [
         {
            "name": "Article Title (by Role)",
            "locators": [
                lambda page: page.get_by_role("heading", level=1), # Role (レベル指定)
                lambda page: page.locator("h1.article-title") # CSS (比較用)
            ]
        },
        {
             "name": "Share Button (by Test ID)",
             "locators": [
                 lambda page: page.get_by_test_id("share-button"), # Test ID
                 lambda page: page.get_by_role("button", name="共有") # Role/Text (比較用)
             ]
         },
        {
            "name": "Non-existent Element (Comment Form)",
            "locators": [
                lambda page: page.get_by_role("form", name="コメント投稿"),
                lambda page: page.locator("#comment-form")
            ]
        }
    ]
    # --- 他のサイトや要素のテストケースを追加 ---
}

async def test_locator(page: Page, locator_func, element_name: str, locator_index: int) -> dict:
    """指定されたLocator関数を評価し、結果を返す"""
    result = {"name": element_name, "locator_type": "Unknown", "status": "Fail", "count": 0, "is_visible": False, "error": None, "selector_used": "N/A"}
    locator = None
    start_time = asyncio.get_event_loop().time()

    try:
        locator = locator_func(page)
        # locator からセレクター文字列を取得しようと試みる（デバッグ用、常に取得できるとは限らない）
        # Playwright の内部表現に依存するため不安定な可能性あり
        try:
             # page.locator を使っている場合は selector 文字列が取れることが多い
             if hasattr(locator, '_selector'):
                 result["selector_used"] = locator._selector
             else:
                 # get_by_* 系は selector を持たないことが多い
                 result["selector_used"] = f"get_by_... ({locator_index})" # 暫定的な表現
        except:
             result["selector_used"] = f"Unknown locator type ({locator_index})"

        logging.info(f"  Testing locator: {result['selector_used']}")
        result["locator_type"] = result["selector_used"] # タイプとして記録

        # 要素の数をカウント (TimeoutError になる可能性)
        # カウント自体のタイムアウトも設定
        count = await locator.count()
        result["count"] = count

        if count == 1:
            logging.info(f"    -> Found 1 element.")
            # 要素が1つ見つかった場合、表示されているか確認
            is_visible = await locator.is_visible(timeout=LOCATOR_TIMEOUT / 2) # 表示確認は短めに
            result["is_visible"] = is_visible
            if is_visible:
                result["status"] = "Success"
                logging.info(f"    -> Element is visible. Highlighting...")
                # 要素をハイライト
                await locator.highlight()
                # 少し待機してハイライトを確認できるようにする
                await asyncio.sleep(HIGHLIGHT_DURATION / 1000)
            else:
                result["status"] = "Fail (Not Visible)"
                logging.warning(f"    -> Element found but not visible.")
        elif count > 1:
            result["status"] = f"Fail (Found {count} elements)"
            logging.warning(f"    -> Found {count} elements, expected 1.")
        else: # count == 0
            result["status"] = "Fail (Not Found)"
            logging.warning(f"    -> Element not found.")

    except PlaywrightTimeoutError as e:
        result["status"] = "Fail (Timeout)"
        result["error"] = f"TimeoutError: {e}"
        logging.error(f"    -> TimeoutError: {e}")
    except PlaywrightError as e:
         result["status"] = "Fail (Playwright Error)"
         result["error"] = f"PlaywrightError: {e}"
         logging.error(f"    -> PlaywrightError: {e}")
    except Exception as e:
        result["status"] = "Fail (Unknown Error)"
        result["error"] = f"{type(e).__name__}: {e}"
        logging.error(f"    -> Unknown error: {e}")
        logging.debug(traceback.format_exc())
    finally:
         end_time = asyncio.get_event_loop().time()
         result["duration_ms"] = round((end_time - start_time) * 1000)

    return result


async def run_tests_for_site(site_key: str, url: str):
    """指定されたサイトに対して定義された要素テストを実行する"""
    logging.info("=" * 50)
    logging.info(f"テスト開始: {site_key} ({url})")
    logging.info("=" * 50)

    site_results = []
    playwright = None
    browser = None

    try:
        playwright = await async_playwright().start()
        # ヘッドレスモードを False にして目視確認できるようにする
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            locale='ja-JP'
        )
        await stealth_async(context)

        page = await context.new_page()

        logging.info(f"ナビゲーション中 ({PAGE_LOAD_TIMEOUT}ms timeout)...")
        try:
            await page.goto(url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT)
            logging.info("ページ読み込み完了。")
        except Exception as nav_e:
             logging.error(f"ナビゲーション失敗: {nav_e}")
             logging.error("このサイトの以降のテストをスキップします。")
             return [{"site": site_key, "status": "Navigation Failed", "error": str(nav_e)}]

        if site_key not in ELEMENTS_TO_TEST:
            logging.warning(f"サイト '{site_key}' のテストケースが定義されていません。")
            return [{"site": site_key, "status": "No Test Cases Defined"}]

        for element_test_case in ELEMENTS_TO_TEST[site_key]:
            element_name = element_test_case["name"]
            locators_to_try = element_test_case["locators"]
            logging.info(f"\n--- Testing Element: {element_name} ---")
            element_results = []
            for i, locator_func in enumerate(locators_to_try):
                result = await test_locator(page, locator_func, element_name, i)
                element_results.append(result)
            site_results.extend(element_results) # サイトの結果リストに追加

    except Exception as e:
        logging.error(f"サイト '{site_key}' のテスト中に予期せぬエラー: {e}")
        logging.debug(traceback.format_exc())
        # エラーが発生した場合でも、それまでの結果を返す
        site_results.append({"site": site_key, "status": "Unexpected Error", "error": str(e)})
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        logging.info(f"サイト '{site_key}' のテスト完了。")

    return site_results

async def main():
    all_results = {}
    for site_key, url in TARGET_SITES.items():
        # 実際のサイトにアクセスするので、URLがアクセス可能か注意
        # 例外処理を入れるなどして安全に実行する
        try:
             results = await run_tests_for_site(site_key, url)
             all_results[site_key] = results
        except Exception as e:
             logging.error(f"サイト {site_key} ({url}) のテスト中に致命的なエラー: {e}")
             all_results[site_key] = [{"status": "Fatal Error", "error": str(e)}]

    logging.info("=" * 60)
    logging.info(">>> 全サイトテスト完了 <<<")
    logging.info("=" * 60)

    # 結果をJSONファイルに出力
    output_path = Path(OUTPUT_RESULTS_FILE)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(all_results, f, indent=2, ensure_ascii=False)
        logging.info(f"テスト結果を '{output_path.resolve()}' に保存しました。")
    except Exception as e:
        logging.error(f"結果のJSONファイル書き込みに失敗しました: {e}")

    # 簡単なサマリーをログに出力
    for site, results in all_results.items():
        success_count = sum(1 for r in results if isinstance(r, dict) and r.get("status") == "Success")
        fail_count = len(results) - success_count
        logging.info(f"  Site: {site} - Success: {success_count}, Fail/Other: {fail_count}")


if __name__ == "__main__":
    asyncio.run(main())