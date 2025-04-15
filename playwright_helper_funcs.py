# --- ファイル: playwright_helper_funcs.py ---
"""
Playwrightに関連するヘルパー関数 (要素探索以外) を提供します。
例: ページテキスト取得、iframeセレクター生成など。
"""
import asyncio
import logging
import time
from playwright.async_api import (
    Page,
    Frame,
    Locator,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError
)
from typing import Tuple, Optional, Union
from urllib.parse import urljoin

import config
import utils # PDF関連の関数を使用するため

logger = logging.getLogger(__name__)


async def get_page_inner_text(context: BrowserContext, url: str, timeout: int) -> Tuple[bool, Optional[str]]:
    """
    指定されたURLに新しいページでアクセスし、ページのinnerTextを取得する。
    成功したかどうかとテキスト内容（またはエラーメッセージ）のタプルを返す。
    """
    page = None
    start_time = time.monotonic()
    # ページ遷移自体のタイムアウトも考慮
    page_access_timeout = max(int(timeout * 0.8), 15000) # アクションタイムアウトの80%か15秒の大きい方
    logger.info(f"URLからテキスト取得開始: {url} (タイムアウト: {page_access_timeout}ms)")
    try:
        page = await context.new_page()
        # ナビゲーションタイムアウトを設定 (ページアクセスタイムアウトの90%か10秒の大きい方)
        nav_timeout = max(int(page_access_timeout * 0.9), 10000)
        logger.debug(f"  Navigating to {url} with timeout {nav_timeout}ms")
        await page.goto(url, wait_until="load", timeout=nav_timeout)
        logger.debug(f"  Navigation to {url} successful.")

        # <body>要素が表示されるまで待機 (残り時間の50%か2秒の大きい方)
        remaining_time_for_body = page_access_timeout - (time.monotonic() - start_time) * 1000
        if remaining_time_for_body <= 0:
            raise PlaywrightTimeoutError(f"No time left to wait for body after navigation to {url}")
        body_wait_timeout = max(int(remaining_time_for_body * 0.5), 2000)
        logger.debug(f"  Waiting for body element with timeout {body_wait_timeout}ms")
        body_locator = page.locator('body')
        await body_locator.wait_for(state='visible', timeout=body_wait_timeout)
        logger.debug("  Body element is visible.")

        # innerText取得タイムアウト (残り時間の80%か1秒の大きい方)
        remaining_time_for_text = page_access_timeout - (time.monotonic() - start_time) * 1000
        if remaining_time_for_text <= 0:
            raise PlaywrightTimeoutError(f"No time left to get innerText from {url}")
        text_timeout = max(int(remaining_time_for_text * 0.8), 1000)

        logger.debug(f"  Getting innerText with timeout {text_timeout}ms")
        text = await body_locator.inner_text(timeout=text_timeout)
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info(f"テキスト取得成功 ({url})。文字数: {len(text)} ({elapsed:.0f}ms)")
        # 取得したテキストを返す (stripして空白を除去)
        return True, text.strip() if text else ""
    except PlaywrightTimeoutError as e:
        elapsed = (time.monotonic() - start_time) * 1000
        error_msg = f"Error: Timeout during processing {url} ({elapsed:.0f}ms). {e}"
        logger.warning(error_msg)
        # エラーメッセージを返す
        return False, error_msg
    except Exception as e:
        elapsed = (time.monotonic() - start_time) * 1000
        error_msg = f"Error: Failed to get text from {url} ({elapsed:.0f}ms) - {type(e).__name__}: {e}"
        logger.error(error_msg, exc_info=False)
        logger.debug(f"Detailed error getting text from {url}:", exc_info=True)
        # エラーメッセージを返す
        return False, error_msg
    finally:
        if page and not page.is_closed():
            try:
                await page.close()
                logger.debug(f"一時ページ ({url}) を閉じました。")
            except Exception as close_e:
                logger.warning(f"一時ページ ({url}) クローズ中にエラー (無視): {close_e}")


async def generate_iframe_selector_async(iframe_locator: Locator) -> Optional[str]:
    """
    iframe要素のLocatorから、特定しやすいセレクター文字列を生成する試み (id, name, src の順)。
    属性取得は短いタイムアウトで並行実行し、エラーは無視します。
    """
    try:
        # 属性取得を並行実行 (タイムアウト短め: 200ms)
        attrs = await asyncio.gather(
            iframe_locator.get_attribute('id', timeout=200),
            iframe_locator.get_attribute('name', timeout=200),
            iframe_locator.get_attribute('src', timeout=200),
            return_exceptions=True # エラーが発生しても処理を止めない
        )
        # 結果から例外を除外して値を取得
        iframe_id, iframe_name, iframe_src = [a if not isinstance(a, Exception) else None for a in attrs]

        # 優先度順にセレクターを生成
        if iframe_id:
            return f'iframe[id="{iframe_id}"]'
        if iframe_name:
            return f'iframe[name="{iframe_name}"]'
        if iframe_src:
            # srcは長すぎる場合があるので注意が必要だが、特定には役立つ場合がある
            return f'iframe[src="{iframe_src}"]'

    except Exception as e:
        # 属性取得中のエラーはデバッグレベルでログ記録し、無視
        logger.debug(f"iframe属性取得中にエラー（無視）: {e}")

    # 適切なセレクターが見つからなければNoneを返す
    return None