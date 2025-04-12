# --- ファイル: main_mcp.py (iframe自動探索 再々修正・完成版) ---
import asyncio
import json
import logging
import os
import sys
import time # sleep用
import pprint
import traceback
import fitz  # PyMuPDF
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
from typing import List, Tuple, Optional, Union, Any, Dict
from urllib.parse import urljoin
import warnings
import inspect

# ▼▼▼ 特定の ResourceWarning を無視する設定 ▼▼▼
warnings.filterwarnings("ignore", category=ResourceWarning, message="unclosed transport")

# --- 設定 ---
DEFAULT_ACTION_TIMEOUT = 5000
IFRAME_SEARCH_TIMEOUT  = 2000 # find_in_all_frames_new で使用
IFRAME_LOCATOR_TIMEOUT = 2000 # この定数は現在直接使われていないが、将来のために残す
PDF_DOWNLOAD_TIMEOUT   = 60000
NEW_PAGE_EVENT_TIMEOUT = 2000

LOG_FILE = 'output_web_runner.log'
DEFAULT_SCREENSHOT_DIR = 'screenshots'

# --- ロギング設定 ---
log_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
root_logger = logging.getLogger()

log_dir = os.path.dirname(LOG_FILE)
if log_dir and not os.path.exists(log_dir):
    try: os.makedirs(log_dir, exist_ok=True)
    except Exception as e: print(f"Warning: Failed to create log directory {log_dir}: {e}")

log_file_path = os.path.abspath(LOG_FILE)
if not any(isinstance(h, logging.FileHandler) and h.baseFilename == log_file_path for h in root_logger.handlers):
    try:
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
        file_handler.setFormatter(log_formatter)
        root_logger.addHandler(file_handler)
    except Exception as e: print(f"Warning: Failed to create file log handler for {log_file_path}: {e}")

if not any(isinstance(h, logging.StreamHandler) and getattr(h, 'stream', None) == sys.stderr for h in root_logger.handlers):
     stream_handler = logging.StreamHandler(sys.stderr)
     stream_handler.setFormatter(log_formatter)
     root_logger.addHandler(stream_handler)

root_logger.setLevel(logging.INFO)

# --- PDFテキスト抽出ヘルパー (変更なし) ---
def extract_text_from_pdf_sync(pdf_data: bytes) -> Optional[str]:
    doc = None
    try:
        logging.info("PDFデータからテキスト抽出中...")
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        text = ""
        logging.info(f"PDFページ数: {len(doc)}")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text", sort=True)
            if page_text: text += page_text + "\n--- Page Separator ---\n"
            else: logging.warning(f"ページ {page_num + 1} からテキスト抽出できず。")
        logging.info(f"PDFテキスト抽出完了。文字数: {len(text)}")
        cleaned_text = '\n'.join([line.strip() for line in text.splitlines() if line.strip()])
        return cleaned_text
    except fitz.FitzError as e: logging.error(f"PDFファイルの処理中にエラー (PyMuPDF): {e}", exc_info=True); return f"Error: Failed to process PDF - {e}"
    except Exception as e: logging.error(f"PDFテキスト抽出中に予期せぬエラー: {e}", exc_info=True); return f"Error: Unexpected error during PDF extraction - {e}"
    finally:
        if doc:
            try: doc.close(); logging.debug("PDFドキュメントを閉じました。")
            except Exception as close_e: logging.error(f"PDFドキュメントのクローズ中にエラー: {close_e}")

# --- PDFダウンロードヘルパー (変更なし) ---
async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    logging.info(f"PDFを非同期でダウンロード中: {url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
        response = await api_request_context.get(url, headers=headers, timeout=PDF_DOWNLOAD_TIMEOUT)
        if not response.ok:
            logging.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}")
            try:
                body_text = await response.text(timeout=5000)
                logging.debug(f"エラーレスポンスボディ (一部): {body_text[:500]}")
            except Exception as body_err: logging.error(f"エラーレスポンスボディ読み取りエラー: {body_err}")
            return None
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type: logging.warning(f"Content-TypeがPDFではありません ({url}): {content_type}。処理を試みますが失敗する可能性があります。")
        body = await response.body()
        logging.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes")
        return body
    except PlaywrightTimeoutError: logging.error(f"PDFダウンロード中にタイムアウト ({url})"); return None
    except Exception as e: logging.error(f"PDF非同期ダウンロード中にエラー ({url}): {e}", exc_info=True); return None

