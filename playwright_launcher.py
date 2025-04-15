# --- ファイル: playwright_launcher.py (修正版) ---
"""
Playwrightの起動、初期設定、アクション実行の呼び出し、終了処理を行います。
ステルスモードエラー時のリトライ機能を追加。
"""
import asyncio
import logging
import os
import time
import traceback
import warnings
from playwright.async_api import (
    async_playwright,
    Page,
    Browser, # Browser 型ヒント追加
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)
from playwright_stealth import stealth_async
from typing import List, Tuple, Dict, Any, Optional # Optional を追加

import config
from playwright_actions import execute_actions_async # アクション実行関数をインポート

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport") # Playwrightの既知の警告を抑制

# --- ▼▼▼ 追加: ステルスモードエラー検出用 ▼▼▼ ---
# 検出対象のエラーメッセージ（完全一致）
STEALTH_ERROR_MESSAGE = "大変申し訳ありませんが、ページを正しく表示できませんでした。\n推奨されているブラウザであるかをご確認の上、時間をおいて再度お試しください。"
# エラーメッセージが含まれる可能性のある要素のセレクター（例: body全体や特定のエラー表示領域）
ERROR_MESSAGE_SELECTOR = "body"
# --- ▲▲▲ 追加 ▲▲▲ ---


# --- ▼▼▼ 追加: ブラウザとコンテキストを起動するヘルパー関数 ▼▼▼ ---
async def _launch_browser_and_context(
    playwright_instance,
    headless_mode: bool,
    slow_motion: int,
    default_timeout: int,
    apply_stealth: bool = True # ステルスモードを適用するかどうかのフラグ
) -> Tuple[Browser, BrowserContext]:
    """ブラウザとコンテキストを起動し、オプションでステルスモードを適用する"""
    logger.info(f"ブラウザ起動 (Chromium, Headless: {headless_mode}, SlowMo: {slow_motion}ms)...")
    browser = await playwright_instance.chromium.launch(
        headless=headless_mode,
        slow_mo=slow_motion,
    )
    logger.info("新しいブラウザコンテキストを作成します...")
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
        viewport={'width': 1920, 'height': 1080},
        locale='ja-JP',
        timezone_id='Asia/Tokyo',
        java_script_enabled=True,
        extra_http_headers={'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'}
    )
    context.set_default_timeout(default_timeout)
    logger.info(f"コンテキストのデフォルトタイムアウトを {default_timeout}ms に設定しました。")

    if apply_stealth:
        logger.info("Applying stealth mode to the context...")
        try:
            await stealth_async(context)
            logger.info("Stealth mode applied successfully.")
        except Exception as stealth_err:
            logger.warning(f"Failed to apply stealth mode: {stealth_err}")
    else:
        logger.info("Stealth mode is disabled for this context.")

    return browser, context
# --- ▲▲▲ 追加 ▲▲▲ ---


