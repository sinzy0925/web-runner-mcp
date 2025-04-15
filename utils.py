# --- ファイル: utils.py (結果追記機能追加版) ---
import json
import logging
import os
import sys
import asyncio
import time
import traceback
import fitz  # PyMuPDF
from playwright.async_api import APIRequestContext, TimeoutError as PlaywrightTimeoutError
# <<< typing に Optional, Dict, Any, List, Union を追加 >>>
from typing import Optional, Dict, Any, List, Union
from urllib.parse import urljoin

import config

logger = logging.getLogger(__name__)

# --- setup_logging_for_standalone, load_input_from_json, ---
# --- extract_text_from_pdf_sync, download_pdf_async は変更なし ---
# (コードは省略)
def setup_logging_for_standalone(log_file_path: str = config.LOG_FILE):
    """Web-Runner単体実行用のロギング設定を行います。"""
    log_level = logging.INFO # デフォルトレベル
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    handlers = [console_handler]
    log_target = "Console"
    file_handler = None
    try:
        log_dir = os.path.dirname(log_file_path)
        if log_dir:
            if not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    print(f"DEBUG [utils]: Created directory '{log_dir}'")
                except Exception as e:
                    print(f"警告 [utils]: ログディレクトリ '{log_dir}' の作成に失敗しました: {e}", file=sys.stderr)
                    raise
            else:
                print(f"DEBUG [utils]: Directory '{log_dir}' already exists.")
        try:
            with open(log_file_path, 'a') as f:
                print(f"DEBUG [utils]: Successfully opened (or created) '{log_file_path}' for appending.")
        except Exception as e:
             print(f"警告 [utils]: ログファイル '{log_file_path}' を開けません（権限確認）: {e}", file=sys.stderr)
             raise
        file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a')
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
        log_target += f" and File ('{log_file_path}')"
        print(f"DEBUG [utils]: FileHandler created for '{log_file_path}'")
    except Exception as e:
        print(f"警告 [utils]: ログファイル '{log_file_path}' のハンドラ設定に失敗しました: {e}", file=sys.stderr)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True
    )
    logging.getLogger('playwright').setLevel(logging.WARNING)
    current_logger = logging.getLogger(__name__)
    current_logger.info(f"Standalone logger setup complete. Level: {logging.getLevelName(log_level)}. Target: {log_target}")
    print(f"DEBUG [utils]: Logging setup finished. Root handlers: {logging.getLogger().handlers}")