# --- iframeセレクター生成ヘルパー (変更なし) ---
async def generate_iframe_selector_async(iframe_locator: Locator) -> Optional[str]:
    try:
        iframe_id = await iframe_locator.get_attribute('id', timeout=500)
        if iframe_id: return f'iframe[id="{iframe_id}"]'
        iframe_name = await iframe_locator.get_attribute('name', timeout=500)
        if iframe_name: return f'iframe[name="{iframe_name}"]'
        iframe_src = await iframe_locator.get_attribute('src', timeout=500)
        if iframe_src: return f'iframe[src="{iframe_src}"]'
    except Exception as e: logging.debug(f"iframe属性取得中にエラー（無視）: {e}")
    return None

# --- ★★★ 新しいフレーム内要素探索ヘルパー (Frameも返す) ★★★ ---
async def find_in_all_frames_new(
    current_scope: Union[Page, Frame], # FrameLocator は直接扱わない
    target_selector: str,
    is_multiple: bool = False, # 複数要素を探すかどうかのフラグ
    depth=0,
    max_depth=3
) -> Tuple[Optional[Union[Page, Frame]], Optional[Locator]]:
    """
    指定されたスコープ(Page or Frame)から開始し、要素が見つかるまで再帰的に
    子フレームを探索する。要素が見つかったスコープ(Page or Frame)と
    対応するLocatorを返す。
    """
    if depth > max_depth:
        logging.warning(f"  最大探索深度 {max_depth} に達したため、このフレーム以下の探索を中止します。")
        return None, None

    scope_id = "UnknownScope"
    if isinstance(current_scope, Page): scope_id = f"Page({current_scope.url})"
    elif isinstance(current_scope, Frame): scope_id = f"Frame({current_scope.name or current_scope.url})"
    logging.debug(f"    探索中 (Depth {depth}): {scope_id} で '{target_selector}' を探す...")

    try:
        short_timeout = max(1, IFRAME_SEARCH_TIMEOUT // 2)
        target_locator: Optional[Locator] = None

        if not is_multiple:
            element_in_scope = current_scope.locator(target_selector).first
            await element_in_scope.wait_for(state='attached', timeout=short_timeout)
            target_locator = element_in_scope
            logging.info(f"  要素 '{target_selector}' を {scope_id} で発見！")
        else:
             # count() にタイムアウトがないため、要素がないと遅い可能性がある
             count = await current_scope.locator(target_selector).count()
             if count > 0:
                  target_locator = current_scope.locator(target_selector)
                  logging.info(f"  要素 '{target_selector}' ({count}件) を {scope_id} で発見！")
             else:
                  raise PlaywrightTimeoutError("要素が見つかりません (count=0)")

        return current_scope, target_locator # 見つかったスコープ(Frame or Page)とLocatorを返す

    except (PlaywrightTimeoutError, PlaywrightError) as e_find:
        logging.debug(f"      {scope_id} 内では見つからず。 ({type(e_find).__name__})")
        try:
            child_frames = current_scope.child_frames
            frames_to_search = [f for f in child_frames if f != current_scope]

            if frames_to_search:
                  logging.debug(f"    {scope_id} の子フレーム ({len(frames_to_search)}個) を探索...")
                  tasks = [find_in_all_frames_new(frame, target_selector, is_multiple, depth + 1, max_depth) for frame in frames_to_search]
                  results_list = await asyncio.gather(*tasks)
                  for found_scope_recursive, found_element_recursive in results_list:
                       if found_element_recursive:
                           return found_scope_recursive, found_element_recursive # Frame オブジェクトもそのまま返す
            else:
                logging.debug(f"    {scope_id} に探索対象の子フレームなし。")

        except Exception as frame_e:
              logging.warning(f"    フレーム探索中に予期せぬエラー ({scope_id}): {frame_e}", exc_info=True)

        return None, None # 見つからなかった
    except Exception as scope_e:
         logging.warning(f"    スコープ {scope_id} の処理中にエラー: {scope_e}", exc_info=True)
         return None, None


# --- Playwright アクション実行コア (find_in_all_frames_new を使用) ---
async def execute_actions_async(initial_page: Page, actions: List[dict], api_request_context: APIRequestContext) -> Tuple[bool, List[dict]]:
    results: List[dict] = []
    current_target: Union[Page, FrameLocator] = initial_page # スコープ管理用
    root_page: Page = initial_page
    iframe_stack: List[Union[Page, FrameLocator]] = []

    for i, step_data in enumerate(actions):
        # ... (パラメータ取得、ログ、ターゲット情報取得は変更なし) ...
        step_num = i + 1; action = step_data.get("action", "").lower(); selector = step_data.get("selector")
        iframe_selector_input = step_data.get("iframe_selector"); value = step_data.get("value")
        attribute_name = step_data.get("attribute_name"); option_type = step_data.get("option_type")
        option_value = step_data.get("option_value");
        action_wait_time_input = step_data.get("wait_time_ms")
        try: action_wait_time = int(action_wait_time_input) if action_wait_time_input is not None else DEFAULT_ACTION_TIMEOUT; # ... (省略) ...
        except (ValueError, TypeError): action_wait_time = DEFAULT_ACTION_TIMEOUT; logging.warning(...)
        logging.info(f"--- ステップ {step_num}/{len(actions)}: Action='{action}' ---")
        step_info = {"selector": selector, "value (input/screenshot/sleep)": value, "iframe(指定)": iframe_selector_input, "option_type": option_type, "option_value": option_value, "attribute_name": attribute_name, "wait_time_ms": action_wait_time if action_wait_time != DEFAULT_ACTION_TIMEOUT else None,}; step_info_str = ", ".join([f"{k}='{v}'" for k, v in step_info.items() if v is not None]); logging.info(f"詳細: {step_info_str}")
        try:
            if root_page.is_closed(): raise PlaywrightError("Root page is closed.")
            current_base_url = root_page.url; root_page_title = await root_page.title(); current_target_type = type(current_target).__name__
            logging.info(f"現在のルートページ: URL='{current_base_url}', Title='{root_page_title}'")
            logging.info(f"現在の探索スコープ(current_target): {current_target_type}") # current_targetのログ追加
        except Exception as e: logging.error(...); results.append(...); return False, results

        try:
            # --- Iframe/Parent Frame 切替 (明示指定) ---
            if action == "switch_to_iframe":
                if not iframe_selector_input: raise ValueError(...)
                logging.info(f"[ユーザー指定] Iframe '{iframe_selector_input}' に切り替えます...")
                try:
                    target_frame_locator = current_target.frame_locator(iframe_selector_input)
                    await target_frame_locator.locator(':root').wait_for(state='attached', timeout=action_wait_time)
                except PlaywrightTimeoutError as e_timeout: raise PlaywrightTimeoutError(...) from e_timeout
                except Exception as e: raise PlaywrightError(...) from e
                if current_target not in iframe_stack: iframe_stack.append(current_target)
                current_target = target_frame_locator # スコープを FrameLocator に更新
                logging.info(f"FrameLocator への切り替え成功。現在のスコープ: {type(current_target).__name__}")
                results.append(...)
                continue
            elif action == "switch_to_parent_frame":
                if not iframe_stack: logging.warning(...); # ...
                else:
                    logging.info("[ユーザー指定] 親ターゲットに戻ります...")
                    current_target = iframe_stack.pop() # スタックから親スコープを取り出す
                    logging.info(f"親ターゲットへの切り替え成功。現在の探索スコープ: {type(current_target).__name__}")
                results.append(...)
                continue

            # --- ページ全体操作 ---
            if action in ["wait_page_load", "sleep", "scroll_page_to_bottom"]:
                # ... (変更なし) ...
                if action == "wait_page_load": await root_page.wait_for_load_state(...); logging.info(...)
                elif action == "sleep": seconds = float(value) if value is not None else 1.0; await asyncio.sleep(seconds); logging.info(...)
                elif action == "scroll_page_to_bottom": await root_page.evaluate(...); await asyncio.sleep(0.5); logging.info(...)
                results.append(...)
                continue

            # --- 要素操作のための準備 ---
            element: Optional[Locator] = None # 単一要素用
            target_locator: Optional[Locator] = None # 探索結果(単一または複数)
            # ★★★ target_scope: 要素が見つかった Page か Frame を保持 ★★★
            target_scope: Union[Page, Frame] = root_page # デフォルトは Page
            found_element = False

            single_element_required_actions = [...]
            multiple_elements_actions = [...]
            is_single_element_required = action in single_element_required_actions
            is_multiple_elements_action = action in multiple_elements_actions
            is_screenshot_element = action == "screenshot" and selector is not None

            # ★★★ 要素探索ロジック (find_in_all_frames_new を使用) ★★★
            if is_single_element_required or is_multiple_elements_action or is_screenshot_element:
                 if not selector: raise ValueError(...)
                 logging.info(f"要素 '{selector}' を探索開始 (現在のスコープ: {type(current_target).__name__})...")

                 # 1. まず現在のスコープで探す (Page or FrameLocator)
                 current_scope_for_search : Union[Page, Frame] # 実際に検索するスコープ
                 if isinstance(current_target, Page):
                      current_scope_for_search = current_target
                 elif isinstance(current_target, FrameLocator):
                      # FrameLocatorからFrameを取得 (Playwrightの内部動作に依存する可能性あり)
                      # より安全な方法は frame_locator(':root').content_frame() などだが、APIバージョンによるかも
                      try:
                           # FrameLocator に対応する Frame を取得しようと試みる
                           # 注意: この方法は Playwright のバージョンによって動作しない可能性がある
                           # frame_obj = await asyncio.wait_for(current_target._frame, timeout=1.0) # 非推奨
                           # 代替案: FrameLocator を使ってダミーの locator を取得し、そこから frame を辿る？ 難しい
                           # ここでは、FrameLocatorの場合、root_page から探索し直すことにする
                           logging.warning(f"現在のスコープが FrameLocator のため、探索起点を Page に戻します。")
                           current_scope_for_search = root_page
                      except Exception:
                           logging.error("FrameLocator から Frame の取得に失敗。Page スコープで探索します。")
                           current_scope_for_search = root_page
                 else:
                      # 予期しない型の場合 (エラーにするか、Pageに戻すか)
                      logging.error(f"予期しない current_target の型: {type(current_target)}。Page スコープで探索します。")
                      current_scope_for_search = root_page


                 try:
                     if is_single_element_required or is_screenshot_element:
                         wait_timeout = max(1, action_wait_time // 3)
                         # ★ current_scope_for_search で探索 ★
                         target_locator = current_scope_for_search.locator(selector).first
                         await target_locator.wait_for(state='attached', timeout=wait_timeout)
                     elif is_multiple_elements_action:
                         count = await current_scope_for_search.locator(selector).count()
                         if count > 0: target_locator = current_scope_for_search.locator(selector)
                         else: raise PlaywrightTimeoutError("要素が見つかりません (count=0)")
                     logging.info(f"  要素 '{selector}' を現在のスコープ({type(current_scope_for_search).__name__})で発見。")
                     target_scope = current_scope_for_search # ★ target_scope を設定
                     found_element = True
                 except PlaywrightTimeoutError:
                     logging.warning("  現在のスコープ直下では見つからず。ページ全体のiframeを探索します...")
                     # ★★★ find_in_all_frames_new を呼び出す (探索起点は root_page) ★★★
                     found_scope, target_locator = await find_in_all_frames_new(
                         root_page, selector, is_multiple=is_multiple_elements_action
                     )
                     if target_locator:
                         found_element = True
                         target_scope = found_scope if found_scope else root_page # 見つかったスコープを設定
                         logging.info(f"  要素 '{selector}' をスコープ {type(target_scope).__name__} で発見（自動探索）。")
                     else:
                         error_msg = f"要素 '{selector}' が現在のスコープおよびページ内のどのiframeにも見つかりませんでした。"
                         logging.error(error_msg)
                         results.append({"step": step_num, "status": "error", "action": action, "selector": selector, "message": error_msg})
                         return False, results

                 # 単一要素が必須のアクションのために element 変数にも保持
                 if is_single_element_required or is_screenshot_element:
                      if target_locator:
                          if is_multiple_elements_action: element = target_locator.first
                          else: element = target_locator
                      if not element: raise PlaywrightError(...)

                 logging.info(f"最終的な要素発見/操作スコープ: {type(target_scope).__name__}")
                 # --- ▲▲▲ 要素探索ロジックここまで ▲▲▲ ---


            # --- 各アクション実行 (element または target_scope/target_locator を使用) ---
            action_result_details = {"selector": selector}

            # --- ▼▼▼ アクションの実行主体を target_scope または element に変更 ▼▼▼ ---
            if action == "click":
                 if not element: raise ValueError("Click action requires an element.")
                 logging.info("要素をクリックします...")
                 await element.wait_for(state='visible', timeout=action_wait_time)
                 context = root_page.context
                 try:
                     async with context.expect_page(timeout=NEW_PAGE_EVENT_TIMEOUT) as new_page_info:
                         await element.click(timeout=action_wait_time)
                     new_page = await new_page_info.value
                     new_page_url = new_page.url
                     logging.info(f"新しいページが開きました: URL={new_page_url}")
                     try: await new_page.wait_for_load_state("load", timeout=action_wait_time)
                     except PlaywrightTimeoutError: logging.warning(...)
                     root_page = new_page
                     current_target = new_page # ★ current_target 更新
                     iframe_stack.clear()
                     target_scope = current_target # target_scope も更新
                     action_result_details.update(...)
                 except PlaywrightTimeoutError:
                     logging.info(...)
                     action_result_details["new_page_opened"] = False
                 results.append(...)

            elif action == "input":
                 if not element: raise ValueError(...)
                 if value is None: raise ValueError(...)
                 logging.info(...)
                 await element.wait_for(state='visible', timeout=action_wait_time)
                 await element.fill(str(value), timeout=action_wait_time)
                 logging.info("入力成功。")
                 action_result_details["value"] = value
                 results.append(...)

            # ... (他の単一要素アクションも element を使うので変更なし) ...
            elif action == "hover": # ... (element を使用) ...
                 if not element: raise ValueError(...) ; await element.wait_for(...); await element.hover(...); logging.info("..."); results.append(...)
            elif action == "get_inner_text": # ... (element を使用) ...
                 if not element: raise ValueError(...) ; await element.wait_for(...); text = await element.inner_text(...); logging.info(...); action_result_details["text"] = text; results.append(...)
            elif action == "get_text_content": # ... (element を使用) ...
                 if not element: raise ValueError(...) ; await element.wait_for(...); text = await element.text_content(...); logging.info(...); action_result_details["text"] = text; results.append(...)
            elif action == "get_inner_html": # ... (element を使用) ...
                 if not element: raise ValueError(...) ; await element.wait_for(...); html_content = await element.inner_html(...); logging.info(...); action_result_details["html"] = html_content; results.append(...)
            elif action == "get_attribute": # ... (element を使用) ...
                #if not element: raise ValueError(...) if not attribute_name: raise ValueError(...)
                #logging.info(...); await element.wait_for(...); attr_value = await element.get_attribute(...); pdf_text_content = None
                #if attribute_name.lower() == 'href' and attr_value is not None: # ... (PDF処理) ...
                #logging.info(...); action_result_details.update(...);
                #if pdf_text_content is not None: action_result_details["pdf_text"] = pdf_text_content;
                #results.append(...)


                # element の存在チェック
                if not element:
                    raise ValueError("Get attribute action requires an element.")
                # attribute_name の存在チェック (改行してインデント)
                if not attribute_name:
                    raise ValueError("Action 'get_attribute' requires 'attribute_name'.")

                logging.info(f"要素の属性 '{attribute_name}' を取得...")
                # 要素の状態を待機
                await element.wait_for(state='attached', timeout=action_wait_time)
                # 属性値を取得
                attr_value = await element.get_attribute(attribute_name, timeout=action_wait_time)
                pdf_text_content = None

                # href属性の場合、URL処理とPDF処理を行う
                if attribute_name.lower() == 'href' and attr_value is not None:
                    original_url = attr_value
                    absolute_url = urljoin(current_base_url, attr_value)
                    if original_url != absolute_url:
                        logging.info(f"  -> 絶対URLに変換: '{absolute_url}'")
                    attr_value = absolute_url # 取得した値を絶対URLで上書き

                    # PDFかどうかをチェック
                    if absolute_url.lower().endswith('.pdf'):
                        logging.info(f"  リンク先がPDF。ダウンロードとテキスト抽出を試みます...")
                        pdf_bytes = await download_pdf_async(api_request_context, absolute_url)
                        if pdf_bytes:
                            # 同期関数を別スレッドで実行
                            pdf_text_content = await asyncio.to_thread(extract_text_from_pdf_sync, pdf_bytes)
                            logging.info(f"  PDFテキスト抽出完了 (先頭抜粋): {pdf_text_content[:200] if pdf_text_content else 'None'}...")
                        else:
                            pdf_text_content = "Error: PDF download failed or returned no data."
                            logging.error(f"  PDFダウンロード失敗: {absolute_url}")

                # 最終的な結果をログに出力
                logging.info(f"取得属性値 ({attribute_name}): '{attr_value}'")
                # 結果辞書を更新
                action_result_details.update({"attribute": attribute_name, "value": attr_value})
                # PDFテキストがあれば追加
                if pdf_text_content is not None:
                    action_result_details["pdf_text"] = pdf_text_content
                # ステップ結果をリストに追加
                results.append({"step": step_num, "status": "success", "action": action, **action_result_details})




            elif action == "get_all_attributes":
                # selector と attribute_name の存在チェック
                if not selector:
                    raise ValueError("Action 'get_all_attributes' requires 'selector'.")
                if not attribute_name:
                    raise ValueError("Action 'get_all_attributes' requires 'attribute_name'.")

                # ★★★ target_locator を使う ★★★
                logging.info(f"セレクター '{selector}' に一致する全要素から属性 '{attribute_name}' を取得 (探索スコープ: {type(target_scope).__name__})...")
                original_attribute_list: List[Optional[str]] = []

                # target_locator が None でない場合のみ要素取得を試みる
                if target_locator:
                    try:
                        # 複数要素用 Locator からすべての Locator を取得
                        all_locators = await target_locator.all()
                        if not all_locators:
                            logging.warning(f"  要素 '{selector}' が見つかりませんでした (all() returned empty list)。")
                        else:
                            logging.info(f"  {len(all_locators)} 個の要素が見つかりました。")
                            get_attr_tasks = []
                            # 各要素から非同期で属性値を取得するタスクを作成
                            for loc_index, loc in enumerate(all_locators):
                                # --- get_single_attr ローカル関数定義 ---
                                async def get_single_attr(locator: Locator, attr_name: str, index: int) -> Optional[str]:
                                    try:
                                        # タイムアウト値を計算 (最低500ms)
                                        wait_timeout = max(1, action_wait_time // 2) if action_wait_time > 0 else 500
                                        # 要素がDOMに存在するか短時間待機
                                        await locator.wait_for(state='attached', timeout=wait_timeout)
                                        # 属性値を取得
                                        return await locator.get_attribute(attr_name, timeout=wait_timeout)
                                    except PlaywrightTimeoutError:
                                        logging.warning(f"  要素 {index+1} の属性 '{attr_name}' 取得中にタイムアウト。")
                                        return None
                                    except Exception as e_inner:
                                        logging.warning(f"  要素 {index+1} の属性 '{attr_name}' 取得中にエラー: {type(e_inner).__name__} - {e_inner}")
                                        return None
                                # --- ローカル関数定義ここまで ---
                                get_attr_tasks.append(get_single_attr(loc, attribute_name, loc_index))

                            # すべての属性取得タスクを並行実行
                            original_attribute_list = await asyncio.gather(*get_attr_tasks)

                    except Exception as e:
                        # all() や gather 中のエラー
                        logging.error(f"全要素の Locator 取得または属性取得中にエラー: {e}", exc_info=True)
                        original_attribute_list = [] # エラー時は空リスト
                else: # target_locator が None (要素が見つからなかった) 場合
                    logging.warning(f"  要素 '{selector}' が見つからなかったため、属性取得をスキップします。")

                # --- 取得した属性値リストの後処理 (URL変換とPDF処理) ---
                final_list_to_store: List[Optional[str]] = []
                pdf_texts_list: List[Optional[str]] = []
                result_key_name = "attribute_list" # 結果のキー名

                if attribute_name.lower() == 'href':
                    result_key_name = "url_lists"
                    logging.info("  取得した href を絶対URLに変換し、PDFを処理します...")
                
                    absolute_url_list_temp = []
                    # URL変換ループ
                    for original_url in original_attribute_list:
                        if original_url is not None:
                            abs_url = urljoin(current_base_url, original_url)
                            absolute_url_list_temp.append(abs_url)
                            if original_url != abs_url:
                                logging.debug(f"    '{original_url}' -> '{abs_url}'")
                            # else:
                            #     logging.debug(f"    '{abs_url}' (変更なし)")
                        else:
                            absolute_url_list_temp.append(None)
                            # logging.debug(f"    (URLがNoneのためスキップ)")
                    final_list_to_store = absolute_url_list_temp

                    # PDFダウンロードタスク作成ループ
                    pdf_download_tasks = []
                    for abs_url in final_list_to_store:
                        if abs_url and abs_url.lower().endswith('.pdf'):
                            pdf_download_tasks.append(download_pdf_async(api_request_context, abs_url))
                        else:
                            # PDF以外またはURLがNoneの場合はNoneを返すFutureを作成
                            future: asyncio.Future[Optional[bytes]] = asyncio.Future()
                            future.set_result(None)
                            pdf_download_tasks.append(future)

                    # PDFダウンロード実行
                    logging.info(f"  {len([t for t in pdf_download_tasks if isinstance(t, asyncio.Task) or (isinstance(t, asyncio.Future) and t.result() is not None)])} 個のPDFダウンロード/スキップ処理を開始...")
                    pdf_byte_results = await asyncio.gather(*pdf_download_tasks)
                    logging.info("  PDFダウンロード処理完了。")

                    # PDFテキスト抽出タスク作成ループ
                    pdf_extract_tasks_with_placeholders = []
                    successful_downloads = 0
                    for pdf_bytes in pdf_byte_results:
                        if pdf_bytes: # ダウンロード成功
                            successful_downloads += 1
                            pdf_extract_tasks_with_placeholders.append(
                                asyncio.to_thread(extract_text_from_pdf_sync, pdf_bytes)
                            )
                        else: # ダウンロード失敗 or 非PDF or None URL
                            pdf_extract_tasks_with_placeholders.append(None)

                    # 実際に実行する抽出タスクのみを抽出
                    actual_extract_tasks = [
                        task for task in pdf_extract_tasks_with_placeholders if task is not None
                    ]

                    # PDFテキスト抽出実行
                    logging.info(f"  {len(actual_extract_tasks)} 個のPDFテキスト抽出を開始...")
                    pdf_texts_extracted = await asyncio.gather(*actual_extract_tasks)
                    logging.info("  PDFテキスト抽出処理完了。")

                    # 結果を元のリスト構造に戻す
                    pdf_texts_list = []
                    extracted_iter = iter(pdf_texts_extracted)
                    for task_placeholder in pdf_extract_tasks_with_placeholders:
                        if task_placeholder is None:
                            pdf_texts_list.append(None)
                        else:
                            try:
                                pdf_texts_list.append(next(extracted_iter))
                            except StopIteration:
                                logging.error("PDFテキスト抽出結果の数が一致しません。")
                                pdf_texts_list.append(None)

                    logging.info(f"  絶対URLリスト ({len(final_list_to_store)}件) 取得完了。")

                else: # href 以外の場合
                    final_list_to_store = original_attribute_list
                    pdf_texts_list = [None] * len(final_list_to_store) # PDFテキストは常にNone
                    logging.info(f"  取得した属性値リスト ({len(final_list_to_store)}件) 取得完了。")

                    # --- 結果をresultsリストに追加 ---
                    # action_result_details に取得した情報を追加
                    action_result_details.update({
                        "attribute": attribute_name,
                        result_key_name: final_list_to_store
                    })
                    # PDFテキストが存在すれば追加
                    if any(t is not None for t in pdf_texts_list):
                        action_result_details["pdf_texts"] = pdf_texts_list
                    # ステップ結果を追加
                results.append({
                    "step": step_num,
                    "status": "success",
                    "action": action,
                    **action_result_details
                })

            elif action == "get_all_text_contents":
                 if not selector: raise ValueError(...)
                 # ★★★ target_locator を使う ★★★
                 logging.info(f"セレクター '{selector}' に一致する全要素から textContent を取得 (探索スコープ: {type(target_scope).__name__})...")
                 text_list: List[Optional[str]] = []
                 if target_locator:
                     try:
                         all_locators = await target_locator.all()
                         if not all_locators: logging.warning(...)
                         else:
                             logging.info(...)
                             get_text_tasks = []
                             for loc_index, loc in enumerate(all_locators): get_text_tasks.append(get_single_text(loc, loc_index))
                             text_list = await asyncio.gather(*get_text_tasks)
                     except Exception as e: logging.error(...); text_list = []
                 else: logging.warning(...)
                 action_result_details["text_list"] = text_list
                 logging.info(...)
                 results.append(...)

            elif action == "wait_visible":
                 # element の存在チェック
                 if not element:
                     raise ValueError("Wait visible action requires an element.")
                 logging.info("要素が表示されるのを待ちます...")
                 # 要素が表示状態になるまで待機
                 await element.wait_for(state='visible', timeout=action_wait_time)
                 logging.info("要素表示確認。")
                 # 成功結果を追加
                 results.append({
                     "step": step_num,
                     "status": "success",
                     "action": action,
                     **action_result_details # selector を含める
                 })

            elif action == "select_option":
                  # element の存在チェック
                  if not element:
                      raise ValueError("Select option action requires an element.")
                  # option_type と option_value の検証
                  if option_type not in ['value', 'index', 'label'] or option_value is None:
                      raise ValueError("Invalid 'option_type' or 'option_value' for select_option.")

                  logging.info(f"ドロップダウン選択 (Type: {option_type}, Value: '{option_value}')...")
                  # 要素が表示されるのを待つ
                  await element.wait_for(state='visible', timeout=action_wait_time)
                  # タイプに応じてオプションを選択
                  if option_type == 'value':
                      await element.select_option(value=str(option_value), timeout=action_wait_time)
                  elif option_type == 'index':
                      # option_value が数値であることを確認 (より厳密に)
                      try:
                          index_val = int(option_value)
                          await element.select_option(index=index_val, timeout=action_wait_time)
                      except (ValueError, TypeError) as e:
                          raise ValueError(f"Invalid index value for select_option: {option_value}") from e
                  elif option_type == 'label':
                      await element.select_option(label=str(option_value), timeout=action_wait_time)
                  logging.info("選択成功。")
                  # 結果に選択情報を追加
                  action_result_details.update({"option_type": option_type, "option_value": option_value})
                  results.append({
                      "step": step_num,
                      "status": "success",
                      "action": action,
                      **action_result_details
                  })

            elif action == "scroll_to_element":
                   # element の存在チェック
                   if not element:
                       raise ValueError("Scroll action requires an element.")
                   logging.info("要素までスクロール...")
                   # 要素がDOMにアタッチされるのを待つ
                   await element.wait_for(state='attached', timeout=action_wait_time)
                   # 要素が表示されるようにスクロール
                   await element.scroll_into_view_if_needed(timeout=action_wait_time)
                   logging.info("スクロール成功。")
                   # 成功結果を追加
                   results.append({
                       "step": step_num,
                       "status": "success",
                       "action": action,
                       **action_result_details # selector を含める
                   })

            elif action == "screenshot":
                 # ファイル名を決定（指定がなければデフォルト名）
                 filename_input = str(value) if value else f"screenshot_step{step_num}.png"
                 # ファイル名から不正な文字を除去し、空の場合のフォールバック
                 filename = "".join(c for c in filename_input if c.isalnum() or c in ('_', '.', '-')).strip()
                 if not filename:
                     filename = f"screenshot_step{step_num}.png"

                 # 保存パスを生成
                 screenshot_path = os.path.join(DEFAULT_SCREENSHOT_DIR, filename)
                 logging.info(f"スクリーンショット保存: '{screenshot_path}'...")

                 # ★ element があるかどうかで分岐 ★
                 if element: # 要素のスクリーンショット
                      # 要素が表示されるのを待つ
                      await element.wait_for(state='visible', timeout=action_wait_time)
                      # 要素のスクリーンショットを撮る
                      await element.screenshot(path=screenshot_path, timeout=action_wait_time)
                      logging.info("要素のスクショ保存成功。")
                 else: # ページ全体のスクリーンショット
                      # ページ全体のスクリーンショットを撮る
                      await root_page.screenshot(path=screenshot_path, full_page=True, timeout=action_wait_time)
                      logging.info("ページ全体のスクショ保存成功。")

                 # 結果にファイルパスを追加
                 action_result_details["filename"] = screenshot_path
                 results.append({
                     "step": step_num,
                     "status": "success",
                     "action": action,
                     **action_result_details # selector (あれば) と filename を含める
                 })
                 
            else: # 未定義アクション
                 known_actions = [...];
                 if action not in known_actions: logging.warning(...); results.append(...)

        except (PlaywrightTimeoutError, PlaywrightError, ValueError, Exception) as e:
            # ... (エラーハンドリングは変更なし) ...
            error_message = f"ステップ {step_num} ({action}) エラー: {type(e).__name__} - {e}"; logging.error(...); screenshot_path = None
            try: os.makedirs(...); # ... (スクショ保存試行) ...
            except Exception as dir_e: logging.error(...)
            error_entry: Dict[str, Any] = {...}; # ... (エラー情報作成) ...
            results.append(error_entry)
            return False, results

    return True, results

# --- Playwright 実行メイン関数 (変更なし) ---
async def run_playwright_automation_async(target_url: str, actions: List[dict],
                                         headless_mode: bool = False,
                                         slow_motion: int = 0) -> Tuple[bool, List[dict]]:
    # ... (関数全体は変更なし) ...
    logging.info("--- Playwright 自動化開始 (非同期) ---")
    # ... (try-except-finally 構造は同じ) ...
    return all_success, final_results

# --- 新しいエントリーポイント関数 (変更なし) ---
async def execute_web_runner_task(input_data: Dict[str, Any], headless_mode: bool = False, slow_motion: int = 0) -> Tuple[bool, List[dict]]:
    # ... (関数全体は変更なし) ...
    logging.debug("--- execute_web_runner_task started ---"); # ... (入力検証) ...
    logging.info(...)
    try:
        logging.debug("Calling run_playwright_automation_async...")
        success, results = await run_playwright_automation_async(...)
        logging.info(...); logging.debug(...); logging.debug(...); return success, results
    except NotImplementedError as nie: error_message = ...; logging.error(...); return False, [...]
    except Exception as e: error_message = ...; logging.error(...); return False, [...]

# --- スクリプト直接実行部分は削除済み ---