async def run_playwright_automation_async(
        target_url: str,
        actions: List[Dict[str, Any]],
        headless_mode: bool = False,
        slow_motion: int = 100,
        default_timeout: int = config.DEFAULT_ACTION_TIMEOUT
    ) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Playwright を非同期で初期化し、指定されたURLにアクセス後、一連のアクションを実行します。
    ステルスモードでエラーが発生した場合、ステルスモードなしでリトライします。
    """
    logger.info("--- Playwright 自動化開始 (非同期) ---")
    all_success = False
    final_results: List[Dict[str, Any]] = []
    playwright = None
    browser: Optional[Browser] = None # Optional に変更
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None
    initial_navigation_successful = False
    retry_attempted = False # リトライフラグ

    try:
        playwright = await async_playwright().start()
        effective_default_timeout = default_timeout if default_timeout else config.DEFAULT_ACTION_TIMEOUT

        # --- 1回目の試行 (ステルスモードあり) ---
        logger.info("--- Initial attempt (with Stealth Mode) ---")
        apply_stealth_mode = True # 最初はステルスモードを適用
        browser, context = await _launch_browser_and_context(
            playwright, headless_mode, slow_motion, effective_default_timeout, apply_stealth=apply_stealth_mode
        )

        logger.info("新しいページを作成します...")
        page = await context.new_page()
        api_request_context = context.request # APIリクエストコンテキストはここで取得

        # 最初のページへのナビゲーション (タイムアウトを長めに設定)
        initial_nav_timeout = max(effective_default_timeout * 3, 30000)
        logger.info(f"最初のナビゲーション: {target_url} (タイムアウト: {initial_nav_timeout}ms)...")
        try:
            await page.goto(target_url, wait_until="load", timeout=initial_nav_timeout)
            initial_navigation_successful = True
            logger.info("最初のナビゲーション成功。")

            # --- ▼▼▼ エラーメッセージチェック ▼▼▼ ---
            logger.info("Checking for stealth error message...")
            try:
                # エラーメッセージが表示されるまで少し待つ（必要に応じて調整）
                await page.wait_for_timeout(1000)
                # 指定されたセレクター内のテキストを取得
                page_content = await page.locator(ERROR_MESSAGE_SELECTOR).inner_text(timeout=5000) # タイムアウト設定
                # 改行も含めて完全一致で比較
                if STEALTH_ERROR_MESSAGE in page_content:
                     logger.warning(f"検出されたエラーメッセージ: \"{STEALTH_ERROR_MESSAGE}\"")
                     logger.warning("Stealth mode may be blocked. Retrying without stealth mode...")
                     retry_attempted = True
                     initial_navigation_successful = False # リトライするので一旦失敗扱い

                     # --- 現在のブラウザとコンテキストを閉じる ---
                     logger.info("Closing current browser and context for retry...")
                     if context: await context.close()
                     if browser: await browser.close()
                     browser, context, page = None, None, None # リセット

                     # --- 2回目の試行 (ステルスモードなし) ---
                     logger.info("--- Retry attempt (without Stealth Mode) ---")
                     apply_stealth_mode = False # ステルスモードを無効化
                     browser, context = await _launch_browser_and_context(
                         playwright, headless_mode, slow_motion, effective_default_timeout, apply_stealth=apply_stealth_mode
                     )
                     logger.info("新しいページを作成します (リトライ)...")
                     page = await context.new_page()
                     api_request_context = context.request # APIリクエストコンテキストを再取得

                     logger.info(f"再ナビゲーション: {target_url} (タイムアウト: {initial_nav_timeout}ms)...")
                     await page.goto(target_url, wait_until="load", timeout=initial_nav_timeout)
                     initial_navigation_successful = True # リトライ成功
                     logger.info("再ナビゲーション成功。")
                else:
                    logger.info("Stealth error message not found.")
            except PlaywrightTimeoutError:
                 logger.warning(f"Timeout while checking for error message in '{ERROR_MESSAGE_SELECTOR}'. Assuming no error message found.")
            except Exception as check_err:
                logger.warning(f"Error checking for stealth message: {check_err}. Proceeding...", exc_info=True)
            # --- ▲▲▲ エラーメッセージチェック ▲▲▲ ---

        except (PlaywrightTimeoutError, PlaywrightError) as nav_error:
            logger.error(f"最初のナビゲーションに失敗しました ({target_url}): {nav_error}")
            # ナビゲーション失敗は致命的エラーとして扱う
            raise nav_error # エラーを再送出して全体のエラーハンドリングに任せる

        # --- ナビゲーション成功後、アクション実行 ---
        if initial_navigation_successful and page:
            logger.info("アクションの実行を開始します...")
            all_success, final_results = await execute_actions_async(
                page, actions, api_request_context, effective_default_timeout
            )
            if all_success:
                logger.info("すべてのステップが正常に完了しました。")
            else:
                logger.error("自動化タスクの途中でエラーが発生しました。")
        elif not initial_navigation_successful and retry_attempted:
             # リトライ後のナビゲーションも失敗した場合
             raise PlaywrightError(f"Navigation failed even after retrying without stealth mode for URL: {target_url}")
        else:
             # 最初のナビゲーションが失敗し、リトライもされなかった場合（エラーチェック中のエラーなど）
             raise PlaywrightError(f"Initial navigation failed and could not proceed for URL: {target_url}")


    # --- 全体的なエラーハンドリング ---
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as e:
         error_msg_overall = f"Playwright 処理全体で予期せぬエラーが発生しました: {type(e).__name__} - {e}"
         logger.error(error_msg_overall, exc_info=True)
         overall_error_screenshot_path = None
         if page and not page.is_closed():
              timestamp = time.strftime("%Y%m%d_%H%M%S")
              overall_error_ss_filename = f"error_overall_{timestamp}.png"
              overall_error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, overall_error_ss_filename)
              try:
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  await page.screenshot(path=overall_error_ss_path, full_page=True, timeout=10000)
                  logger.info(f"全体エラー発生時のスクリーンショットを保存しました: {overall_error_ss_path}")
                  overall_error_screenshot_path = overall_error_ss_path
              except Exception as ss_e:
                  logger.error(f"全体エラー発生時のスクリーンショット保存に失敗しました: {ss_e}")
         if not final_results or (isinstance(final_results[-1].get("status"), str) and final_results[-1].get("status") != "error"):
             error_details = {
                 "step": "Overall Execution",
                 "status": "error",
                 "message": str(e),
                 "full_error": error_msg_overall,
                 "traceback": traceback.format_exc()
             }
             if overall_error_screenshot_path:
                 error_details["error_screenshot"] = overall_error_screenshot_path
             final_results.append(error_details)
         all_success = False

    # --- クリーンアップ処理 ---
    finally:
        logger.info("クリーンアップ処理を開始します...")
        if context:
            try:
                await context.close()
                logger.info("ブラウザコンテキストを閉じました。")
            except Exception as context_close_e:
                if "closed" not in str(context_close_e).lower():
                    logger.warning(f"ブラウザコンテキストのクローズ中にエラーが発生しました (無視): {context_close_e}")
        else:
             logger.debug("ブラウザコンテキストは存在しません。")

        if browser and browser.is_connected():
            try:
                await browser.close()
                logger.info("ブラウザを閉じました。")
            except Exception as browser_close_e:
                logger.error(f"ブラウザのクローズ中にエラーが発生しました: {browser_close_e}")
        else:
             logger.debug("ブラウザは接続されていないか、存在しません。")

        if playwright:
            try:
                await playwright.stop()
                logger.info("Playwright を停止しました。")
            except Exception as playwright_stop_e:
                logger.error(f"Playwright の停止中にエラーが発生しました: {playwright_stop_e}")
        try:
            await asyncio.sleep(0.1)
        except Exception as sleep_e:
            logger.warning(f"クリーンアップ後の待機中にエラーが発生しました: {sleep_e}")

    logger.info("--- Playwright 自動化終了 (非同期) ---")
    return all_success, final_results