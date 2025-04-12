# --- ファイル: playwright_handler.py ---
"""
Playwrightを使ったブラウザ操作とアクション実行のコアロジック。
"""
import asyncio
import logging # logging をインポート
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
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    APIRequestContext
)
from typing import List, Tuple, Optional, Union, Any, Deque, Dict
from urllib.parse import urljoin

# --- 依存モジュールをインポート ---
import config # 設定値
import utils  # PDF処理などのヘルパー関数

# --- ロガー取得 ---
logger = logging.getLogger(__name__)

# ▼▼▼ 特定の ResourceWarning を無視する設定 ▼▼▼
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")


# --- 動的要素探索ヘルパー関数 (単一要素用) ---
async def find_element_dynamically(
    base_locator: Union[Page, FrameLocator],
    target_selector: str,
    max_depth: int = config.DYNAMIC_SEARCH_MAX_DEPTH,
    timeout: int = config.DEFAULT_ACTION_TIMEOUT,
    target_state: str = "attached"
) -> Tuple[Optional[Locator], Optional[Union[Page, FrameLocator]]]:
    """単一の要素を動的に探索する。"""
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

# --- 動的要素探索ヘルパー関数 (複数要素用) ---
async def find_all_elements_dynamically(
    base_locator: Union[Page, FrameLocator],
    target_selector: str,
    max_depth: int = config.DYNAMIC_SEARCH_MAX_DEPTH,
    timeout: int = config.DEFAULT_ACTION_TIMEOUT,
) -> List[Tuple[Locator, Union[Page, FrameLocator]]]:
    """複数の要素を動的に探索する。"""
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

