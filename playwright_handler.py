# --- ファイル: playwright_handler.py ---
"""
Playwrightを使ったブラウザ操作とアクション実行のコアロジック。
"""
import asyncio
import logging
import os
import time
import pprint
import traceback
import warnings
from collections import deque
from playwright.async_api import (
    async_playwright,
    Page,
    Frame,
    Locator,
    FrameLocator,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    APIRequestContext
)
from playwright_stealth import stealth_async
from typing import List, Tuple, Optional, Union, Any, Deque, Dict
from urllib.parse import urljoin

import config
import utils

logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")


# --- 動的要素探索ヘルパー関数 (単一要素用 - 変更なし) ---
async def find_element_dynamically(
    base_locator: Union[Page, FrameLocator],
    target_selector: str,
    max_depth: int = config.DYNAMIC_SEARCH_MAX_DEPTH,
    timeout: int = config.DEFAULT_ACTION_TIMEOUT,
    target_state: str = "attached"
) -> Tuple[Optional[Locator], Optional[Union[Page, FrameLocator]]]:
    logger.info(f"動的探索(単一)開始: 起点={type(base_locator).__name__}, セレクター='{target_selector}', 最大深度={max_depth}, 状態='{target_state}', 全体タイムアウト={timeout}ms")
    start_time = time.monotonic()
    queue: Deque[Tuple[Union[Page, FrameLocator], int]] = deque([(base_locator, 0)])
    visited_scope_ids = {id(base_locator)}
    element_wait_timeout = 2000
    iframe_check_timeout = config.IFRAME_LOCATOR_TIMEOUT
    logger.debug(f"  要素待機タイムアウト: {element_wait_timeout}ms, フレーム確認タイムアウト: {iframe_check_timeout}ms")

    while queue:
        current_monotonic_time = time.monotonic()
        elapsed_time_ms = (current_monotonic_time - start_time) * 1000
        if elapsed_time_ms >= timeout:
            logger.warning(f"動的探索(単一)タイムアウト ({timeout}ms) - 経過時間: {elapsed_time_ms:.0f}ms")
            return None, None
        remaining_time_ms = timeout - elapsed_time_ms
        if remaining_time_ms < 100:
             logger.warning(f"動的探索(単一)の残り時間がわずかなため ({remaining_time_ms:.0f}ms)、探索を打ち切ります。")
             return None, None

        current_scope, current_depth = queue.popleft()
        scope_type_name = type(current_scope).__name__
        scope_identifier = f" ({repr(current_scope)})" if isinstance(current_scope, FrameLocator) else ""
        logger.debug(f"  探索中(単一): スコープ={scope_type_name}{scope_identifier}, 深度={current_depth}, 残り時間: {remaining_time_ms:.0f}ms")

        step_start_time = time.monotonic()
        try:
            element = current_scope.locator(target_selector).first
            effective_element_timeout = max(50, min(element_wait_timeout, int(remaining_time_ms - 50)))
            await element.wait_for(state=target_state, timeout=effective_element_timeout)
            step_elapsed = (time.monotonic() - step_start_time) * 1000
            logger.info(f"要素 '{target_selector}' をスコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) で発見。({step_elapsed:.0f}ms)")
            return element, current_scope
        except PlaywrightTimeoutError:
            step_elapsed = (time.monotonic() - step_start_time) * 1000
            logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' 直下では見つからず (タイムアウト {effective_element_timeout}ms)。({step_elapsed:.0f}ms)")
        except Exception as e:
            step_elapsed = (time.monotonic() - step_start_time) * 1000
            logger.warning(f"    スコープ '{scope_type_name}{scope_identifier}' での要素 '{target_selector}' 探索中にエラー: {type(e).__name__} - {e} ({step_elapsed:.0f}ms)")

        if current_depth < max_depth:
            current_monotonic_time = time.monotonic()
            elapsed_time_ms = (current_monotonic_time - start_time) * 1000
            if elapsed_time_ms >= timeout: continue
            remaining_time_ms = timeout - elapsed_time_ms
            if remaining_time_ms < 100: continue

            step_start_time = time.monotonic()
            logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) 内の可視iframeを探索...")
            try:
                iframe_base_selector = 'iframe:visible'
                visible_iframe_locators = current_scope.locator(iframe_base_selector)
                count = await visible_iframe_locators.count()
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                if count > 0: logger.debug(f"      発見した可視iframe候補数: {count} ({step_elapsed:.0f}ms)")

                for i in range(count):
                    current_monotonic_time_inner = time.monotonic()
                    elapsed_time_ms_inner = (current_monotonic_time_inner - start_time) * 1000
                    if elapsed_time_ms_inner >= timeout:
                         logger.warning(f"動的探索(単一)タイムアウト ({timeout}ms) - iframeループ中")
                         break
                    remaining_time_ms_inner = timeout - elapsed_time_ms_inner
                    if remaining_time_ms_inner < 50: break

                    iframe_step_start_time = time.monotonic()
                    try:
                        nth_iframe_selector = f"{iframe_base_selector} >> nth={i}"
                        next_frame_locator = current_scope.frame_locator(nth_iframe_selector)
                        effective_iframe_check_timeout = max(50, min(iframe_check_timeout, int(remaining_time_ms_inner - 50)))
                        await next_frame_locator.locator(':root').wait_for(state='attached', timeout=effective_iframe_check_timeout)
                        scope_id = id(next_frame_locator)
                        if scope_id not in visited_scope_ids:
                            visited_scope_ids.add(scope_id)
                            queue.append((next_frame_locator, current_depth + 1))
                            iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                            logger.debug(f"        キューに追加(単一): スコープ=FrameLocator(nth={i}), 新深度={current_depth + 1} ({iframe_step_elapsed:.0f}ms)")
                    except PlaywrightTimeoutError:
                         iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                         logger.debug(f"      iframe {i} ('{nth_iframe_selector}') は有効でないかタイムアウト ({effective_iframe_check_timeout}ms)。({iframe_step_elapsed:.0f}ms)")
                    except Exception as e:
                         iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                         logger.warning(f"      iframe {i} ('{nth_iframe_selector}') の処理中にエラー: {type(e).__name__} - {e} ({iframe_step_elapsed:.0f}ms)")
            except Exception as e:
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                logger.error(f"    スコープ '{scope_type_name}{scope_identifier}' でのiframe探索中に予期せぬエラー: {type(e).__name__} - {e} ({step_elapsed:.0f}ms)", exc_info=True)

    final_elapsed_time = (time.monotonic() - start_time) * 1000
    logger.warning(f"動的探索(単一)完了: 要素 '{target_selector}' が最大深度 {max_depth} までで見つかりませんでした。({final_elapsed_time:.0f}ms)")
    return None, None