def load_input_from_json(filepath: str) -> Dict[str, Any]:
    """指定されたJSONファイルから入力データを読み込む。"""
    logger.info(f"入力ファイル '{filepath}' の読み込みを開始します...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "target_url" not in data or not data["target_url"]:
            raise ValueError("JSONファイルに必須キー 'target_url' が存在しないか、値が空です。")
        if "actions" not in data or not isinstance(data["actions"], list):
            raise ValueError("JSONファイルに必須キー 'actions' が存在しないか、リスト形式ではありません。")
        if not data["actions"]:
             logger.warning(f"入力ファイル '{filepath}' の 'actions' リストが空です。")
        logger.info(f"入力ファイル '{filepath}' を正常に読み込みました。")
        return data
    except FileNotFoundError:
        logger.error(f"入力ファイルが見つかりません: {filepath}")
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSON形式のエラーです ({filepath}): {e}")
        raise
    except ValueError as ve:
        logger.error(f"入力データの形式が不正です ({filepath}): {ve}")
        raise
    except Exception as e:
        logger.error(f"入力ファイルの読み込み中に予期せぬエラーが発生しました ({filepath}): {e}", exc_info=True)
        raise

def extract_text_from_pdf_sync(pdf_data: bytes) -> Optional[str]:
    """PDFのバイトデータからテキストを抽出する (同期的)。エラー時はエラーメッセージ文字列を返す。"""
    doc = None
    try:
        logger.info(f"PDFデータ (サイズ: {len(pdf_data)} bytes) からテキスト抽出を開始します...")
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        text_parts = []
        logger.info(f"PDFページ数: {len(doc)}")
        for page_num in range(len(doc)):
            page_start_time = time.monotonic()
            try:
                page = doc.load_page(page_num)
                page_text = page.get_text("text", sort=True)
                if page_text:
                    text_parts.append(page_text.strip())
                page_elapsed = (time.monotonic() - page_start_time) * 1000
                logger.debug(f"ページ {page_num + 1} 処理完了 ({page_elapsed:.0f}ms)。")
            except Exception as page_e:
                logger.warning(f"ページ {page_num + 1} の処理中にエラー: {page_e}")
                text_parts.append(f"--- Error processing page {page_num + 1}: {page_e} ---")
        full_text = "\n--- Page Separator ---\n".join(text_parts)
        cleaned_text = '\n'.join([line.strip() for line in full_text.splitlines() if line.strip()])
        logger.info(f"PDFテキスト抽出完了。総文字数 (整形後): {len(cleaned_text)}")
        return cleaned_text if cleaned_text else "(No text extracted from PDF)"
    except fitz.fitz.TryingToReadFromEmptyFileError:
         logger.error("PDF処理エラー: ファイルデータが空または破損しています。")
         return "Error: PDF data is empty or corrupted."
    except fitz.fitz.FileDataError as e:
         logger.error(f"PDF処理エラー (PyMuPDF FileDataError): {e}", exc_info=False)
         return f"Error: PDF file data error - {e}"
    except RuntimeError as e:
        logger.error(f"PDF処理エラー (PyMuPDF RuntimeError): {e}", exc_info=True)
        return f"Error: PDF processing failed (PyMuPDF RuntimeError) - {e}"
    except Exception as e:
        logger.error(f"PDFテキスト抽出中に予期せぬエラーが発生しました: {e}", exc_info=True)
        return f"Error: Unexpected error during PDF text extraction - {e}"
    finally:
        if doc:
            try: doc.close(); logger.debug("PDFドキュメントを閉じました。")
            except Exception as close_e: logger.warning(f"PDFドキュメントのクローズ中にエラーが発生しました (無視): {close_e}")

async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    """指定されたURLからPDFを非同期でダウンロードし、バイトデータを返す。失敗時はNoneを返す。"""
    logger.info(f"PDFを非同期でダウンロード中: {url} (Timeout: {config.PDF_DOWNLOAD_TIMEOUT}ms)")
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        response = await api_request_context.get(url, headers=headers, timeout=config.PDF_DOWNLOAD_TIMEOUT, fail_on_status_code=False)
        if not response.ok:
            logger.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}")
            try:
                error_body = await response.text(timeout=5000)
                logger.debug(f"エラーレスポンスボディ (一部): {error_body[:500]}")
            except Exception as body_err: logger.warning(f"エラーレスポンスボディの読み取り中にエラーが発生しました: {body_err}")
            return None
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type:
            logger.warning(f"レスポンスのContent-TypeがPDFではありません ({url}): '{content_type}'。ダウンロードは続行しますが、後続処理で失敗する可能性があります。")
        body = await response.body()
        if not body:
             logger.warning(f"PDFダウンロード成功 ({url}) Status: {response.status} ですが、レスポンスボディが空です。")
             return None
        logger.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes")
        return body
    except PlaywrightTimeoutError:
        logger.error(f"PDFダウンロード中にタイムアウトが発生しました ({url})。設定タイムアウト: {config.PDF_DOWNLOAD_TIMEOUT}ms")
        return None
    except Exception as e:
        logger.error(f"PDF非同期ダウンロード中に予期せぬエラーが発生しました ({url}): {e}", exc_info=True)
        return None