# --- Playwright アクション実行コア ---
async def execute_actions_async(initial_page: Page, actions: List[dict], api_request_context: APIRequestContext, default_timeout: int) -> Tuple[bool, List[dict]]:
    """Playwright アクションを非同期で実行する。iframe探索を動的に行う。"""
    results: List[dict] = []
    current_target: Union[Page, FrameLocator] = initial_page
    root_page: Page = initial_page
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
        action_wait_time = step_data.get("wait_time_ms", default_timeout)

        logger.info(f"--- ステップ {step_num}/{len(actions)}: Action='{action}' ---") # logger を使用
        step_info = {"selector": selector, "value": value, "iframe(指定)": iframe_selector_input,
                     "option_type": option_type, "option_value": option_value, "attribute_name": attribute_name}
        step_info_str = ", ".join([f"{k}='{v}'" for k, v in step_info.items() if v is not None])
        logger.info(f"詳細: {step_info_str} (timeout: {action_wait_time}ms)") # logger を使用

        try:
            if root_page.is_closed(): raise PlaywrightError("Root page is closed.")
            current_base_url = root_page.url
            root_page_title = await root_page.title()
            current_target_type = type(current_target).__name__
            logger.info(f"現在のルートページ: URL='{current_base_url}', Title='{root_page_title}'") # logger を使用
            logger.info(f"現在の探索スコープ: {current_target_type}") # logger を使用
        except Exception as e:
            logger.error(f"現在のターゲット情報取得中にエラー: {e}", exc_info=True) # logger を使用
            results.append({"step": step_num, "status": "error", "action": action, "message": f"Failed to get target info: {e}"})
            return False, results

        try:
            # --- Iframe/Parent Frame 切替 (明示指定) ---
            if action == "switch_to_iframe":
                if not iframe_selector_input: raise ValueError("Action 'switch_to_iframe' requires 'iframe_selector'")
                logger.info(f"[ユーザー指定] Iframe '{iframe_selector_input}' に切り替えます...") # logger を使用
                try:
                    target_frame_locator = current_target.frame_locator(iframe_selector_input)
                    await target_frame_locator.locator(':root').wait_for(state='attached', timeout=action_wait_time)
                except PlaywrightTimeoutError: raise PlaywrightTimeoutError(f"指定 iframe '{iframe_selector_input}' が見つからないかタイムアウト ({action_wait_time}ms)。")
                except Exception as e: raise PlaywrightError(f"Iframe '{iframe_selector_input}' 切替エラー: {e}")
                if id(current_target) not in [id(s) for s in iframe_stack]: iframe_stack.append(current_target)
                current_target = target_frame_locator
                logger.info("FrameLocator への切り替え成功。") # logger を使用
                results.append({"step": step_num, "status": "success", "action": action, "selector": iframe_selector_input})
                continue
            elif action == "switch_to_parent_frame":
                if not iframe_stack:
                    logger.warning("既にトップレベルかスタックが空です。") # logger を使用
                    if isinstance(current_target, FrameLocator):
                        logger.info("ターゲットをルートページに戻します。") # logger を使用
                        current_target = root_page
                    results.append({"step": step_num, "status": "warning", "action": action, "message": "Already at top-level or stack empty."})
                else:
                    logger.info("[ユーザー指定] 親ターゲットに戻ります...") # logger を使用
                    current_target = iframe_stack.pop()
                    target_type = type(current_target).__name__
                    logger.info(f"親ターゲットへの切り替え成功。現在の探索スコープ: {target_type}") # logger を使用
                    results.append({"step": step_num, "status": "success", "action": action})
                continue

            # --- ページ全体操作 ---
            if action in ["wait_page_load", "sleep", "scroll_page_to_bottom"]:
                if action == "wait_page_load":
                    logger.info("ページの読み込み完了 (load) を待ちます...") # logger を使用
                    await root_page.wait_for_load_state("load", timeout=action_wait_time)
                    logger.info("読み込み完了。") # logger を使用
                    results.append({"step": step_num, "status": "success", "action": action})
                elif action == "sleep":
                    seconds = float(value) if value is not None else 1.0
                    logger.info(f"{seconds:.1f} 秒待機します...") # logger を使用
                    await asyncio.sleep(seconds)
                    results.append({"step": step_num, "status": "success", "action": action, "duration_sec": seconds})
                elif action == "scroll_page_to_bottom":
                    logger.info("ページ最下部へスクロールします...") # logger を使用
                    await root_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(0.5)
                    logger.info("スクロール完了。") # logger を使用
                    results.append({"step": step_num, "status": "success", "action": action})
                continue

            # --- 要素操作のための準備 ---
            element: Optional[Locator] = None
            found_elements_list: List[Tuple[Locator, Union[Page, FrameLocator]]] = []

            single_element_required_actions = [
                "click", "input", "hover", "get_inner_text", "get_text_content", "get_inner_html",
                "get_attribute", "wait_visible", "select_option", "scroll_to_element"
            ]
            multiple_elements_actions = ["get_all_attributes", "get_all_text_contents"]
            is_single_element_required = action in single_element_required_actions
            is_multiple_elements_action = action in multiple_elements_actions
            is_screenshot_element = action == "screenshot" and selector is not None

            if is_single_element_required or is_multiple_elements_action or is_screenshot_element:
                 if not selector: raise ValueError(f"Action '{action}' requires a 'selector'.")

            # --- 単一要素探索 ---
            if is_single_element_required or is_screenshot_element:
                required_state = 'visible' if action in ['click', 'hover', 'screenshot', 'select_option', 'input', 'get_inner_text', 'wait_visible'] else 'attached'
                element, found_scope = await find_element_dynamically(
                    base_locator=current_target, target_selector=selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH,
                    timeout=action_wait_time, target_state=required_state
                )
                if element and found_scope:
                    found_scope_type = type(found_scope).__name__
                    if id(found_scope) != id(current_target):
                        logger.info(f"探索スコープを要素が見つかった '{found_scope_type}' に更新。") # logger を使用
                        if id(current_target) not in [id(s) for s in iframe_stack]: iframe_stack.append(current_target)
                        current_target = found_scope
                else:
                    error_msg = f"要素 '{selector}' (状態: {required_state}) が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。"
                    logger.error(error_msg) # logger を使用
                    results.append({"step": step_num, "status": "error", "action": action, "selector": selector, "message": error_msg})
                    return False, results
                if not isinstance(element, Locator): raise PlaywrightError(f"要素 '{selector}' の Locator 取得失敗 (発見後)。")
                logger.info(f"最終的な単一操作対象スコープ: {type(current_target).__name__}") # logger を使用

            # --- 複数要素探索 ---
            elif is_multiple_elements_action:
                found_elements_list = await find_all_elements_dynamically(
                    base_locator=current_target, target_selector=selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH,
                    timeout=action_wait_time
                )
                if not found_elements_list:
                    logger.warning(f"要素 '{selector}' が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。") # logger を使用

            # --- 各アクション実行 ---
            action_result_details = {"selector": selector}

            if action == "click":
                if not element: raise ValueError("Click action requires an element.")
                logger.info("要素をクリックします...") # logger を使用
                context = root_page.context
                try:
                    async with context.expect_page(timeout=config.NEW_PAGE_EVENT_TIMEOUT) as new_page_info:
                        await element.click(timeout=action_wait_time)
                    new_page = await new_page_info.value
                    new_page_url = new_page.url
                    logger.info(f"新しいページが開きました: URL={new_page_url}") # logger を使用
                    try: await new_page.wait_for_load_state("load", timeout=action_wait_time)
                    except PlaywrightTimeoutError: logger.warning(f"新しいページのロード待機タイムアウト ({action_wait_time}ms)。") # logger を使用
                    root_page = new_page; current_target = new_page; iframe_stack.clear()
                    logger.info("スコープを新しいページにリセットしました。") # logger を使用
                    action_result_details.update({"new_page_opened": True, "new_page_url": new_page_url})
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})
                except PlaywrightTimeoutError:
                    logger.info(f"クリック完了 (新しいページは {config.NEW_PAGE_EVENT_TIMEOUT}ms 以内に開きませんでした)。") # logger を使用
                    action_result_details["new_page_opened"] = False
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "input":
                 if not element: raise ValueError("Input action requires an element.")
                 if value is None: raise ValueError("Input action requires 'value'.")
                 logger.info(f"要素に '{value}' を入力...") # logger を使用
                 await element.fill(str(value), timeout=action_wait_time)
                 logger.info("入力成功。") # logger を使用
                 action_result_details["value"] = value
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "hover":
                 if not element: raise ValueError("Hover action requires an element.")
                 logger.info("要素にマウスオーバー...") # logger を使用
                 await element.hover(timeout=action_wait_time)
                 logger.info("ホバー成功。") # logger を使用
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_inner_text":
                 if not element: raise ValueError("Get text action requires an element.")
                 logger.info("要素の innerText を取得...") # logger を使用
                 text = await element.inner_text(timeout=action_wait_time)
                 logger.info(f"取得テキスト(innerText): '{text}'") # logger を使用
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_text_content":
                 if not element: raise ValueError("Get text action requires an element.")
                 logger.info("要素の textContent を取得...") # logger を使用
                 text = await element.text_content(timeout=action_wait_time)
                 logger.info(f"取得テキスト(textContent): '{text}'") # logger を使用
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_inner_html":
                 if not element: raise ValueError("Get HTML action requires an element.")
                 logger.info("要素の innerHTML を取得...") # logger を使用
                 html_content = await element.inner_html(timeout=action_wait_time)
                 logger.info(f"取得HTML(innerHTML):\n{html_content[:500]}...") # logger を使用
                 action_result_details["html"] = html_content
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_attribute":
                if not element: raise ValueError("Get attribute action requires an element.")
                if not attribute_name: raise ValueError("Action 'get_attribute' requires 'attribute_name'.")
                logger.info(f"要素の属性 '{attribute_name}' を取得...") # logger を使用
                attr_value = await element.get_attribute(attribute_name, timeout=action_wait_time)
                pdf_text_content = None
                if attribute_name.lower() == 'href' and attr_value is not None:
                    original_url = attr_value
                    try:
                        absolute_url = urljoin(current_base_url, attr_value)
                        if original_url != absolute_url: logger.info(f"  -> 絶対URLに変換: '{absolute_url}'") # logger を使用
                        attr_value = absolute_url
                        if isinstance(absolute_url, str) and absolute_url.lower().endswith('.pdf'):
                            logger.info(f"  リンク先がPDF。ダウンロードとテキスト抽出を試みます...") # logger を使用
                            pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                            if pdf_bytes:
                                pdf_text_content = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                logger.info(f"  PDFテキスト抽出完了 (先頭抜粋): {pdf_text_content[:200] if pdf_text_content else 'None'}...") # logger を使用
                            else:
                                pdf_text_content = "Error: PDF download failed or returned no data."
                                logger.error(f"  PDFダウンロード失敗: {absolute_url}") # logger を使用
                    except Exception as url_e:
                        logger.error(f"URL処理中にエラー (URL: '{attr_value}'): {url_e}") # logger を使用
                logger.info(f"取得属性値 ({attribute_name}): '{attr_value}'") # logger を使用
                action_result_details.update({"attribute": attribute_name, "value": attr_value})
                if pdf_text_content is not None: action_result_details["pdf_text"] = pdf_text_content
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_all_attributes":
                if not selector: raise ValueError("Action 'get_all_attributes' requires 'selector'.")
                if not attribute_name: raise ValueError("Action 'get_all_attributes' requires 'attribute_name'.")

                original_attribute_list: List[Optional[str]] = []
                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、属性取得をスキップします。") # logger を使用
                else:
                    logger.info(f"動的探索で見つかった {len(found_elements_list)} 個の要素から属性 '{attribute_name}' を取得します。") # logger を使用
                    get_attr_tasks = []
                    for loc_index, (loc, _) in enumerate(found_elements_list):
                         async def get_single_attr(locator: Locator, attr_name: str, index: int) -> Optional[str]:
                             try:
                                 return await locator.get_attribute(attr_name, timeout=action_wait_time // 2 if action_wait_time > 1000 else 500)
                             except PlaywrightTimeoutError:
                                 logger.warning(f"要素 {index+1} の属性 '{attr_name}' 取得中にタイムアウト。") # logger を使用
                                 return None
                             except Exception as e:
                                 logger.warning(f"要素 {index+1} の属性 '{attr_name}' 取得中にエラー: {type(e).__name__} - {e}") # logger を使用
                                 return None
                         get_attr_tasks.append(get_single_attr(loc, attribute_name, loc_index))
                    original_attribute_list = await asyncio.gather(*get_attr_tasks)

                final_list_to_store: List[Optional[str]] = []
                pdf_texts_list: List[Optional[str]] = []
                result_key_name = "attribute_list"

                if attribute_name.lower() == 'href':
                    result_key_name = "url_lists"
                    logger.info("取得した href を絶対URLに変換し、PDFを処理します...") # logger を使用
                    absolute_url_list_temp = []
                    for original_url in original_attribute_list:
                        if original_url is not None:
                            try:
                                abs_url = urljoin(current_base_url, original_url)
                                absolute_url_list_temp.append(abs_url)
                                if original_url != abs_url: logger.info(f"  '{original_url}' -> '{abs_url}'") # logger を使用
                                else: logger.info(f"  '{abs_url}' (変更なし)") # logger を使用
                            except Exception as url_e:
                                logger.error(f"URL変換中にエラー (URL: '{original_url}'): {url_e}") # logger を使用
                                absolute_url_list_temp.append(original_url)
                        else:
                            absolute_url_list_temp.append(None)
                            logger.info(f"  (URLがNoneのためスキップ)") # logger を使用
                    final_list_to_store = absolute_url_list_temp

                    pdf_download_tasks = []
                    for abs_url in final_list_to_store:
                        if abs_url and isinstance(abs_url, str) and abs_url.lower().endswith('.pdf'):
                            pdf_download_tasks.append(utils.download_pdf_async(api_request_context, abs_url))
                        else:
                            pdf_download_tasks.append(asyncio.sleep(0, result=None))

                    if pdf_download_tasks:
                        logger.info(f"{len(pdf_download_tasks)} 個のURLについてPDFダウンロード/処理を開始...") # logger を使用
                        pdf_byte_results = await asyncio.gather(*pdf_download_tasks, return_exceptions=True)
                        logger.info("PDFダウンロード/処理完了。") # logger を使用
                        pdf_extract_tasks = []
                        for idx, result in enumerate(pdf_byte_results):
                            if isinstance(result, bytes):
                                pdf_extract_tasks.append(asyncio.to_thread(utils.extract_text_from_pdf_sync, result))
                            else:
                                if isinstance(result, Exception): logger.error(f"URL {final_list_to_store[idx]} のPDFダウンロード中にエラー(gather結果): {result}") # logger を使用
                                pdf_extract_tasks.append(asyncio.sleep(0, result=None))
                        if pdf_extract_tasks:
                            logger.info(f"{len(pdf_extract_tasks)} 件のPDFテキスト抽出/処理を開始...") # logger を使用
                            pdf_texts_results = await asyncio.gather(*pdf_extract_tasks, return_exceptions=True)
                            logger.info("PDFテキスト抽出/処理完了。") # logger を使用
                            pdf_texts_list = []
                            for idx, text_result in enumerate(pdf_texts_results):
                                if isinstance(text_result, str): pdf_texts_list.append(text_result)
                                else:
                                    if isinstance(text_result, Exception): logger.error(f"URL {final_list_to_store[idx]} のPDFテキスト抽出中にエラー(gather結果): {text_result}") # logger を使用
                                    pdf_texts_list.append(None)
                    logger.info(f"絶対URLリスト ({len(final_list_to_store)}件):") # logger を使用
                else:
                    final_list_to_store = original_attribute_list
                    pdf_texts_list = [None] * len(final_list_to_store)
                    logger.info(f"取得した属性値リスト ({len(final_list_to_store)}件):") # logger を使用

                #pprint.pprint(final_list_to_store)
                action_result_details.update({"attribute": attribute_name, result_key_name: final_list_to_store})
                if not all(v is None for v in pdf_texts_list): action_result_details["pdf_texts"] = pdf_texts_list
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_all_text_contents":
                if not selector: raise ValueError("Action 'get_all_text_contents' requires 'selector'.")

                text_list: List[Optional[str]] = []
                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、テキスト取得をスキップします。") # logger を使用
                else:
                    logger.info(f"動的探索で見つかった {len(found_elements_list)} 個の要素から textContent を取得します。") # logger を使用
                    get_text_tasks = []
                    for loc_index, (loc, _) in enumerate(found_elements_list):
                         async def get_single_text(locator: Locator, index: int) -> Optional[str]:
                             try:
                                 text = await locator.text_content(timeout=action_wait_time // 2 if action_wait_time > 1000 else 500)
                                 return text
                             except PlaywrightTimeoutError:
                                 logger.warning(f"要素 {index+1} の textContent 取得中にタイムアウト。") # logger を使用
                                 return None
                             except Exception as e:
                                 logger.warning(f"要素 {index+1} の textContent 取得中にエラー: {type(e).__name__} - {e}") # logger を使用
                                 return None
                         get_text_tasks.append(get_single_text(loc, loc_index))
                    text_list = await asyncio.gather(*get_text_tasks)

                action_result_details["text_list"] = text_list
                logger.info(f"取得したテキストリスト ({len(text_list)}件):") # logger を使用
                #pprint.pprint(text_list)
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "wait_visible":
                 if not element: raise ValueError("Wait visible action requires an element.")
                 logger.info("要素が表示されるのを待ちます...") # logger を使用
                 await element.wait_for(state='visible', timeout=action_wait_time)
                 logger.info("要素表示確認。") # logger を使用
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "select_option":
                  if not element: raise ValueError("Select option action requires an element.")
                  if option_type not in ['value', 'index', 'label'] or option_value is None:
                      raise ValueError("Invalid 'option_type' or 'option_value'.")
                  logger.info(f"ドロップダウン選択 (Type: {option_type}, Value: '{option_value}')...") # logger を使用
                  if option_type == 'value': await element.select_option(value=str(option_value), timeout=action_wait_time)
                  elif option_type == 'index': await element.select_option(index=int(option_value), timeout=action_wait_time)
                  elif option_type == 'label': await element.select_option(label=str(option_value), timeout=action_wait_time)
                  logger.info("選択成功。") # logger を使用
                  action_result_details.update({"option_type": option_type, "option_value": option_value})
                  results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "scroll_to_element":
                   if not element: raise ValueError("Scroll action requires an element.")
                   logger.info("要素までスクロール...") # logger を使用
                   await element.scroll_into_view_if_needed(timeout=action_wait_time)
                   logger.info("スクロール成功。") # logger を使用
                   results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "screenshot":
                  filename = str(value) if value else f"screenshot_step{step_num}.png"
                  screenshot_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, filename)
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  logger.info(f"スクリーンショット保存: '{screenshot_path}'...") # logger を使用
                  if element:
                       await element.screenshot(path=screenshot_path, timeout=action_wait_time)
                       logger.info("要素のスクショ保存成功。") # logger を使用
                       action_result_details["filename"] = screenshot_path
                       results.append({"step": step_num, "status": "success", "action": action, **action_result_details})
                  else:
                       await root_page.screenshot(path=screenshot_path, full_page=True)
                       logger.info("ページ全体のスクショ保存成功。") # logger を使用
                       results.append({"step": step_num, "status": "success", "action": action, "filename": screenshot_path})

            else:
                  known_actions = [
                      "click", "input", "hover", "get_inner_text", "get_text_content",
                      "get_inner_html", "get_attribute", "get_all_attributes",
                      "get_all_text_contents",
                      "wait_visible", "select_option", "screenshot",
                      "scroll_page_to_bottom", "scroll_to_element", "wait_page_load",
                      "sleep", "switch_to_iframe", "switch_to_parent_frame"
                  ]
                  if action not in known_actions:
                     logger.warning(f"未定義のアクション '{action}'。スキップします。") # logger を使用
                     results.append({"step": step_num, "status": "skipped", "action": action, "message": "Undefined action"})

        except (PlaywrightTimeoutError, PlaywrightError, ValueError, Exception) as e:
            error_message = f"ステップ {step_num} ({action}) エラー: {type(e).__name__} - {e}"
            logger.error(error_message, exc_info=True) # logger を使用
            error_screenshot_path = None
            if root_page and not root_page.is_closed():
                 timestamp = time.strftime("%Y%m%d_%H%M%S")
                 error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, f"error_step{step_num}_{timestamp}.png")
                 try:
                     os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                     await root_page.screenshot(path=error_ss_path, full_page=True)
                     logger.info(f"エラー発生時のスクリーンショットを保存: {error_ss_path}") # logger を使用
                     error_screenshot_path = error_ss_path
                 except Exception as ss_e: logger.error(f"エラー時のスクリーンショット保存に失敗: {ss_e}") # logger を使用
            elif root_page and root_page.is_closed():
                 error_message += " (Root page was closed)"
                 logger.warning("根本原因: ルートページが閉じられた可能性あり。") # logger を使用
            error_details = {"step": step_num, "status": "error", "action": action, "selector": selector, "message": str(e), "full_error": error_message}
            if error_screenshot_path: error_details["error_screenshot"] = error_screenshot_path
            results.append(error_details)
            return False, results

    return True, results

