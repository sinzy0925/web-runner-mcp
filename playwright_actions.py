# --- ファイル: playwright_actions.py ---
"""
Playwrightの各アクション（クリック、入力、取得など）を実行するコアロジック。
"""
import asyncio
import logging
import os
import time
import pprint
import traceback
from urllib.parse import urljoin
from typing import List, Tuple, Optional, Union, Dict, Any

from playwright.async_api import (
    Page,
    Frame,
    Locator,
    FrameLocator,
    BrowserContext,
    TimeoutError as PlaywrightTimeoutError,
    Error as PlaywrightError,
    APIRequestContext
)

import config
import utils # PDF処理などで使用
from playwright_finders import find_element_dynamically, find_all_elements_dynamically
from playwright_helper_funcs import get_page_inner_text

logger = logging.getLogger(__name__)

async def execute_actions_async(
    initial_page: Page,
    actions: List[Dict[str, Any]],
    api_request_context: APIRequestContext,
    default_timeout: int
) -> Tuple[bool, List[Dict[str, Any]]]:
    """
    指定されたページを起点として、定義されたアクションリストを順に実行します。
    iframeの探索や切り替え、データ取得、エラーハンドリングなどを行います。
    実行全体の成否 (bool) と、各ステップの結果詳細のリスト (List[dict]) を返します。
    """
    results: List[Dict[str, Any]] = []
    current_target: Union[Page, FrameLocator] = initial_page # 現在の操作対象スコープ
    root_page: Page = initial_page # ルートとなるページオブジェクト (ページ遷移後も更新)
    current_context: BrowserContext = root_page.context # 現在のブラウザコンテキスト
    iframe_stack: List[Union[Page, FrameLocator]] = [] # iframe切り替えのためのスタック

    for i, step_data in enumerate(actions):
        step_num = i + 1
        action = step_data.get("action", "").lower()
        selector = step_data.get("selector")
        iframe_selector_input = step_data.get("iframe_selector")
        value = step_data.get("value")
        attribute_name = step_data.get("attribute_name")
        option_type = step_data.get("option_type")
        option_value = step_data.get("option_value")
        # アクション固有タイムアウト > 全体デフォルトタイムアウト > configデフォルト
        action_wait_time = step_data.get("wait_time_ms", default_timeout)

        logger.info(f"--- ステップ {step_num}/{len(actions)}: Action='{action}' ---")
        step_info = {"selector": selector, "value": value, "iframe(指定)": iframe_selector_input,
                     "option_type": option_type, "option_value": option_value, "attribute_name": attribute_name}
        # Noneでない値だけをログに出力
        step_info_str = ", ".join([f"{k}='{str(v)[:50]}{'...' if len(str(v)) > 50 else ''}'" # 値が長い場合は省略
                                   for k, v in step_info.items() if v is not None])
        logger.info(f"詳細: {step_info_str} (timeout: {action_wait_time}ms)")

        try:
            # 各ステップ開始前にページが閉じられていないか確認
            if root_page.is_closed():
                raise PlaywrightError(f"Root page was closed before step {step_num}.")
            # 現在の状態をログ出力
            current_base_url = root_page.url
            root_page_title = await root_page.title()
            current_target_type = type(current_target).__name__
            logger.info(f"現在のルートページ: URL='{current_base_url}', Title='{root_page_title}'")
            logger.info(f"現在の探索スコープ: {current_target_type}")
        except Exception as e:
            logger.error(f"ステップ {step_num} 開始前の状態取得中にエラー: {e}", exc_info=True)
            results.append({"step": step_num, "status": "error", "action": action, "message": f"Failed to get target info before step: {e}"})
            return False, results # 状態取得失敗は致命的として中断

        try:
            # --- Iframe/Parent Frame 切替 ---
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
                    # 見つからない場合は明確なエラーとする
                    raise PlaywrightTimeoutError(f"指定されたiframe '{iframe_selector_input}' が現在のスコープ '{type(current_target).__name__}' で見つからないか、タイムアウト({action_wait_time}ms)しました。")
                except Exception as e:
                    raise PlaywrightError(f"Iframe '{iframe_selector_input}' への切り替え中に予期せぬエラーが発生しました: {e}")

                # 切り替え成功
                if id(current_target) not in [id(s) for s in iframe_stack]: # 現在のターゲットがまだスタックになければ追加
                    iframe_stack.append(current_target)
                current_target = target_frame_locator # ターゲットを新しい FrameLocator に更新
                logger.info(f"FrameLocator '{iframe_selector_input}' への切り替え成功。")
                results.append({"step": step_num, "status": "success", "action": action, "selector": iframe_selector_input})
                continue # 次のステップへ

            elif action == "switch_to_parent_frame":
                if not iframe_stack:
                    logger.warning("既にトップレベルフレームか、iframeスタックが空です。操作はスキップされます。")
                    # FrameLocatorの場合のみルートページに戻す安全策は維持
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


            # --- ページ全体操作 ---
            if action in ["wait_page_load", "sleep", "scroll_page_to_bottom"]:
                if action == "wait_page_load":
                    logger.info("ページの読み込み完了 (load) を待ちます...")
                    await root_page.wait_for_load_state("load", timeout=action_wait_time)
                    logger.info("ページの読み込みが完了しました。")
                    results.append({"step": step_num, "status": "success", "action": action})
                elif action == "sleep":
                    try:
                        seconds = float(value) if value is not None else 1.0 # デフォルト1秒
                        if seconds < 0: raise ValueError("Sleep time cannot be negative.")
                    except (TypeError, ValueError):
                        raise ValueError("Invalid value for sleep action. Must be a non-negative number (seconds).")
                    logger.info(f"{seconds:.1f} 秒待機します...")
                    await asyncio.sleep(seconds)
                    results.append({"step": step_num, "status": "success", "action": action, "duration_sec": seconds})
                elif action == "scroll_page_to_bottom":
                    logger.info("ページ最下部へスクロールします...")
                    # JavaScriptを実行してスクロール
                    await root_page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                    await asyncio.sleep(0.5) # スクロール後の描画やイベント発生を少し待つ
                    logger.info("ページ最下部へのスクロールが完了しました。")
                    results.append({"step": step_num, "status": "success", "action": action})
                continue # 次のステップへ


            # --- 要素操作のための準備 ---
            element: Optional[Locator] = None # 単一要素操作用
            found_elements_list: List[Tuple[Locator, Union[Page, FrameLocator]]] = [] # 複数要素操作用
            found_scope: Optional[Union[Page, FrameLocator]] = None # 要素が見つかったスコープ

            # アクションが単一要素を必要とするか、複数要素を対象とするか
            single_element_required_actions = ["click", "input", "hover", "get_inner_text", "get_text_content", "get_inner_html", "get_attribute", "wait_visible", "select_option", "scroll_to_element"]
            multiple_elements_actions = ["get_all_attributes", "get_all_text_contents"]
            # スクリーンショットはセレクターがあれば単一要素、なければページ全体
            is_screenshot_element = action == "screenshot" and selector is not None

            if action in single_element_required_actions or action in multiple_elements_actions or is_screenshot_element:
                if not selector:
                    raise ValueError(f"Action '{action}' requires a 'selector'.")

                # --- 要素探索 ---
                if action in single_element_required_actions or is_screenshot_element:
                    # 探索する要素の状態を決定
                    required_state = 'visible' if action in ['click', 'hover', 'screenshot', 'select_option', 'input', 'wait_visible', 'scroll_to_element'] else 'attached'
                    logger.info(f"単一要素 '{selector}' (状態: {required_state}) を動的に探索します...")
                    element, found_scope = await find_element_dynamically(
                        current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time, target_state=required_state
                    )
                    if not element or not found_scope:
                        # 要素が見つからない場合は明確なエラーとして処理を中断
                        error_msg = f"要素 '{selector}' (状態: {required_state}) が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。"
                        logger.error(error_msg)
                        results.append({"step": step_num, "status": "error", "action": action, "selector": selector, "required_state": required_state, "message": error_msg})
                        return False, results # Falseを返して処理中断

                    # 要素が見つかったスコープが現在の探索スコープと異なる場合、ターゲットを更新
                    if id(found_scope) != id(current_target):
                        found_scope_type = type(found_scope).__name__
                        logger.info(f"要素発見スコープ({found_scope_type})が現在のスコープ({type(current_target).__name__})と異なるため、探索スコープを更新します。")
                        # スタック管理: 現在のターゲットをスタックに追加 (重複回避)
                        if id(current_target) not in [id(s) for s in iframe_stack]:
                            iframe_stack.append(current_target)
                        current_target = found_scope
                    logger.info(f"最終的な単一操作対象スコープ: {type(current_target).__name__}")

                elif action in multiple_elements_actions:
                    logger.info(f"複数要素 '{selector}' を動的に探索します...")
                    found_elements_list = await find_all_elements_dynamically(
                        current_target, selector, max_depth=config.DYNAMIC_SEARCH_MAX_DEPTH, timeout=action_wait_time
                    )
                    if not found_elements_list:
                        # 複数要素が見つからなくてもエラーとはせず、警告ログに留め、後続処理で空リストとして扱う
                        logger.warning(f"要素 '{selector}' が現在のスコープおよび探索可能なiframe (深さ{config.DYNAMIC_SEARCH_MAX_DEPTH}まで) 内で見つかりませんでした。")
                    # 複数要素の場合、見つかった各要素のスコープは異なる可能性があるため、current_target は更新しない


            # --- 各アクション実行 ---
            action_result_details = {"selector": selector} if selector else {} # 結果にセレクター情報を含める

            if action == "click":
                if not element: raise ValueError("Click action requires an element, but it was not found.")
                logger.info("要素をクリックします...")
                context_for_click = root_page.context # 新しいページが開くイベントはルートページのコンテキストで捕捉
                new_page: Optional[Page] = None
                try:
                    # 新しいページが開く可能性を考慮して待機 (タイムアウトは短めに設定)
                    async with context_for_click.expect_page(timeout=config.NEW_PAGE_EVENT_TIMEOUT) as new_page_info:
                        await element.click(timeout=action_wait_time)
                    # タイムアウト内に新しいページが開いた場合
                    new_page = await new_page_info.value
                    new_page_url = new_page.url
                    logger.info(f"クリックにより新しいページが開きました: URL={new_page_url}")
                    try:
                        # 新しいページのロード完了を待つ (タイムアウトはアクション固有時間)
                        await new_page.wait_for_load_state("load", timeout=action_wait_time)
                        logger.info("新しいページのロードが完了しました。")
                    except PlaywrightTimeoutError:
                        logger.warning(f"新しいページのロード待機がタイムアウトしました ({action_wait_time}ms)。処理は続行します。")
                    # 操作対象を新しいページに切り替え、iframeスタックをクリア
                    root_page = new_page
                    current_target = new_page
                    current_context = new_page.context
                    iframe_stack.clear()
                    logger.info("スコープを新しいページにリセットしました。iframeスタックもクリアされました。")
                    action_result_details.update({"new_page_opened": True, "new_page_url": new_page_url})
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})
                except PlaywrightTimeoutError:
                    # expect_pageがタイムアウトした場合 (新しいページが開かなかった場合)
                    logger.info(f"クリックは完了しましたが、{config.NEW_PAGE_EVENT_TIMEOUT}ms 以内に新しいページは開きませんでした。")
                    action_result_details["new_page_opened"] = False
                    results.append({"step": step_num, "status": "success", "action": action, **action_result_details})
                except Exception as click_err:
                    # クリック自体が失敗した場合など
                    logger.error(f"クリック操作中に予期せぬエラーが発生しました: {click_err}", exc_info=True)
                    raise click_err # エラーを再送出してステップ全体のエラーハンドリングに任せる

            elif action == "input":
                 if not element: raise ValueError("Input action requires an element.")
                 if value is None: raise ValueError("Input action requires 'value'.")
                 input_value_str = str(value)
                 logger.info(f"要素に '{input_value_str[:50]}{'...' if len(input_value_str) > 50 else ''}' を入力します...")
                 # fill: 要素の内容をクリアしてから入力する
                 await element.fill(input_value_str, timeout=action_wait_time)
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
                 if not element: raise ValueError("Get innerText action requires an element.")
                 logger.info("要素の innerText を取得します...")
                 text = await element.inner_text(timeout=action_wait_time)
                 text = text.strip() if text else "" # 前後の空白除去
                 logger.info(f"取得テキスト(innerText): '{text[:100]}{'...' if len(text) > 100 else ''}'") # 長いテキストは省略
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_text_content":
                 if not element: raise ValueError("Get textContent action requires an element.")
                 logger.info("要素の textContent を取得します...")
                 text = await element.text_content(timeout=action_wait_time)
                 text = text.strip() if text else "" # 前後の空白を除去
                 logger.info(f"取得テキスト(textContent): '{text[:100]}{'...' if len(text) > 100 else ''}'")
                 action_result_details["text"] = text
                 results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_inner_html":
                 if not element: raise ValueError("Get innerHTML action requires an element.")
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
                processed_value = attr_value # 結果に含める値（URL変換やPDFテキストが入る可能性）

                # --- href属性の場合の特別処理 ---
                if attribute_name.lower() == 'href' and attr_value is not None:
                    original_url = attr_value
                    try:
                        # 絶対URLに変換
                        absolute_url = urljoin(current_base_url, original_url)
                        if original_url != absolute_url:
                            logger.info(f"  href属性値を絶対URLに変換: '{original_url}' -> '{absolute_url}'")
                        processed_value = absolute_url # 結果には絶対URLを

                        # PDFかどうかを判定して処理
                        if isinstance(absolute_url, str) and absolute_url.lower().endswith('.pdf'):
                            logger.info(f"  リンク先がPDFファイルです。ダウンロードとテキスト抽出を試みます: {absolute_url}")
                            # PDFダウンロード (utilsを使用)
                            pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                            if pdf_bytes:
                                # PDFテキスト抽出 (utilsを使用, 同期関数を非同期実行)
                                pdf_text_content = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                if isinstance(pdf_text_content, str) and pdf_text_content.startswith("Error:"):
                                     logger.error(f"  PDFテキスト抽出エラー: {pdf_text_content}")
                                else:
                                     log_text = pdf_text_content[:200] + '...' if pdf_text_content and len(pdf_text_content) > 200 else pdf_text_content
                                     logger.info(f"  PDFテキスト抽出完了 (先頭200文字): {log_text if log_text else 'None'}")
                            else:
                                pdf_text_content = "Error: PDF download failed or returned no data."
                                logger.error(f"  PDFダウンロード失敗: {absolute_url}")
                        else:
                             logger.debug(f"  リンク先はPDFではありません ({absolute_url})。")
                    except Exception as url_e:
                        error_detail = f"Error processing URL or PDF: {url_e}"
                        logger.error(f"  URL処理またはPDF処理中にエラーが発生しました (URL: '{original_url}'): {url_e}", exc_info=True)
                        pdf_text_content = error_detail # エラー情報を結果に含める

                logger.info(f"取得した属性値 ({attribute_name}): '{processed_value}'")
                action_result_details.update({"attribute": attribute_name, "value": processed_value}) # 結果には処理後の値を保存
                if pdf_text_content is not None:
                    action_result_details["pdf_text"] = pdf_text_content # PDFテキストも結果に含める
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "get_all_attributes":
                if not selector: raise ValueError("Action 'get_all_attributes' requires 'selector'.")
                if not attribute_name: raise ValueError("Action 'get_all_attributes' requires 'attribute_name'.")

                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、属性取得をスキップします。")
                    action_result_details["attribute_list"] = [] # 空リストを結果として設定
                    # href, pdf, content モードの場合も空リストを設定
                    if attribute_name.lower() in ['href', 'pdf', 'content']:
                        action_result_details["url_list"] = []
                    if attribute_name.lower() == 'pdf':
                        action_result_details["pdf_texts"] = []
                    if attribute_name.lower() == 'content':
                        action_result_details["scraped_texts"] = []
                else:
                    num_found = len(found_elements_list)
                    logger.info(f"動的探索で見つかった {num_found} 個の要素から属性/コンテンツ '{attribute_name}' を取得します。")

                    # 結果格納用リスト
                    url_list_for_file: List[Optional[str]] = []
                    pdf_texts_list_for_file: List[Optional[str]] = []
                    scraped_texts_list_for_file: List[Optional[str]] = []
                    generic_attribute_list_for_file: List[Optional[str]] = []

                    # --- 属性名に応じて処理を分岐 ---
                    if attribute_name.lower() in ['href', 'pdf', 'content']:
                        logger.info("href属性を取得し、絶対URLに変換、必要に応じてコンテンツを取得します...")

                        # 同時実行数を制御するためのセマフォを作成 (同時実行数5)
                        CONCURRENT_LIMIT = 5
                        semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
                        logger.info(f"URLアクセス/コンテンツ取得の同時実行数を {CONCURRENT_LIMIT} に制限します。")

                        # 個々の要素から属性/コンテンツを取得する内部関数 (セマフォを受け取るように変更)
                        async def process_single_element_for_href_content(
                            locator: Locator, index: int, base_url: str, attr_mode: str, sem: asyncio.Semaphore # セマフォ引数を追加
                        ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
                            """ 1要素のhrefを取得し、モードに応じてPDF/コンテンツも取得 (セマフォで同時実行制御) """
                            original_href: Optional[str] = None
                            absolute_url: Optional[str] = None
                            pdf_text: Optional[str] = None
                            scraped_text: Optional[str] = None

                            # セマフォを取得するまで待機し、処理ブロックを実行
                            async with sem:
                                try:
                                    logger.debug(f"  [{index+1}/{num_found}] Processing started (Semaphore acquired)...")
                                    # href属性取得 (個々のタイムアウトは短めに調整)
                                    href_timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)

                                    original_href = await locator.get_attribute("href", timeout=href_timeout)
                                    if original_href is None:
                                        logger.debug(f"  [{index+1}/{num_found}] href属性が見つかりません。")
                                        return None, None, None # この要素の処理は終了

                                    # 絶対URL変換
                                    try:
                                        absolute_url = urljoin(base_url, original_href)
                                    except Exception as url_conv_e:
                                         logger.warning(f"  [{index+1}/{num_found}] 絶対URL変換エラー ({original_href}): {url_conv_e}")
                                         absolute_url = f"Error converting URL: {original_href}" # エラー情報を含める

                                    # pdf モードの場合
                                    if attr_mode == 'pdf' and isinstance(absolute_url, str) and absolute_url.lower().endswith('.pdf'):
                                        pdf_start = time.monotonic()
                                        pdf_bytes = await utils.download_pdf_async(api_request_context, absolute_url)
                                        if pdf_bytes:
                                            pdf_text = await asyncio.to_thread(utils.extract_text_from_pdf_sync, pdf_bytes)
                                        else:
                                            pdf_text = "Error: PDF download failed or returned no data."
                                        pdf_elapsed = (time.monotonic() - pdf_start) * 1000
                                        logger.info(f"  [{index+1}/{num_found}] PDF処理完了 ({pdf_elapsed:.0f}ms) URL: {absolute_url}")

                                    # content モードの場合 (PDF以外)
                                    elif attr_mode == 'content' and isinstance(absolute_url, str) and not absolute_url.lower().endswith('.pdf'):
                                        content_start = time.monotonic()
                                        # ページテキスト取得 (get_page_inner_textを使用, タイムアウトはアクション全体時間)
                                        success, content_or_error = await get_page_inner_text(current_context, absolute_url, action_wait_time)
                                        scraped_text = content_or_error
                                        content_elapsed = (time.monotonic() - content_start) * 1000
                                        logger.info(f"  [{index+1}/{num_found}] Content取得試行完了 ({content_elapsed:.0f}ms) URL: {absolute_url} Success: {success}")

                                    logger.debug(f"  [{index+1}/{num_found}] Processing finished (Semaphore released). URL: {absolute_url}")
                                    return absolute_url, pdf_text, scraped_text

                                except PlaywrightTimeoutError:
                                    logger.warning(f"  [{index+1}/{num_found}] href属性取得タイムアウト ({href_timeout}ms)。")
                                    return f"Error: Timeout getting href", None, None
                                except Exception as e:
                                    logger.warning(f"  [{index+1}/{num_found}] href/コンテンツ取得中に予期せぬエラー: {type(e).__name__} - {e}", exc_info=True)
                                    return f"Error: {type(e).__name__} - {e}", None, None
                            # 'async with sem:' を抜けるとセマフォは自動的に解放される

                        # --- 見つかった全要素に対して並行処理 (タスク作成時にセマフォを渡す) ---
                        process_tasks = [
                            process_single_element_for_href_content(loc, idx, current_base_url, attribute_name.lower(), semaphore)
                            for idx, (loc, _) in enumerate(found_elements_list)
                        ]
                        # asyncio.gather は全てのタスクが完了するのを待つ
                        results_tuples = await asyncio.gather(*process_tasks)

                        # 結果をリストに格納
                        for abs_url, pdf_content, scraped_content in results_tuples:
                            url_list_for_file.append(abs_url)
                            pdf_texts_list_for_file.append(pdf_content)
                            scraped_texts_list_for_file.append(scraped_content)

                        logger.info(f"全 {num_found} 要素の href/コンテンツ処理が完了しました。")

                        # 結果を action_result_details に格納
                        action_result_details["attribute"] = attribute_name
                        action_result_details["url_list"] = url_list_for_file # 常にURLリストを含める
                        if attribute_name.lower() == 'pdf':
                             action_result_details["pdf_texts"] = pdf_texts_list_for_file
                        if attribute_name.lower() == 'content':
                             action_result_details["scraped_texts"] = scraped_texts_list_for_file
                        # hrefモードの場合は url_list が主要な結果

                    else: # href, pdf, content 以外の通常の属性取得
                        logger.info(f"指定された属性 '{attribute_name}' を取得します...")
                        # 属性値を取得する内部関数
                        async def get_single_attr(locator: Locator, attr_name: str, index: int) -> Optional[str]:
                            try:
                                attr_timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)
                                return await locator.get_attribute(attr_name, timeout=attr_timeout)
                            except PlaywrightTimeoutError:
                                logger.warning(f"  [{index+1}/{num_found}] 属性 '{attr_name}' 取得タイムアウト ({attr_timeout}ms)。")
                                return f"Error: Timeout getting attribute '{attr_name}'"
                            except Exception as e:
                                logger.warning(f"  [{index+1}/{num_found}] 属性 '{attr_name}' 取得中にエラー: {type(e).__name__}")
                                return f"Error: {type(e).__name__} getting attribute '{attr_name}'"

                        # 並行処理で属性を取得
                        attr_tasks = [
                            get_single_attr(loc, attribute_name, idx)
                            for idx, (loc, _) in enumerate(found_elements_list)
                        ]
                        generic_attribute_list_for_file = await asyncio.gather(*attr_tasks)

                        action_result_details.update({"attribute": attribute_name, "attribute_list": generic_attribute_list_for_file})
                        logger.info(f"取得した属性値リスト ({len(generic_attribute_list_for_file)}件)")

                # 最後に結果を追加
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})


            elif action == "get_all_text_contents":
                if not selector: raise ValueError("Action 'get_all_text_contents' requires 'selector'.")
                text_list: List[Optional[str]] = []
                if not found_elements_list:
                    logger.warning(f"動的探索で要素 '{selector}' が見つからなかったため、テキスト取得をスキップします。")
                else:
                    num_found = len(found_elements_list)
                    logger.info(f"動的探索で見つかった {num_found} 個の要素から textContent を取得します。")
                    # textContent を取得する内部関数
                    async def get_single_text(locator: Locator, index: int) -> Optional[str]:
                        try:
                            text_timeout = max(500, action_wait_time // num_found if num_found > 5 else action_wait_time // 3)
                            text = await locator.text_content(timeout=text_timeout)
                            return text.strip() if text else "" # 前後空白除去
                        except PlaywrightTimeoutError:
                             logger.warning(f"  [{index+1}/{num_found}] textContent取得タイムアウト ({text_timeout}ms)。")
                             return "Error: Timeout getting textContent"
                        except Exception as e:
                            logger.warning(f"  [{index+1}/{num_found}] textContent 取得中にエラー: {type(e).__name__}")
                            return f"Error: {type(e).__name__} getting textContent"

                    # 並行処理でテキストを取得
                    get_text_tasks = [
                        get_single_text(loc, idx) for idx, (loc, _) in enumerate(found_elements_list)
                    ]
                    text_list = await asyncio.gather(*get_text_tasks)
                    logger.info(f"取得したテキストリスト ({len(text_list)}件)")

                action_result_details["text_list"] = text_list
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "wait_visible":
                # 要素探索自体が required_state='visible' で行われているため、
                # ここに到達した時点で要素は可視のはず。ログのみ。
                if not element: raise ValueError("Wait visible action requires an element.")
                logger.info("要素が表示されていることを確認しました (動的探索時に確認済み)。")
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "select_option":
                  if not element: raise ValueError("Select option action requires an element.")
                  if option_type not in ['value', 'index', 'label'] or option_value is None:
                       raise ValueError("Invalid 'option_type' or 'option_value' for select_option action.")
                  logger.info(f"ドロップダウンを選択します (Type: {option_type}, Value: '{option_value}')...")
                  # select_option は内部で要素が選択可能になるのを待つ
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
                   if not element: raise ValueError("Scroll action requires an element.")
                   logger.info("要素が表示されるまでスクロールします...")
                   # scroll_into_view_if_needed は要素がビューポートに入るようにスクロールする
                   await element.scroll_into_view_if_needed(timeout=action_wait_time)
                   await asyncio.sleep(0.3) # スクロール後の安定待ち
                   logger.info("要素へのスクロールが成功しました。")
                   results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            elif action == "screenshot":
                  # ファイル名の決定 (valueがあればそれ、なければデフォルト名)
                  filename_base = str(value).strip() if value else f"screenshot_step{step_num}"
                  # 拡張子がなければ .png を追加
                  filename = f"{filename_base}.png" if not filename_base.lower().endswith(('.png', '.jpg', '.jpeg')) else filename_base
                  screenshot_path = os.path.join(config.DEFAULT_SCREENSHOT_DIR, filename)
                  # ディレクトリが存在しない場合は作成
                  os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)
                  logger.info(f"スクリーンショットを '{screenshot_path}' に保存します...")
                  if element: # 要素が指定されていれば要素のスクリーンショット
                       logger.info("要素のスクリーンショットを取得します...")
                       await element.screenshot(path=screenshot_path, timeout=action_wait_time)
                       logger.info("要素のスクリーンショットを保存しました。")
                  else: # 要素が指定されていなければページ全体
                       logger.info("ページ全体のスクリーンショットを取得します...")
                       # ページ全体は時間がかかる可能性があるのでタイムアウトを少し長くする
                       await root_page.screenshot(path=screenshot_path, full_page=True, timeout=action_wait_time*2)
                       logger.info("ページ全体のスクリーンショットを保存しました。")
                  action_result_details["filename"] = screenshot_path # 結果にファイルパスを含める
                  results.append({"step": step_num, "status": "success", "action": action, **action_result_details})

            else:
                  known_actions = [
                      "click", "input", "hover", "get_inner_text", "get_text_content",
                      "get_inner_html", "get_attribute", "get_all_attributes", "get_all_text_contents",
                      "wait_visible", "select_option", "screenshot", "scroll_page_to_bottom",
                      "scroll_to_element", "wait_page_load", "sleep", "switch_to_iframe",
                      "switch_to_parent_frame"
                  ]
                  if action not in known_actions:
                     logger.warning(f"未定義または不明なアクション '{action}' です。このステップはスキップされます。")
                     results.append({"step": step_num, "status": "skipped", "action": action, "message": f"Undefined action: {action}"})

        # --- ステップごとのエラーハンドリング ---
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
                     # エラー時のスクショはタイムアウト短め、フルページで試す
                     await root_page.screenshot(path=error_ss_path, full_page=True, timeout=10000)
                     logger.info(f"エラー発生時のスクリーンショットを保存しました: {error_ss_path}")
                     error_screenshot_path = error_ss_path
                 except Exception as ss_e:
                     logger.error(f"エラー発生時のスクリーンショット保存に失敗しました: {ss_e}")
            elif root_page and root_page.is_closed():
                 # ページが閉じられている場合、エラーメッセージに追記
                 error_message += " (Note: Root page was closed during execution, possibly due to an unexpected navigation or crash)"
                 logger.warning("根本原因: ルートページが閉じられた可能性があります。")

            # エラー情報を結果リストに追加
            error_details = {
                "step": step_num,
                "status": "error",
                "action": action,
                "selector": selector, # エラー発生時のセレクターも記録
                "message": str(e), # エラーメッセージ本文
                "full_error": error_message, # より詳細なエラー情報 (スタックトレースはログのみ)
                "traceback": traceback.format_exc() # スタックトレースも結果に含める（デバッグ用）
            }
            if error_screenshot_path:
                error_details["error_screenshot"] = error_screenshot_path
            results.append(error_details)
            return False, results # Falseを返して処理中断

    # 全てのステップが正常に完了した場合
    return True, results