# --- 動的要素探索ヘルパー関数 (複数要素用 - 変更なし) ---
async def find_all_elements_dynamically(
    base_locator: Union[Page, FrameLocator],
    target_selector: str,
    max_depth: int = config.DYNAMIC_SEARCH_MAX_DEPTH,
    timeout: int = config.DEFAULT_ACTION_TIMEOUT,
) -> List[Tuple[Locator, Union[Page, FrameLocator]]]:
    logger.info(f"動的探索(複数)開始: 起点={type(base_locator).__name__}, セレクター='{target_selector}', 最大深度={max_depth}, 全体タイムアウト={timeout}ms")
    start_time = time.monotonic()
    found_elements: List[Tuple[Locator, Union[Page, FrameLocator]]] = []
    queue: Deque[Tuple[Union[Page, FrameLocator], int]] = deque([(base_locator, 0)])
    visited_scope_ids = {id(base_locator)}
    iframe_check_timeout = config.IFRAME_LOCATOR_TIMEOUT
    logger.debug(f"  フレーム確認タイムアウト: {iframe_check_timeout}ms")

    while queue:
        current_monotonic_time = time.monotonic()
        elapsed_time_ms = (current_monotonic_time - start_time) * 1000
        if elapsed_time_ms >= timeout:
            logger.warning(f"動的探索(複数)タイムアウト ({timeout}ms) - 経過時間: {elapsed_time_ms:.0f}ms")
            break
        remaining_time_ms = timeout - elapsed_time_ms
        if remaining_time_ms < 100:
             logger.warning(f"動的探索(複数)の残り時間がわずかなため ({remaining_time_ms:.0f}ms)、探索を打ち切ります。")
             break

        current_scope, current_depth = queue.popleft()
        scope_type_name = type(current_scope).__name__
        scope_identifier = f" ({repr(current_scope)})" if isinstance(current_scope, FrameLocator) else ""
        logger.debug(f"  探索中(複数): スコープ={scope_type_name}{scope_identifier}, 深度={current_depth}, 残り時間: {remaining_time_ms:.0f}ms")

        step_start_time = time.monotonic()
        try:
            # 要素が表示されているかに関わらず全て取得する
            elements_in_scope = await current_scope.locator(target_selector).all()
            step_elapsed = (time.monotonic() - step_start_time) * 1000
            if elements_in_scope:
                logger.info(f"  スコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) で {len(elements_in_scope)} 個の要素を発見。({step_elapsed:.0f}ms)")
                for elem in elements_in_scope:
                    found_elements.append((elem, current_scope))
            else:
                logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' 直下では要素が見つからず。({step_elapsed:.0f}ms)")
        except Exception as e:
            step_elapsed = (time.monotonic() - step_start_time) * 1000
            logger.warning(f"    スコープ '{scope_type_name}{scope_identifier}' での要素 '{target_selector}' 複数探索中にエラー: {type(e).__name__} - {e} ({step_elapsed:.0f}ms)")

        if current_depth < max_depth:
            current_monotonic_time = time.monotonic()
            elapsed_time_ms = (current_monotonic_time - start_time) * 1000
            if elapsed_time_ms >= timeout: continue
            remaining_time_ms = timeout - elapsed_time_ms
            if remaining_time_ms < 100: continue

            step_start_time = time.monotonic()
            logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) 内の可視iframeを探索...")
            try:
                # iframe自体は可視である必要がある
                iframe_base_selector = 'iframe:visible'
                visible_iframe_locators = current_scope.locator(iframe_base_selector)
                count = await visible_iframe_locators.count()
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                if count > 0: logger.debug(f"      発見した可視iframe候補数: {count} ({step_elapsed:.0f}ms)")

                for i in range(count):
                    current_monotonic_time_inner = time.monotonic()
                    elapsed_time_ms_inner = (current_monotonic_time_inner - start_time) * 1000
                    if elapsed_time_ms_inner >= timeout:
                         logger.warning(f"動的探索(複数)タイムアウト ({timeout}ms) - iframeループ中")
                         break
                    remaining_time_ms_inner = timeout - elapsed_time_ms_inner
                    if remaining_time_ms_inner < 50: break

                    iframe_step_start_time = time.monotonic()
                    try:
                        nth_iframe_selector = f"{iframe_base_selector} >> nth={i}"
                        next_frame_locator = current_scope.frame_locator(nth_iframe_selector)
                        effective_iframe_check_timeout = max(50, min(iframe_check_timeout, int(remaining_time_ms_inner - 50)))
                        # iframe内のルート要素が存在するかどうかを確認
                        await next_frame_locator.locator(':root').wait_for(state='attached', timeout=effective_iframe_check_timeout)
                        scope_id = id(next_frame_locator)
                        if scope_id not in visited_scope_ids:
                            visited_scope_ids.add(scope_id)
                            queue.append((next_frame_locator, current_depth + 1))
                            iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                            logger.debug(f"        キューに追加(複数): スコープ=FrameLocator(nth={i}), 新深度={current_depth + 1} ({iframe_step_elapsed:.0f}ms)")
                    except PlaywrightTimeoutError:
                         iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                         logger.debug(f"      iframe {i} ('{nth_iframe_selector}') は有効でないかタイムアウト ({effective_iframe_check_timeout}ms)。({iframe_step_elapsed:.0f}ms)")
                    except Exception as e:
                         iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                         logger.warning(f"      iframe {i} ('{nth_iframe_selector}') の処理中にエラー: {type(e).__name__} - {e} ({iframe_step_elapsed:.0f}ms)")
            except Exception as e:
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                logger.error(f"    スコープ '{scope_type_name}{scope_identifier}' でのiframe探索中に予期せぬエラー: {type(e).__name__} - {e} ({step_elapsed:.0f}ms)", exc_info=True)

    final_elapsed_time = (time.monotonic() - start_time) * 1000
    logger.info(f"動的探索(複数)完了: 合計 {len(found_elements)} 個の要素が見つかりました。({final_elapsed_time:.0f}ms)")
    return found_elements