# --- Playwright 実行メイン関数 ---
async def run_playwright_automation_async(
        target_url: str,
        actions: List[dict],
        headless_mode: bool = False,
        slow_motion: int = 100,
        default_timeout: int = config.DEFAULT_ACTION_TIMEOUT
    ) -> Tuple[bool, List[dict]]:
    """Playwright を非同期で初期化、アクション実行、終了処理を行う。"""
    logger.info("--- Playwright 自動化開始 (非同期) ---") # logger を使用
    all_success = False
    final_results: List[dict] = []
    playwright = None; browser = None; context = None; page = None
    try:
        playwright = await async_playwright().start()
        logger.info(f"ブラウザ起動 (Chromium, Headless: {headless_mode}, SlowMo: {slow_motion}ms)...") # logger を使用
        browser = await playwright.chromium.launch(headless=headless_mode, slow_mo=slow_motion)
        context = await browser.new_context(
             user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        )
        context.set_default_timeout(default_timeout)
        api_request_context = context.request
        page = await context.new_page()
        logger.info(f"ナビゲーション: {target_url} ...") # logger を使用
        await page.goto(target_url, wait_until="load", timeout=default_timeout * 3)
        logger.info("ナビゲーション成功。") # logger を使用
        all_success, final_results = await execute_actions_async(page, actions, api_request_context, default_timeout)
        if all_success: logger.info("すべてのステップが正常に完了しました。") # logger を使用
        else: logger.error("途中でエラーが発生しました。") # logger を使用
    except (PlaywrightTimeoutError, PlaywrightError, Exception) as e:
         error_msg_overall = f"Playwright 処理全体でエラー: {type(e).__name__} - {e}"
         logger.error(error_msg_overall, exc_info=True) # logger を使用
         overall_error_screenshot_path = None
         if page and not page.is_closed():
              timestamp = time.strftime("%Y%m%d_%H%M%S")
              overall_error_ss_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, f"error_overall_{timestamp}.png")
              try:
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  await page.screenshot(path=overall_error_ss_path, full_page=True)
                  logger.info(f"全体エラー発生時のスクリーンショットを保存: {overall_error_ss_path}") # logger を使用
                  overall_error_screenshot_path = overall_error_ss_path
              except Exception as ss_e: logger.error(f"全体エラー時のスクリーンショット保存に失敗: {ss_e}") # logger を使用
         if not final_results or final_results[-1].get("status") != "error":
             error_details = {"step": "Overall", "status": "error", "message": str(e), "full_error": error_msg_overall}
             if overall_error_screenshot_path: error_details["error_screenshot"] = overall_error_screenshot_path
             final_results.append(error_details)
         all_success = False
    finally:
        logger.info("クリーンアップ処理を開始します...") # logger を使用
        if context:
            try: await context.close(); logger.info("ブラウザコンテキストを閉じました。") # logger を使用
            except Exception as context_close_e: logger.error(f"ブラウザコンテキストのクローズ中にエラー: {context_close_e}") # logger を使用
        if browser:
            try: await browser.close(); logger.info("ブラウザを閉じました。") # logger を使用
            except Exception as browser_close_e: logger.error(f"ブラウザのクローズ中にエラー: {browser_close_e}") # logger を使用
        if playwright:
            try: await playwright.stop(); logger.info("Playwright を停止しました。") # logger を使用
            except Exception as playwright_stop_e: logger.error(f"Playwright の停止中にエラー: {playwright_stop_e}") # logger を使用
        try: await asyncio.sleep(0.1)
        except Exception as sleep_e: logger.warning(f"クリーンアップ後の待機中にエラー: {sleep_e}") # logger を使用
    logger.info("--- Playwright 自動化終了 (非同期) ---") # logger を使用
    return all_success, final_results