# --- ▼▼▼ write_results_to_file 修正 ▼▼▼ ---
def write_results_to_file(
    results: List[Dict[str, Any]],
    filepath: str,
    final_summary_data: Optional[Dict[str, Any]] = None # <<< 集約結果を受け取る引数を追加
):
    """
    実行結果を指定されたファイルに書き込む。
    オプションで最終的な集約結果も追記する。
    """
    logger.info(f"実行結果を '{filepath}' に書き込みます...")
    try:
        output_dir = os.path.dirname(filepath)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"出力ディレクトリ '{output_dir}' を作成しました。")

        with open(filepath, "w", encoding="utf-8") as file:
            file.write("--- Web Runner 実行結果 ---\n\n")
            # --- 各ステップ結果の書き込み (ここは変更なし) ---
            if not results:
                 file.write("(実行ステップ結果がありません)\n")
            for i, res in enumerate(results):
                 # ステップ結果が辞書でない場合のエラーハンドリングを追加
                if not isinstance(res, dict):
                    file.write(f"--- Step {i+1}: Invalid Format ---\n")
                    file.write(f"Received non-dict data: {res}\n\n")
                    continue

                step_num = res.get('step', i + 1)
                action_type = res.get('action', 'Unknown')
                status = res.get('status', 'Unknown')
                selector = res.get('selector')

                file.write(f"--- Step {step_num}: {action_type} ({status}) ---\n")
                if res.get('memo'): file.write(f"Memo: {res.get('memo')}\n") # メモも表示
                if selector: file.write(f"Selector: {selector}\n")
                if res.get('iframe_selector'): file.write(f"IFrame Selector (for switch): {res.get('iframe_selector')}\n")
                if res.get('required_state'): file.write(f"Required Element State: {res.get('required_state')}\n")

                if status == "error":
                     file.write(f"Message: {res.get('message')}\n")
                     if res.get('full_error') and res.get('full_error') != res.get('message'): file.write(f"Details: {res.get('full_error')}\n")
                     if res.get('error_screenshot'): file.write(f"Screenshot: {res.get('error_screenshot')}\n")
                     if res.get('traceback'): file.write(f"Traceback:\n{res.get('traceback')}\n")
                elif status == "success":
                    details_to_write = res.copy()
                    for key in ['step', 'status', 'action', 'selector', 'iframe_selector', 'required_state', 'memo']: details_to_write.pop(key, None)
                    # --- アクションタイプに応じた整形出力 (ここは変更なし) ---
                    if action_type == 'get_all_attributes':
                        attr_name = details_to_write.pop('attribute', 'N/A')
                        results_count = details_to_write.pop('results_count', 0)
                        url_list = details_to_write.pop('url_list', None)
                        pdf_texts = details_to_write.pop('pdf_texts', None)
                        scraped_texts = details_to_write.pop('scraped_texts', None)
                        email_list = details_to_write.pop('extracted_emails', None)
                        attr_list = details_to_write.pop('attribute_list', None)
                        file.write(f"Requested Attribute/Content: {attr_name}\n")
                        file.write(f"Results ({results_count} items processed):\n")
                        if results_count > 0:
                            if email_list is not None:
                                file.write("  Extracted Emails (Unique Domains for this step):\n")
                                if email_list: file.write('\n'.join(f"  - {email}" for email in email_list) + "\n")
                                else: file.write("    (No unique domain emails found for this step)\n")
                            if url_list is not None and attr_name.lower() != 'mail':
                                file.write(f"  Processed URLs ({len(url_list)}):\n")
                                for idx, url in enumerate(url_list): file.write(f"    [{idx+1}] {url if url else '(URL not processed or invalid)'}\n")
                            if pdf_texts is not None:
                                file.write("  Extracted PDF Texts:\n")
                                for idx, pdf_content in enumerate(pdf_texts):
                                    if pdf_content is not None:
                                        file.write(f"    [{idx+1}]")
                                        if isinstance(pdf_content, str) and pdf_content.startswith("Error:"): file.write(f" (Error): {pdf_content}\n")
                                        elif pdf_content == "(No text extracted from PDF)": file.write(": (No text extracted)\n")
                                        else:
                                            file.write(f" (Length: {len(pdf_content or '')}):\n")
                                            indented_content = "\n".join(["      " + line for line in str(pdf_content).splitlines()])
                                            file.write(indented_content + "\n")
                            if scraped_texts is not None:
                                file.write("  Scraped Page Texts:\n")
                                for idx, scraped_content in enumerate(scraped_texts):
                                    if scraped_content is not None:
                                        file.write(f"    [{idx+1}]")
                                        if isinstance(scraped_content, str) and scraped_content.startswith("Error"): file.write(f" (Error): {scraped_content}\n")
                                        else:
                                            file.write(f" (Length: {len(scraped_content or '')}):\n")
                                            indented_content = "\n".join(["      " + line for line in str(scraped_content).splitlines()])
                                            file.write(indented_content + "\n")
                            if attr_list is not None:
                                file.write(f"  Attribute '{attr_name}' Values:\n")
                                for idx, attr_content in enumerate(attr_list): file.write(f"    [{idx+1}] {attr_content}\n")
                        else: file.write("  (No items found matching the selector for this step)\n")
                    elif action_type == 'get_all_text_contents':
                        text_list_result = details_to_write.pop('text_list', [])
                        results_count = details_to_write.pop('results_count', len(text_list_result) if isinstance(text_list_result, list) else 0)
                        file.write(f"Result Text List ({results_count} items):\n")
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text) for text in text_list_result if text is not None]
                            if valid_texts: file.write('\n'.join(f"- {text}" for text in valid_texts) + "\n")
                            else: file.write("(No text content found)\n")
                        else: file.write("(Invalid format received)\n")
                    elif action_type in ['get_text_content', 'get_inner_text'] and 'text' in details_to_write: file.write(f"Result Text:\n{details_to_write.pop('text', '')}\n")
                    elif action_type == 'get_inner_html' and 'html' in details_to_write: file.write(f"Result HTML:\n{details_to_write.pop('html', '')}\n")
                    elif action_type == 'get_attribute':
                        attr_name = details_to_write.pop('attribute', ''); attr_value = details_to_write.pop('value', None)
                        file.write(f"Result Attribute ('{attr_name}'): {attr_value}\n")
                        if 'pdf_text' in details_to_write:
                            pdf_text = details_to_write.pop('pdf_text', '')
                            prefix = "Extracted PDF Text"
                            if isinstance(pdf_text, str) and pdf_text.startswith("Error:"): file.write(f"{prefix} (Error): {pdf_text}\n")
                            elif pdf_text == "(No text extracted from PDF)": file.write(f"{prefix}: (No text extracted)\n")
                            else: file.write(f"{prefix}:\n{pdf_text}\n")
                    elif action_type == 'screenshot' and 'filename' in details_to_write: file.write(f"Screenshot saved to: {details_to_write.pop('filename')}\n")
                    elif action_type == 'click' and 'new_page_opened' in details_to_write:
                        if details_to_write.get('new_page_opened'): file.write(f"New page opened: {details_to_write.get('new_page_url')}\n")
                        else: file.write("New page did not open within timeout.\n")
                        details_to_write.pop('new_page_opened', None); details_to_write.pop('new_page_url', None)
                    if details_to_write:
                        file.write("Other Details:\n")
                        for key, val in details_to_write.items(): file.write(f"  {key}: {val}\n")
                elif status == "skipped" or status == "warning": file.write(f"Message: {res.get('message', 'No message provided.')}\n")
                else: file.write(f"Raw Data: {res}\n") # 不明な場合は生データを書き出す
                file.write("\n")

            # --- ▼▼▼ 最終集約結果の追記処理 ▼▼▼ ---
            if final_summary_data:
                file.write("--- Final Aggregated Summary ---\n\n")
                for summary_key, summary_value in final_summary_data.items():
                    file.write(f"{summary_key}:\n")
                    if isinstance(summary_value, list):
                        # リスト（配列）形式で出力
                        # JSON形式でインデント付きで書き込む
                        try:
                             json_output = json.dumps(summary_value, indent=2, ensure_ascii=False)
                             file.write(json_output + "\n")
                        except TypeError:
                             # JSONシリアライズできない場合は repr で出力
                             file.write(repr(summary_value) + "\n")
                    else:
                        # リスト以外はそのまま出力
                        file.write(f"{summary_value}\n")
                file.write("\n")
            # --- ▲▲▲ 最終集約結果の追記処理 ▲▲▲ ---

        logger.info(f"結果の書き込みが完了しました: '{filepath}'")
    except IOError as e:
        logger.error(f"結果ファイル '{filepath}' の書き込み中にIOエラーが発生しました: {e}")
    except Exception as e:
        logger.error(f"結果の処理またはファイル書き込み中に予期せぬエラーが発生しました: {e}", exc_info=True)

# --- utils.py のテスト用コード (変更なし) ---
# (コードは省略)
if __name__ == "__main__":
    print("--- Testing logging setup from utils.py ---")
    TEST_LOG_FILE = config.MCP_SERVER_LOG_FILE
    print(f"Test log file path: {TEST_LOG_FILE}")
    if os.path.exists(TEST_LOG_FILE):
        try: os.remove(TEST_LOG_FILE); print(f"Removed existing test log file: {TEST_LOG_FILE}")
        except Exception as e: print(f"Could not remove existing test log file: {e}")
    try:
        setup_logging_for_standalone(log_file_path=TEST_LOG_FILE)
        test_logger = logging.getLogger("utils_test")
        print("\nAttempting to log messages...")
        test_logger.info("INFO message from utils_test.")
        test_logger.warning("WARNING message from utils_test.")
        test_logger.error("ERROR message from utils_test.")
        print(f"\nLogging test complete.")
        print(f"Please check the console output above and the content of the file: {os.path.abspath(TEST_LOG_FILE)}")
        print(f"Root logger handlers: {logging.getLogger().handlers}")
    except Exception as e: print(f"\n--- Error during logging test ---"); print(f"{type(e).__name__}: {e}"); traceback.print_exc()