# --- ページ内テキスト取得ヘルパー関数 (変更なし) ---
async def get_page_inner_text(context: BrowserContext, url: str, timeout: int) -> Tuple[bool, Optional[str]]:
    """
    指定されたURLにアクセスし、ページのinnerTextを取得する。
    成功したかどうかとテキスト内容（またはエラーメッセージ）のタプルを返す。
    """
    page = None
    start_time = time.monotonic()
    # ページ遷移自体のタイムアウトも考慮
    page_access_timeout = max(int(timeout * 0.8), 15000) # アクションタイムアウトの80%か15秒の大きい方
    logger.info(f"URLからテキスト取得開始: {url} (タイムアウト: {page_access_timeout}ms)")
    try:
        page = await context.new_page()
        # ナビゲーションタイムアウトを設定
        nav_timeout = max(int(page_access_timeout * 0.9), 10000)
        logger.debug(f"  Navigating to {url} with timeout {nav_timeout}ms")
        await page.goto(url, wait_until="load", timeout=nav_timeout)
        logger.debug(f"  Navigation to {url} successful.")

        # <body>要素が表示されるまで待機
        remaining_time_for_body = page_access_timeout - (time.monotonic() - start_time) * 1000
        body_wait_timeout = max(int(remaining_time_for_body * 0.5), 2000)
        if remaining_time_for_body <= 0:
            raise PlaywrightTimeoutError(f"No time left to wait for body after navigation to {url}")
        logger.debug(f"  Waiting for body element with timeout {body_wait_timeout}ms")
        body_locator = page.locator('body')
        await body_locator.wait_for(state='visible', timeout=body_wait_timeout)
        logger.debug("  Body element is visible.")

        # innerText取得タイムアウト
        remaining_time_for_text = page_access_timeout - (time.monotonic() - start_time) * 1000
        text_timeout = max(int(remaining_time_for_text * 0.8), 1000)
        if remaining_time_for_text <= 0:
            raise PlaywrightTimeoutError(f"No time left to get innerText from {url}")

        logger.debug(f"  Getting innerText with timeout {text_timeout}ms")
        text = await body_locator.inner_text(timeout=text_timeout)
        elapsed = (time.monotonic() - start_time) * 1000
        logger.info(f"テキスト取得成功 ({url})。文字数: {len(text)} ({elapsed:.0f}ms)")
        # 取得したテキストを返す
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


