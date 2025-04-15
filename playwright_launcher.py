# --- ファイル: playwright_launcher.py ---
"""
Playwrightの起動、初期設定、アクション実行の呼び出し、終了処理を行います。
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
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
)
from playwright_stealth import stealth_async
from typing import List, Tuple, Dict, Any

import config
from playwright_actions import execute_actions_async # アクション実行関数をインポート

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport") # Playwrightの既知の警告を抑制

async def run_playwright_automation_async(
        target_url: str,
        actions: List[Dict[str, Any]],
        headless_mode: bool = False,
        slow_motion: int = 100,
        default_timeout: int = config.DEFAULT_ACTION_TIMEOUT
    ) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Playwright を非同期で初期化し、指定されたURLにアクセス後、一連のアクションを実行します。
    ブラウザの起動、コンテキスト設定、ページ作成、アクション実行、クリーンアップを行います。
    実行全体の成否 (bool) と、各ステップの結果詳細のリスト (List[dict]) を返します。
    """
    logger.info("--- Playwright 自動化開始 (非同期) ---")
    all_success = False
    final_results: List[Dict[str, Any]] = []
    playwright = None
    browser = None
    context: Optional[BrowserContext] = None # Optional に変更
    page: Optional[Page] = None # Optional に変更

    try:
        playwright = await async_playwright().start()
        logger.info(f"ブラウザ起動 (Chromium, Headless: {headless_mode}, SlowMo: {slow_motion}ms)...")
        # 起動オプション (必要に応じて追加)
        launch_options = {
            "headless": headless_mode,
            "slow_mo": slow_motion,
            # "args": ["--disable-blink-features=AutomationControlled"] # Stealthで不要になる可能性
        }
        browser = await playwright.chromium.launch(**launch_options)
        logger.info("新しいブラウザコンテキストを作成します...")
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36', # 一般的なUA
            viewport={'width': 1920, 'height': 1080}, # 一般的な解像度
            locale='ja-JP', # 日本語ロケール
            timezone_id='Asia/Tokyo', # 日本時間
            # accept_downloads=True, # ファイルダウンロードを許可する場合
            java_script_enabled=True, # JavaScript有効
            # ページの読み込み戦略 (デフォルトは 'load')
            # navigation_timeout= ..., # ナビゲーション全体のタイムアウト (デフォルトは30秒)
            extra_http_headers={'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'} # Accept-Languageヘッダ
        )
        # デフォルトタイムアウト設定 (コンテキスト全体)
        effective_default_timeout = default_timeout if default_timeout else config.DEFAULT_ACTION_TIMEOUT
        context.set_default_timeout(effective_default_timeout)
        logger.info(f"コンテキストのデフォルトタイムアウトを {effective_default_timeout}ms に設定しました。")

        # --- Stealth モード適用 ---
        logger.info("Applying stealth mode to the context...")
        try:
            await stealth_async(context)
            logger.info("Stealth mode applied successfully.")
        except Exception as stealth_err:
             logger.warning(f"Failed to apply stealth mode: {stealth_err}")

        # APIリクエスト用のコンテキスト (PDFダウンロード等で使用)
        api_request_context = context.request

        logger.info("新しいページを作成します...")
        page = await context.new_page()

        # 最初のページへのナビゲーション (タイムアウトを長めに設定)
        initial_nav_timeout = max(effective_default_timeout * 3, 30000) # デフォルトの3倍か30秒の長い方
        logger.info(f"最初のナビゲーション: {target_url} (タイムアウト: {initial_nav_timeout}ms)...")
        # wait_until="load" は、load イベントが発火するまで待つ
        # 他に "domcontentloaded", "networkidle" などがある
        await page.goto(target_url, wait_until="load", timeout=initial_nav_timeout)
        logger.info("最初のナビゲーション成功。アクションの実行を開始します...")

        # --- アクション実行 (playwright_actions.pyの関数を呼び出し) ---
        all_success, final_results = await execute_actions_async(
            page, actions, api_request_context, effective_default_timeout
        )

        if all_success:
            logger.info("すべてのステップが正常に完了しました。")
        else:
            logger.error("自動化タスクの途中でエラーが発生しました。")

    # --- 全体的なエラーハンドリング ---
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as e:
         error_msg_overall = f"Playwright 処理全体で予期せぬエラーが発生しました: {type(e).__name__} - {e}"
         logger.error(error_msg_overall, exc_info=True) # スタックトレース付きログ
         overall_error_screenshot_path = None
         # エラー発生時のスクリーンショット (ページが存在すれば)
         if page and not page.is_closed():
              timestamp = time.strftime("%Y%m%d_%H%M%S")
              overall_error_ss_filename = f"error_overall_{timestamp}.png"
              overall_error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, overall_error_ss_filename)
              try:
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  await page.screenshot(path=overall_error_ss_path, full_page=True, timeout=10000) # タイムアウト短め
                  logger.info(f"全体エラー発生時のスクリーンショットを保存しました: {overall_error_ss_path}")
                  overall_error_screenshot_path = overall_error_ss_path
              except Exception as ss_e:
                  logger.error(f"全体エラー発生時のスクリーンショット保存に失敗しました: {ss_e}")
         # 最終結果リストに全体エラー情報を追加 (既に追加されていなければ)
         if not final_results or (isinstance(final_results[-1].get("status"), str) and final_results[-1].get("status") != "error") :
             error_details = {
                 "step": "Overall Execution", # ステップ名を明確化
                 "status": "error",
                 "message": str(e),
                 "full_error": error_msg_overall,
                 "traceback": traceback.format_exc() # トレースバックも追加
             }
             if overall_error_screenshot_path:
                 error_details["error_screenshot"] = overall_error_screenshot_path
             final_results.append(error_details)
         all_success = False # 全体エラーなので失敗扱い

    # --- クリーンアップ処理 ---
    finally:
        logger.info("クリーンアップ処理を開始します...")
        # コンテキスト -> ブラウザ -> Playwright の順で閉じる
        if context: # コンテキストが存在する場合のみ閉じる
            try:
                await context.close()
                logger.info("ブラウザコンテキストを閉じました。")
            except Exception as context_close_e:
                # 既に閉じられている場合などのエラーは警告レベルに留める
                # PlaywrightError: Context closed << などは無視して良い
                if "closed" not in str(context_close_e).lower():
                    logger.warning(f"ブラウザコンテキストのクローズ中にエラーが発生しました (無視): {context_close_e}")
        else:
             logger.debug("ブラウザコンテキストは存在しません (既に閉じられたか、作成されませんでした)。")

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

        # イベントループのクリーンアップを促すための短い待機
        try:
            await asyncio.sleep(0.1)
        except Exception as sleep_e:
            logger.warning(f"クリーンアップ後の待機中にエラーが発生しました: {sleep_e}")

    logger.info("--- Playwright 自動化終了 (非同期) ---")
    return all_success, final_results