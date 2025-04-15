# --- ファイル: playwright_finders.py ---
"""
Playwrightを使った動的な要素探索機能を提供します。
"""
import asyncio
import logging
import time
from collections import deque
from playwright.async_api import (
    Page,
    FrameLocator,
    Locator,
    TimeoutError as PlaywrightTimeoutError,
)
from typing import List, Tuple, Optional, Union, Deque

import config

logger = logging.getLogger(__name__)

# --- 動的要素探索ヘルパー関数 (単一要素用) ---
async def find_element_dynamically(
    base_locator: Union[Page, FrameLocator],
    target_selector: str,
    max_depth: int = config.DYNAMIC_SEARCH_MAX_DEPTH,
    timeout: int = config.DEFAULT_ACTION_TIMEOUT,
    target_state: str = "attached"
) -> Tuple[Optional[Locator], Optional[Union[Page, FrameLocator]]]:
    """
    指定された起点からiframe内を含めて動的に単一の要素を探索します。
    見つかった要素のLocatorと、それが見つかったスコープ (Page or FrameLocator) を返します。
    タイムアウトするか見つからない場合は (None, None) を返します。
    """
    logger.info(f"動的探索(単一)開始: 起点={type(base_locator).__name__}, セレクター='{target_selector}', 最大深度={max_depth}, 状態='{target_state}', 全体タイムアウト={timeout}ms")
    start_time = time.monotonic()
    queue: Deque[Tuple[Union[Page, FrameLocator], int]] = deque([(base_locator, 0)])
    visited_scope_ids = {id(base_locator)}
    element_wait_timeout = 2000  # 要素存在確認のタイムアウト（短め）
    iframe_check_timeout = config.IFRAME_LOCATOR_TIMEOUT # iframe有効性確認のタイムアウト
    logger.debug(f"  要素待機タイムアウト: {element_wait_timeout}ms, フレーム確認タイムアウト: {iframe_check_timeout}ms")

    while queue:
        current_monotonic_time = time.monotonic()
        elapsed_time_ms = (current_monotonic_time - start_time) * 1000
        if elapsed_time_ms >= timeout:
            logger.warning(f"動的探索(単一)タイムアウト ({timeout}ms) - 経過時間: {elapsed_time_ms:.0f}ms")
            return None, None
        remaining_time_ms = timeout - elapsed_time_ms
        if remaining_time_ms < 100: # 残り時間が少なすぎる場合は探索打ち切り
             logger.warning(f"動的探索(単一)の残り時間がわずかなため ({remaining_time_ms:.0f}ms)、探索を打ち切ります。")
             return None, None

        current_scope, current_depth = queue.popleft()
        scope_type_name = type(current_scope).__name__
        scope_identifier = f" ({repr(current_scope)})" if isinstance(current_scope, FrameLocator) else ""
        logger.debug(f"  探索中(単一): スコープ={scope_type_name}{scope_identifier}, 深度={current_depth}, 残り時間: {remaining_time_ms:.0f}ms")

        step_start_time = time.monotonic()
        try:
            element = current_scope.locator(target_selector).first
            # 要素の待機タイムアウトは、全体タイムアウトの残り時間と設定値の小さい方、かつ最低50msを確保
            effective_element_timeout = max(50, min(element_wait_timeout, int(remaining_time_ms - 50))) # 50msのマージン
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

        # --- iframe探索 ---
        if current_depth < max_depth:
            # 再度時間チェック
            current_monotonic_time = time.monotonic()
            elapsed_time_ms = (current_monotonic_time - start_time) * 1000
            if elapsed_time_ms >= timeout: continue # 時間切れなら次のキューへ
            remaining_time_ms = timeout - elapsed_time_ms
            if remaining_time_ms < 100: continue # 残り時間が少なすぎる場合

            step_start_time = time.monotonic()
            logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) 内の可視iframeを探索...")
            try:
                iframe_base_selector = 'iframe:visible' # 可視iframeのみを対象
                visible_iframe_locators = current_scope.locator(iframe_base_selector)
                count = await visible_iframe_locators.count()
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                if count > 0: logger.debug(f"      発見した可視iframe候補数: {count} ({step_elapsed:.0f}ms)")

                for i in range(count):
                    # ループ内でも時間チェック
                    current_monotonic_time_inner = time.monotonic()
                    elapsed_time_ms_inner = (current_monotonic_time_inner - start_time) * 1000
                    if elapsed_time_ms_inner >= timeout:
                         logger.warning(f"動的探索(単一)タイムアウト ({timeout}ms) - iframeループ中")
                         break # このスコープのiframe探索を打ち切り
                    remaining_time_ms_inner = timeout - elapsed_time_ms_inner
                    if remaining_time_ms_inner < 50: break # 残り時間が少なすぎる場合

                    iframe_step_start_time = time.monotonic()
                    try:
                        # nth=i で i番目の可視iframeを特定
                        nth_iframe_selector = f"{iframe_base_selector} >> nth={i}"
                        next_frame_locator = current_scope.frame_locator(nth_iframe_selector)
                        # iframeが有効かどうかのチェックタイムアウト
                        effective_iframe_check_timeout = max(50, min(iframe_check_timeout, int(remaining_time_ms_inner - 50)))
                        # iframe内のルート要素が存在するかで有効性を判断
                        await next_frame_locator.locator(':root').wait_for(state='attached', timeout=effective_iframe_check_timeout)
                        # 訪問済みでなければキューに追加
                        scope_id = id(next_frame_locator) # FrameLocatorオブジェクトのIDで訪問管理
                        if scope_id not in visited_scope_ids:
                            visited_scope_ids.add(scope_id)
                            queue.append((next_frame_locator, current_depth + 1))
                            iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                            logger.debug(f"        キューに追加(単一): スコープ=FrameLocator(nth={i}), 新深度={current_depth + 1} ({iframe_step_elapsed:.0f}ms)")
                        else:
                            logger.debug(f"        スキップ(単一): FrameLocator(nth={i}) は訪問済み")
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
    """
    指定された起点からiframe内を含めて動的に複数の要素を探索します。
    見つかったすべての要素のLocatorとそのスコープのタプルのリストを返します。
    タイムアウトした場合は、それまでに見つかった要素のリストを返します。
    """
    logger.info(f"動的探索(複数)開始: 起点={type(base_locator).__name__}, セレクター='{target_selector}', 最大深度={max_depth}, 全体タイムアウト={timeout}ms")
    start_time = time.monotonic()
    found_elements: List[Tuple[Locator, Union[Page, FrameLocator]]] = []
    queue: Deque[Tuple[Union[Page, FrameLocator], int]] = deque([(base_locator, 0)])
    visited_scope_ids = {id(base_locator)}
    iframe_check_timeout = config.IFRAME_LOCATOR_TIMEOUT # iframe有効性確認のタイムアウト
    logger.debug(f"  フレーム確認タイムアウト: {iframe_check_timeout}ms")

    while queue:
        current_monotonic_time = time.monotonic()
        elapsed_time_ms = (current_monotonic_time - start_time) * 1000
        if elapsed_time_ms >= timeout:
            logger.warning(f"動的探索(複数)タイムアウト ({timeout}ms) - 経過時間: {elapsed_time_ms:.0f}ms")
            break # 時間切れの場合はループを抜ける
        remaining_time_ms = timeout - elapsed_time_ms
        if remaining_time_ms < 100: # 残り時間が少なすぎる場合は探索打ち切り
             logger.warning(f"動的探索(複数)の残り時間がわずかなため ({remaining_time_ms:.0f}ms)、探索を打ち切ります。")
             break

        current_scope, current_depth = queue.popleft()
        scope_type_name = type(current_scope).__name__
        scope_identifier = f" ({repr(current_scope)})" if isinstance(current_scope, FrameLocator) else ""
        logger.debug(f"  探索中(複数): スコープ={scope_type_name}{scope_identifier}, 深度={current_depth}, 残り時間: {remaining_time_ms:.0f}ms")

        step_start_time = time.monotonic()
        try:
            # 要素が表示されているかに関わらず、スコープ内のすべての要素を取得
            # all() は要素がなくてもエラーにならない（空リストを返す）
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

        # --- iframe探索 ---
        if current_depth < max_depth:
            # 再度時間チェック
            current_monotonic_time = time.monotonic()
            elapsed_time_ms = (current_monotonic_time - start_time) * 1000
            if elapsed_time_ms >= timeout: continue # 時間切れなら次のキューへ
            remaining_time_ms = timeout - elapsed_time_ms
            if remaining_time_ms < 100: continue # 残り時間が少なすぎる場合

            step_start_time = time.monotonic()
            logger.debug(f"    スコープ '{scope_type_name}{scope_identifier}' (深度 {current_depth}) 内の可視iframeを探索...")
            try:
                iframe_base_selector = 'iframe:visible' # 可視iframeのみを対象
                visible_iframe_locators = current_scope.locator(iframe_base_selector)
                count = await visible_iframe_locators.count()
                step_elapsed = (time.monotonic() - step_start_time) * 1000
                if count > 0: logger.debug(f"      発見した可視iframe候補数: {count} ({step_elapsed:.0f}ms)")

                for i in range(count):
                    # ループ内でも時間チェック
                    current_monotonic_time_inner = time.monotonic()
                    elapsed_time_ms_inner = (current_monotonic_time_inner - start_time) * 1000
                    if elapsed_time_ms_inner >= timeout:
                         logger.warning(f"動的探索(複数)タイムアウト ({timeout}ms) - iframeループ中")
                         break # このスコープのiframe探索を打ち切り
                    remaining_time_ms_inner = timeout - elapsed_time_ms_inner
                    if remaining_time_ms_inner < 50: break # 残り時間が少なすぎる場合

                    iframe_step_start_time = time.monotonic()
                    try:
                        nth_iframe_selector = f"{iframe_base_selector} >> nth={i}"
                        next_frame_locator = current_scope.frame_locator(nth_iframe_selector)
                        # iframeが有効かどうかのチェックタイムアウト
                        effective_iframe_check_timeout = max(50, min(iframe_check_timeout, int(remaining_time_ms_inner - 50)))
                        await next_frame_locator.locator(':root').wait_for(state='attached', timeout=effective_iframe_check_timeout)
                        # 訪問済みでなければキューに追加
                        scope_id = id(next_frame_locator)
                        if scope_id not in visited_scope_ids:
                            visited_scope_ids.add(scope_id)
                            queue.append((next_frame_locator, current_depth + 1))
                            iframe_step_elapsed = (time.monotonic() - iframe_step_start_time) * 1000
                            logger.debug(f"        キューに追加(複数): スコープ=FrameLocator(nth={i}), 新深度={current_depth + 1} ({iframe_step_elapsed:.0f}ms)")
                        else:
                             logger.debug(f"        スキップ(複数): FrameLocator(nth={i}) は訪問済み")
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