# --- Playwright アクション実行コア (変更なし) ---
async def execute_actions_async(initial_page: Page, actions: List[dict], api_request_context: APIRequestContext, default_timeout: int) -> Tuple[bool, List[dict]]:
    """Playwright アクションを非同期で実行する。iframe探索を動的に行う。"""
    results: List[dict] = []
    current_target: Union[Page, FrameLocator] = initial_page
    root_page: Page = initial_page
    current_context: BrowserContext = root_page.context
    iframe_stack: List[Union[Page, FrameLocator]] = []

    for i, step_data in enumerate(actions):
        step_num = i + 1
        action = step_data.get("action", "").lower()
        selector = step_data.get("selector")
        iframe_selector_input = step_data.get("iframe_selector")
        value = step_data.get("value")
        attribute_name = step_data.get("attribute_name")
        option_type = step_data.get("option_type")
        option_value = step_data.get("option_value")
        # アクション固有のタイムアウト > JSON全体のデフォルト > configのデフォルト
        action_wait_time = step_data.get("wait_time_ms", default_timeout)

        logger.info(f"--- ステップ {step_num}/{len(actions)}: Action='{action}' ---")
        step_info = {"selector": selector, "value": value, "iframe(指定)": iframe_selector_input,
                     "option_type": option_type, "option_value": option_value, "attribute_name": attribute_name}
        step_info_str = ", ".join([f"{k}='{v}'" for k, v in step_info.items() if v is not None])
        logger.info(f"詳細: {step_info_str} (timeout: {action_wait_time}ms)")

        try:
            if root_page.is_closed():
                raise PlaywrightError("Root page is closed.")
            current_base_url = root_page.url # 現在のページのベースURLを取得
            root_page_title = await root_page.title()
            current_target_type = type(current_target).__name__
            logger.info(f"現在のルートページ: URL='{current_base_url}', Title='{root_page_title}'")
            logger.info(f"現在の探索スコープ: {current_target_type}")
        except Exception as e:
            logger.error(f"現在のターゲット情報取得中にエラー: {e}", exc_info=True)
            results.append({"step": step_num, "status": "error", "action": action, "message": f"Failed to get target info: {e}"})
            return False, results

        try:
            # --- Iframe/Parent Frame 切替 (変更なし) ---
            if action == "switch_to_iframe":
                if not iframe_selector_input:
                    raise ValueError("Action 'switch_to_iframe' requires 'iframe_selector'")
                logger.info(f"[ユーザー指定] Iframe '{iframe_selector_input}' に切り替えます...")
                target_frame_locator: Optional[FrameLocator] = None
                try:
                    # 現在のターゲットから iframe を探す
                    target_frame_locator = current_target.frame_locator(iframe_selector_input)
                    # iframeが存在し、アクセス可能か確認 (ルート要素の存在確認)
                    await target_frame_locator.locator(':root').wait_for(state='attached', timeout=action_wait_time)
                except PlaywrightTimeoutError:
                    # logger.debug(f"現在のスコープ({type(current_target).__name__})に指定iframeなし。ルートページから再検索...")
                    # try:
                    #     target_frame_locator = root_page.frame_locator(iframe_selector_input)
                    #     await target_frame_locator.locator(':root').wait_for(state='attached', timeout=max(1000, action_wait_time // 2))
                    # except PlaywrightTimeoutError:
                        raise PlaywrightTimeoutError(f"指定されたiframe '{iframe_selector_input}' が見つからないか、タイムアウト({action_wait_time}ms)しました。")
                except Exception as e:
                    raise PlaywrightError(f"Iframe '{iframe_selector_input}' への切り替え中に予期せぬエラーが発生しました: {e}")

                # 切り替え成功
                if id(current_target) not in [id(s) for s in iframe_stack]: # 重複を避ける
                    iframe_stack.append(current_target) # 現在のターゲットをスタックに追加
                current_target = target_frame_locator # ターゲットを新しい FrameLocator に更新
                logger.info("FrameLocator への切り替え成功。")
                results.append({"step": step_num, "status": "success", "action": action, "selector": iframe_selector_input})
                continue # 次のステップへ

            elif action == "switch_to_parent_frame":
                if not iframe_stack:
                    logger.warning("既にトップレベルフレームか、iframeスタックが空です。")
                    # FrameLocatorの場合のみルートページに戻す（安全策）
                    if isinstance(current_target, FrameLocator):
                        logger.info("現在のターゲットがFrameLocatorのため、ルートページに戻します。")
                        current_target = root_page
                    results.append({"step": step_num, "status": "warning", "action": action, "message": "Already at top-level or stack empty."})
                else:
                    logger.info("[ユーザー指定] 親ターゲットに戻ります...")
                    current_target = iframe_stack.pop() # スタックから親ターゲットを取り出す
                    target_type = type(current_target).__name__
                    logger.info(f"親ターゲットへの切り替え成功。現在の探索スコープ: {target_type}")
                    results.append({"step": step_num, "status": "success", "action": action})
                continue # 次のステップへ


            # --- ページ全体操作 (変更なし) ---
            if action in ["wait_page_load", "sleep", "scroll_page_to_bottom"]:
                if action == "wait_page_load":
                    logger.info("ページの読み込み完了 (load) を待ちます...")
                    await root_page.wait_for_load_state("load", timeout=action_wait_time)
                    logger.info("ページの読み込みが完了しました。")
                    results.append({"step": step_num, "status": "success", "action": action})
                elif action == "sleep":
                    try:
                        seconds = float(value) if value is not None else 1.0
                        if seconds < 0: raise ValueError("Sleep time cannot be negative.")
                    except (TypeError, ValueError):
                        raise ValueError("Invalid value for sleep action. Must be a non-negative number (seconds).")
                    logger.info(f"{seconds:.1f} 秒待機します...")
                    await asyncio.sleep(seconds)
                    results.append({"step": step_num, "status": "success", "action": action, "duration_sec": seconds})
                elif action == "scroll_page_to_bottom":
                    logger.info("ページ最下部へスクロールします...")
                    await root_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(0.5) # スクロール後の描画待ち
                    logger.info("ページ最下部へのスクロールが完了しました。")
                    results.append({"step": step_num, "status": "success", "action": action})
                continue # 次のステップへ


            # --- 要素操作のための準備 ---
            element: Optional[Locator] = None
            found_elements_list: List[Tuple[Locator, Union[Page, FrameLocator]]] = []
            found_scope: Optional[Union[Page, FrameLocator]] = None

            # アクションが単一要素を必要とするか、複数要素を対象とするか
            single_element_required_actions = ["click", "input", "hover", "get_inner_text", "get_text_content", "get_inner_html", "get_attribute", "wait_visible", "select_option", "scroll_to_element"]
            multiple_elements_actions = ["get_all_attributes", "get_all_text_contents"]
            is_single_element_required = action in single_element_required_actions
            is_multiple_elements_action = action in multiple_elements_actions
            # スクリーンショットは要素指定がある場合のみ要素探索が必要
            is_screenshot_element = action == "screenshot" and selector is not None

            if is_single_element_required or is_multiple_elements_action or is_screenshot_element:
                if not selector:
                    raise ValueError(f"Action '{action}' requires a 'selector'.")

                # --- 要素探索 ---
                if is_single_element_required or is_screenshot_element:
                    # 探索する要素の状態を決定
                    required_state = 'visible' if action in ['click', 'hover', 'screenshot', 'select_option', 'input', 'get_inner_text', 'wait_visible', 'scroll_to_element'] else 'attached'
                    element, found_scope = await find_element_dynamically(
                        current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time, target_state=required_state
                    )
                    if not element or not found_scope:
                        error_msg = f"要素 '{selector}' (状態: {required_state}) が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。"
                        logger.error(error_msg)
                        # エラー結果を追加して終了
                        results.append({"step": step_num, "status": "error", "action": action, "selector": selector, "message": error_msg})
                        return False, results # Falseを返して処理中断

                    # 要素が見つかったスコープが現在のスコープと異なる場合、ターゲットを更新
                    if id(found_scope) != id(current_target):
                        logger.info(f"探索スコープを要素が見つかった '{type(found_scope).__name__}' に更新します。")
                        if id(current_target) not in [id(s) for s in iframe_stack]:
                            iframe_stack.append(current_target) # 元のターゲットをスタックに追加
                        current_target = found_scope
                    logger.info(f"最終的な単一操作対象スコープ: {type(current_target).__name__}")

                elif is_multiple_elements_action:
                    found_elements_list = await find_all_elements_dynamically(
                        current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time
                    )
                    if not found_elements_list:
                        # 要素が見つからなくてもエラーとはせず、警告ログのみ（後続処理で空リストとして扱う）
                        logger.warning(f"要素 '{selector}' が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。")
                    # 複数要素の場合、見つかった各要素のスコープは異なる可能性があるため、current_target は更新しない


            # --- 各アクション実行 ---
            action_result_details = {"selector": selector} if selector else {} # 結果にセレクター情報を含める

            if action == "click":
                if not element: raise ValueError("Click action requires an element, but it was not found or assigned.")
                logger.info("要素をクリックします...")
                context = root_page.context # クリックによるページ遷移を検知するためルートページのコンテキストを使用
                new_page: Optional[Page] = None
                try:
                    # 新しいページが開く可能性を考慮して待機
                    async with context.expect_page(timeout=config.NEW_PAGE_EVENT_TIMEOUT) as new_page_info:
                        await element.click(timeout=action_wait_time)
                    # タイムアウト内に新しいページが開いた場合
                    new_page = await new_page_info.value
                    new_page_url = new_page.url
                    logger.info(f"新しいページが開きました: URL={new_page_url}")
                    try:
                        # 新しいページのロード完了を待つ
                        await new_page.wait_for_load_state("load", timeout=action_wait_time)
                    except PlaywrightTimeoutError:
                        logger.warning(f"新しいページのロード待機がタイムアウトしました ({action_wait_time}ms)。")
                    # 操作対象を新しいページに切り替え、iframeスタックをクリア
                    root_page = new_page
                    current_target = new_page
                    current_context = new_page.context
                    iframe_stack.clear()
                    logger.info("スコープを新しいページにリセットしました。")
                    action_result_details.update({"new_page_opened": True, "new_page_url": new_page_url})
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})
                except PlaywrightTimeoutError:
                    # expect_pageがタイムアウトした場合 (新しいページが開かなかった場合)
                    logger.info(f"クリックは完了しましたが、{config.NEW_PAGE_EVENT_TIMEOUT}ms 以内に新しいページは開きませんでした。")
                    action_result_details["new_page_opened"] = False
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "input":
                 if not element: raise ValueError("Input action requires an element.")
                 if value is None: raise ValueError("Input action requires 'value'.")
                 logger.info(f"要素に '{str(value)[:50]}{'...' if len(str(value)) > 50 else ''}' を入力します...") # 長い値は省略してログ表示
                 await element.fill(str(value), timeout=action_wait_time)
                 logger.info("入力が成功しました。")
                 action_result_details["value"] = value # 結果には元の値を保持
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "hover":
                 if not element: raise ValueError("Hover action requires an element.")
                 logger.info("要素にマウスオーバーします...")
                 await element.hover(timeout=action_wait_time)
                 logger.info("ホバーが成功しました。")
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_inner_text":
                 if not element: raise ValueError("Get text action requires an element.")
                 logger.info("要素の innerText を取得します...")
                 text = await element.inner_text(timeout=action_wait_time)
                 logger.info(f"取得テキスト(innerText): '{text[:100]}{'...' if len(text) > 100 else ''}'") # 長いテキストは省略
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_text_content":
                 if not element: raise ValueError("Get text action requires an element.")
                 logger.info("要素の textContent を取得します...")
                 text = await element.text_content(timeout=action_wait_time)
                 text = text.strip() if text else "" # 前後の空白を除去
                 logger.info(f"取得テキスト(textContent): '{text[:100]}{'...' if len(text) > 100 else ''}'")
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_inner_html":
                 if not element: raise ValueError("Get HTML action requires an element.")
                 logger.info("要素の innerHTML を取得します...")
                 html_content = await element.inner_html(timeout=action_wait_time)
                 logger.info(f"取得HTML(innerHTML):\n{html_content[:500]}{'...' if len(html_content) > 500 else ''}") # 先頭のみ表示
                 action_result_details["html"] = html_content
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_attribute":
                if not element: raise ValueError("Get attribute action requires an element.")
                if not attribute_name: raise ValueError("Action 'get_attribute' requires 'attribute_name'.")
                logger.info(f"要素の属性 '{attribute_name}' を取得します...")
                attr_value = await element.get_attribute(attribute_name, timeout=action_wait_time)
                pdf_text_content = None
                processed_url = attr_value # 変換後のURLも保持

                # href属性の場合、絶対URLに変換し、PDFなら内容を取得
                if attribute_name.lower() == 'href' and attr_value is not None:
                    original_url = attr_value
                    try:
                        absolute_url = urljoin(current_base_url, original_url) # 相対URLを絶対URLに
                        if original_url != absolute_url:
                            logger.info(f"  href属性値を絶対URLに変換: '{original_url}' -> '{absolute_url}'")
                        processed_url = absolute_url # 変換後のURLを保持

                        # PDFかどうかを判定して処理
                        if isinstance(absolute_url, str) and absolute_url.lower().endswith('.pdf'):
                            logger.info(f"  リンク先がPDFファイルです。ダウンロードとテキスト抽出を試みます: {absolute_url}")
                            pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                            if pdf_bytes:
                                # 同期関数を非同期で実行
                                pdf_text_content = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                logger.info(f"  PDFテキスト抽出完了 (先頭200文字): {pdf_text_content[:200] if pdf_text_content else 'None'}...")
                            else:
                                pdf_text_content = "Error: PDF download failed or returned no data."
                                logger.error(f"  PDFダウンロード失敗: {absolute_url}")
                        else:
                             logger.debug(f"  リンク先はPDFではありません ({absolute_url})。")
                    except Exception as url_e:
                        logger.error(f"  URL処理またはPDF処理中にエラーが発生しました (URL: '{original_url}'): {url_e}")
                        pdf_text_content = f"Error processing URL or PDF: {url_e}"

                logger.info(f"取得した属性値 ({attribute_name}): '{processed_url}'") # 変換後のURLを表示
                action_result_details.update({"attribute": attribute_name, "value": processed_url}) # 結果には処理後のURLを保存
                if pdf_text_content is not None:
                    action_result_details["pdf_text"] = pdf_text_content # PDFテキストも結果に含める
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})


            # --- get_all_attributes (変更なし) ---
            elif action == "get_all_attributes":
                if not selector: raise ValueError("Action 'get_all_attributes' requires 'selector'.")
                if not attribute_name: raise ValueError("Action 'get_all_attributes' requires 'attribute_name'.")

                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、属性取得をスキップします。")
                    action_result_details["attribute_list"] = [] # 空リストを結果として設定
                else:
                    logger.info(f"動的探索で見つかった {len(found_elements_list)} 個の要素から属性 '{attribute_name}' を取得します。")

                    # --- 結果格納用のリスト ---
                    url_list_for_file: List[Optional[str]] = []       # 最終的な結果ファイル用 (絶対URL)
                    pdf_texts_list_for_file: List[Optional[str]] = [] # 最終的な結果ファイル用
                    scraped_texts_list_for_file: List[Optional[str]] = [] # 最終的な結果ファイル用
                    generic_attribute_list_for_file: List[Optional[str]] = [] # 最終的な結果ファイル用

                    # --- href属性を取得する内部関数 ---
                    async def get_single_href(locator: Locator, index: int) -> Optional[str]:
                         try:
                             href = await locator.get_attribute("href", timeout=max(500, action_wait_time // 5)) # 個々のタイムアウトは短めに
                             return href
                         except Exception as e:
                             logger.warning(f"  要素 {index+1} の href 属性取得中にエラー: {type(e).__name__}")
                             return None

                    # --- 属性名に応じて処理 ---
                    if attribute_name.lower() in ['href', 'pdf', 'content']:
                        logger.info("href属性を取得し、絶対URLに変換します...")
                        # まず全要素からhref属性(元URL)を取得
                        original_href_list: List[Optional[str]] = []
                        for idx, (loc, _) in enumerate(found_elements_list):
                             original_href = await get_single_href(loc, idx)
                             original_href_list.append(original_href)

                        # 逐次処理のための準備
                        absolute_urls_processed: List[Optional[str]] = [None] * len(original_href_list)

                        logger.info(f"--- Processing {len(original_href_list)} URLs for '{attribute_name.lower()}' ---")
                        process_start_time = time.monotonic()

                        for idx, original_url in enumerate(original_href_list):
                            item_start_time = time.monotonic()
                            if original_url is None:
                                logger.info(f"[{idx+1}/{len(original_href_list)}] URL: None (Skipping)")
                                url_list_for_file.append(None)
                                pdf_texts_list_for_file.append(None)
                                scraped_texts_list_for_file.append(None)
                                continue

                            abs_url: Optional[str] = None
                            try:
                                abs_url = urljoin(current_base_url, original_url)
                                logger.info(f"[{idx+1}/{len(original_href_list)}] URL: {abs_url} (Original: {original_url})")
                                absolute_urls_processed[idx] = abs_url # 処理済みURLを保持
                            except Exception as url_e:
                                logger.error(f"  [{idx+1}] URL変換エラー ({original_url}): {url_e}")
                                url_list_for_file.append(f"Error converting URL: {original_url}")
                                pdf_texts_list_for_file.append(None)
                                scraped_texts_list_for_file.append(None)
                                continue

                            # --- pdf モードの場合 ---
                            if attribute_name.lower() == 'pdf':
                                if isinstance(abs_url, str) and abs_url.lower().endswith('.pdf'):
                                    pdf_text_content = "Error: PDF download or extraction failed." # デフォルトエラーメッセージ
                                    try:
                                        pdf_bytes = await utils.download_pdf_async(api_request_context, abs_url)
                                        if pdf_bytes:
                                            pdf_text_content = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                            logger.info(f"  [{idx+1}] PDF Text Extracted (Length: {len(pdf_text_content or '')})")
                                            logger.debug(f"    Text: {pdf_text_content[:200] if pdf_text_content else 'None'}...")
                                        else:
                                            pdf_text_content = "Error: PDF download failed or returned no data."
                                            logger.error(f"  [{idx+1}] PDF download failed.")
                                    except Exception as pdf_err:
                                        logger.error(f"  [{idx+1}] PDF processing error: {pdf_err}")
                                        pdf_text_content = f"Error: {pdf_err}"
                                    pdf_texts_list_for_file.append(pdf_text_content)
                                    # 他のリストはこのURLでは関係ないのでNoneを追加
                                    url_list_for_file.append(abs_url)
                                    scraped_texts_list_for_file.append(None)
                                else:
                                    logger.info(f"  [{idx+1}] Not a PDF link. Skipping PDF processing.")
                                    url_list_for_file.append(abs_url) # URLは記録
                                    pdf_texts_list_for_file.append(None) # PDFテキストはない
                                    scraped_texts_list_for_file.append(None)

                            # --- content モードの場合 ---
                            elif attribute_name.lower() == 'content':
                                if isinstance(abs_url, str) and not abs_url.lower().endswith('.pdf'):
                                    content_success, scraped_text_or_error = await get_page_inner_text(current_context, abs_url, action_wait_time)
                                    if content_success:
                                        logger.info(f"  [{idx+1}] Page Content Scraped (Length: {len(scraped_text_or_error or '')})")
                                        logger.debug(f"    Content: {scraped_text_or_error[:200] if scraped_text_or_error else ''}...")
                                        scraped_texts_list_for_file.append(scraped_text_or_error)
                                    else:
                                        logger.error(f"  [{idx+1}] Page Content Scraping Failed: {scraped_text_or_error}")
                                        scraped_texts_list_for_file.append(f"Error scraping content: {scraped_text_or_error}")
                                    # 他のリストはこのURLでは関係ないのでNoneを追加
                                    url_list_for_file.append(abs_url)
                                    pdf_texts_list_for_file.append(None)
                                else:
                                    logger.info(f"  [{idx+1}] Is PDF or invalid URL. Skipping content scraping.")
                                    url_list_for_file.append(abs_url) # URLは記録
                                    scraped_texts_list_for_file.append(None) # スクレイプテキストはない
                                    pdf_texts_list_for_file.append(None)

                            # --- href モードの場合 ---
                            elif attribute_name.lower() == 'href':
                                url_list_for_file.append(abs_url)
                                # 他のリストはこのモードでは関係ないのでNoneを追加
                                pdf_texts_list_for_file.append(None)
                                scraped_texts_list_for_file.append(None)

                            item_elapsed = (time.monotonic() - item_start_time) * 1000
                            logger.debug(f"  [{idx+1}] Item processing time: {item_elapsed:.0f}ms")

                        process_elapsed = (time.monotonic() - process_start_time) * 1000
                        logger.info(f"--- Finished processing {len(original_href_list)} URLs ({process_elapsed:.0f}ms) ---")

                        # 結果を action_result_details に格納
                        action_result_details["attribute"] = attribute_name # 元の要求属性名を記録
                        action_result_details["url_list"] = url_list_for_file # 常にURLリストを含める
                        if attribute_name.lower() == 'pdf':
                             action_result_details["pdf_texts"] = pdf_texts_list_for_file
                        elif attribute_name.lower() == 'content':
                             action_result_details["scraped_texts"] = scraped_texts_list_for_file
                        # hrefの場合は url_list のみが主要な結果

                    else: # href, pdf, content 以外の場合 (通常の属性取得)
                        logger.info(f"指定された属性 '{attribute_name}' を取得します...")
                        generic_attribute_list_for_file = [None] * len(found_elements_list)
                        # --- 属性値を取得する内部関数 ---
                        async def get_single_attr(locator: Locator, attr_name: str, index: int) -> Optional[str]:
                            try:
                                return await locator.get_attribute(attr_name, timeout=max(500, action_wait_time // 5))
                            except Exception as e:
                                logger.warning(f"  要素 {index+1} の属性 '{attr_name}' 取得中にエラー: {type(e).__name__}")
                                return None

                        # 逐次処理で属性を取得
                        for idx, (loc, _) in enumerate(found_elements_list):
                             attr_val = await get_single_attr(loc, attribute_name, idx)
                             logger.info(f"  [{idx+1}/{len(found_elements_list)}] Attribute '{attribute_name}': {attr_val}")
                             generic_attribute_list_for_file[idx] = attr_val

                        action_result_details.update({"attribute": attribute_name, "attribute_list": generic_attribute_list_for_file})
                        logger.info(f"取得した属性値リスト ({len(generic_attribute_list_for_file)}件)")
                        # pprint はログが冗長になるので logger.info で十分か

                # 最後に結果を追加
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_all_text_contents":
                 # ... (変更なし) ...
                if not selector: raise ValueError("Action 'get_all_text_contents' requires 'selector'.")
                text_list: List[Optional[str]] = []
                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、テキスト取得をスキップします。")
                else:
                    logger.info(f"動的探索で見つかった {len(found_elements_list)} 個の要素から textContent を取得します。")
                    get_text_tasks = []
                    # --- textContent を取得する内部関数 ---
                    async def get_single_text(locator: Locator, index: int) -> Optional[str]:
                        try:
                            text = await locator.text_content(timeout=max(500, action_wait_time // 5))
                            return text.strip() if text else "" # 前後空白除去
                        except Exception as e:
                            logger.warning(f"  要素 {index+1} の textContent 取得中にエラー: {type(e).__name__}")
                            return None

                    for loc_index, (loc, _) in enumerate(found_elements_list):
                         get_text_tasks.append(get_single_text(loc, loc_index))

                    text_list = await asyncio.gather(*get_text_tasks)
                    logger.info(f"取得したテキストリスト ({len(text_list)}件)")

                action_result_details["text_list"] = text_list
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "wait_visible":
                 # ... (変更なし) ...
                if not element: raise ValueError("Wait visible action requires an element.")
                logger.info("要素が表示されるのを待ちます...")
                await element.wait_for(state='visible', timeout=action_wait_time)
                logger.info("要素が表示されていることを確認しました。")
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "select_option":
                 # ... (変更なし) ...
                  if not element: raise ValueError("Select option action requires an element.")
                  if option_type not in ['value', 'index', 'label'] or option_value is None:
                       raise ValueError("Invalid 'option_type' or 'option_value' for select_option action.")
                  logger.info(f"ドロップダウンを選択します (Type: {option_type}, Value: '{option_value}')...")
                  if option_type == 'value':
                      await element.select_option(value=str(option_value), timeout=action_wait_time)
                  elif option_type == 'index':
                      try: index_val = int(option_value)
                      except (ValueError, TypeError): raise ValueError("Option type 'index' requires an integer value.")
                      await element.select_option(index=index_val, timeout=action_wait_time)
                  elif option_type == 'label':
                      await element.select_option(label=str(option_value), timeout=action_wait_time)
                  logger.info("ドロップダウンの選択が成功しました。")
                  action_result_details.update({"option_type": option_type, "option_value": option_value})
                  results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "scroll_to_element":
                 # ... (変更なし) ...
                   if not element: raise ValueError("Scroll action requires an element.")
                   logger.info("要素が表示されるまでスクロールします...")
                   await element.scroll_into_view_if_needed(timeout=action_wait_time)
                   await asyncio.sleep(0.3) # スクロール後の安定待ち
                   logger.info("要素へのスクロールが成功しました。")
                   results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "screenshot":
                 # ... (変更なし) ...
                  # ファイル名の決定 (valueがあればそれ、なければデフォルト名)
                  filename_base = str(value) if value else f"screenshot_step{step_num}"
                  # 拡張子がなければ .png を追加
                  filename = f"{filename_base}.png" if not filename_base.lower().endswith(('.png', '.jpg', '.jpeg')) else filename_base
                  screenshot_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, filename)
                  # ディレクトリが存在しない場合は作成
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  logger.info(f"スクリーンショットを '{screenshot_path}' に保存します...")
                  if element: # 要素が指定されていれば要素のスクリーンショット
                       await element.screenshot(path=screenshot_path, timeout=action_wait_time)
                       logger.info("要素のスクリーンショットを保存しました。")
                  else: # 要素が指定されていなければページ全体
                       await root_page.screenshot(path=screenshot_path, full_page=True)
                       logger.info("ページ全体のスクリーンショットを保存しました。")
                  action_result_details["filename"] = screenshot_path # 結果にファイルパスを含める
                  results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            else:
                 # ... (変更なし) ...
                  known_actions = ["click", "input", "hover", "get_inner_text", "get_text_content", "get_inner_html", "get_attribute", "get_all_attributes", "get_all_text_contents", "wait_visible", "select_option", "screenshot", "scroll_page_to_bottom", "scroll_to_element", "wait_page_load", "sleep", "switch_to_iframe", "switch_to_parent_frame"]
                  if action not in known_actions:
                     logger.warning(f"未定義または不明なアクション '{action}' です。このステップはスキップされます。")
                     results.append({"step": step_num, "status": "skipped", "action": action, "message": f"Undefined action: {action}"})

        # --- エラーハンドリング (変更なし) ---
        except (PlaywrightTimeoutError, PlaywrightError, ValueError, Exception) as e:
            error_message = f"ステップ {step_num} ({action}) の実行中にエラーが発生しました: {type(e).__name__} - {e}"
            logger.error(error_message, exc_info=True) # スタックトレース付きでログ出力
            error_screenshot_path = None
            # エラー発生時のスクリーンショットを試みる
            if root_page and not root_page.is_closed():
                 timestamp = time.strftime("%Y%m%d_%H%M%S")
                 error_ss_filename = f"error_step{step_num}_{timestamp}.png"
                 error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, error_ss_filename)
                 try:
                     os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                     await root_page.screenshot(path=error_ss_path, full_page=True)
                     logger.info(f"エラー発生時のスクリーンショットを保存しました: {error_ss_path}")
                     error_screenshot_path = error_ss_path
                 except Exception as ss_e:
                     logger.error(f"エラー発生時のスクリーンショット保存に失敗しました: {ss_e}")
            elif root_page and root_page.is_closed():
                 # ページが閉じられている場合、エラーメッセージに追記
                 error_message += " (Root page was closed during execution)"
                 logger.warning("根本原因: ルートページが閉じられた可能性があります。")

            # エラー情報を結果リストに追加
            error_details = {
                "step": step_num,
                "status": "error",
                "action": action,
                "selector": selector,
                "message": str(e), # エラーメッセージ本文
                "full_error": error_message # より詳細なエラー情報
            }
            if error_screenshot_path:
                error_details["error_screenshot"] = error_screenshot_path
            results.append(error_details)
            return False, results # Falseを返して処理中断

    # 全てのステップが正常に完了した場合
    return True, results


# --- Playwright 実行メイン関数 (修正: クリーンアップ処理) ---
async def run_playwright_automation_async(
        target_url: str,
        actions: List[dict],
        headless_mode: bool = False,
        slow_motion: int = 100,
        default_timeout: int = config.DEFAULT_ACTION_TIMEOUT
    ) -> Tuple[bool, List[dict]]:
    """Playwright を非同期で初期化、アクション実行、終了処理を行う。"""
    logger.info("--- Playwright 自動化開始 (非同期) ---")
    all_success = False
    final_results: List[dict] = []
    playwright = None; browser = None; context = None; page = None
    try:
        playwright = await async_playwright().start()
        logger.info(f"ブラウザ起動 (Chromium, Headless: {headless_mode}, SlowMo: {slow_motion}ms)...")
        browser = await playwright.chromium.launch(headless=headless_mode, slow_mo=slow_motion)
        logger.info("新しいブラウザコンテキストを作成します...")
        context = await browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36', # 一般的なUA
            viewport={'width': 1920, 'height': 1080}, # 一般的な解像度
            locale='ja-JP', # 日本語ロケール
            timezone_id='Asia/Tokyo', # 日本時間
            # accept_downloads=True, # PDFダウンロード等に必要なら有効化
            extra_http_headers={'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'} # Accept-Languageヘッダ
        )
        # デフォルトタイムアウト設定
        effective_default_timeout = default_timeout if default_timeout else config.DEFAULT_ACTION_TIMEOUT
        context.set_default_timeout(effective_default_timeout)
        logger.info(f"コンテキストのデフォルトタイムアウトを {effective_default_timeout}ms に設定しました。")
        # APIリクエスト用のコンテキスト (PDFダウンロード等で使用)
        api_request_context = context.request

        # --- Stealth モード (必要であればコメント解除) ---
        # logger.info("Applying stealth mode to the context...")
        # try:
        #     await stealth_async(context)
        #     logger.info("Stealth mode applied successfully.")
        # except Exception as stealth_err:
        #      logger.warning(f"Failed to apply stealth mode: {stealth_err}")

        logger.info("新しいページを作成します...")
        page = await context.new_page()

        # 最初のページへのナビゲーション (タイムアウトを長めに設定)
        initial_nav_timeout = max(effective_default_timeout * 3, 30000) # デフォルトの3倍か30秒の長い方
        logger.info(f"最初のナビゲーション: {target_url} (タイムアウト: {initial_nav_timeout}ms)...")
        await page.goto(target_url, wait_until="load", timeout=initial_nav_timeout)
        logger.info("最初のナビゲーション成功。アクションの実行を開始します...")

        # アクション実行
        all_success, final_results = await execute_actions_async(page, actions, api_request_context, effective_default_timeout)

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
                  await page.screenshot(path=overall_error_ss_path, full_page=True)
                  logger.info(f"全体エラー発生時のスクリーンショットを保存しました: {overall_error_ss_path}")
                  overall_error_screenshot_path = overall_error_ss_path
              except Exception as ss_e:
                  logger.error(f"全体エラー発生時のスクリーンショット保存に失敗しました: {ss_e}")
         # 最終結果リストに全体エラー情報を追加 (既に追加されていなければ)
         if not final_results or final_results[-1].get("status") != "error":
             error_details = {"step": "Overall", "status": "error", "message": str(e), "full_error": error_msg_overall}
             if overall_error_screenshot_path:
                 error_details["error_screenshot"] = overall_error_screenshot_path
             final_results.append(error_details)
         all_success = False # 全体エラーなので失敗扱い

    # --- クリーンアップ処理 ---
    finally:
        logger.info("クリーンアップ処理を開始します...")
        # --- ▼▼▼ 修正箇所 ▼▼▼ ---
        if context: # is_closed() チェックを削除
            try:
                await context.close()
                logger.info("ブラウザコンテキストを閉じました。")
            except Exception as context_close_e:
                # 既に閉じられている場合などのエラーは警告レベルに留める
                logger.warning(f"ブラウザコンテキストのクローズ中にエラーが発生しました (無視): {context_close_e}")
        # --- ▲▲▲ 修正箇所 ▲▲▲ ---
        else:
             logger.debug("ブラウザコンテキストは存在しません (既に閉じられたか、作成されませんでした)。")

        if browser and browser.is_connected():
            try: await browser.close(); logger.info("ブラウザを閉じました。")
            except Exception as browser_close_e: logger.error(f"ブラウザのクローズ中にエラーが発生しました: {browser_close_e}")
        else: logger.debug("ブラウザは接続されていないか、存在しません。")

        if playwright:
            try: await playwright.stop(); logger.info("Playwright を停止しました。")
            except Exception as playwright_stop_e: logger.error(f"Playwright の停止中にエラーが発生しました: {playwright_stop_e}")

        # イベントループのクリーンアップを促すための短い待機
        try: await asyncio.sleep(0.1)
        except Exception as sleep_e: logger.warning(f"クリーンアップ後の待機中にエラーが発生しました: {sleep_e}")

    logger.info("--- Playwright 自動化終了 (非同期) ---")
    return all_success, final_results