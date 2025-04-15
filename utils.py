# --- ファイル: utils.py (修正・テストコード追加版) ---
import json
import logging
import os # ★★★ os モジュールをインポート ★★★
import sys
import asyncio
import time
import traceback # ★★★ traceback をインポート ★★★
import fitz  # PyMuPDF
from playwright.async_api import APIRequestContext, TimeoutError as PlaywrightTimeoutError
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

import config

logger = logging.getLogger(__name__)

def setup_logging_for_standalone(log_file_path: str = config.LOG_FILE):
    """Web-Runner単体実行用のロギング設定を行います。"""
    log_level = logging.INFO # デフォルトレベル

    # ルートロガーを取得
    root_logger = logging.getLogger()
    # 既存のハンドラをすべて削除
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # フォーマッタ
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # コンソールハンドラ
    console_handler = logging.StreamHandler(sys.stdout) # 標準出力へ
    console_handler.setFormatter(formatter)
    handlers = [console_handler]
    log_target = "Console"

    # ファイルハンドラ
    file_handler = None # ★★★ 初期化 ★★★
    try:
        log_dir = os.path.dirname(log_file_path)
        if log_dir: # ディレクトリパスが空でない場合のみ処理
            if not os.path.exists(log_dir):
                try:
                    os.makedirs(log_dir, exist_ok=True)
                    print(f"DEBUG [utils]: Created directory '{log_dir}'") # デバッグ出力
                except Exception as e:
                    print(f"警告 [utils]: ログディレクトリ '{log_dir}' の作成に失敗しました: {e}", file=sys.stderr)
                    # ディレクトリ作成失敗時はファイルログを諦める
                    raise # エラーを再送出して try ブロックを抜ける
            else:
                print(f"DEBUG [utils]: Directory '{log_dir}' already exists.") # デバッグ出力

        # ★★★ ファイルが開けるか先にテスト ★★★
        try:
            with open(log_file_path, 'a') as f:
                print(f"DEBUG [utils]: Successfully opened (or created) '{log_file_path}' for appending.")
        except Exception as e:
             print(f"警告 [utils]: ログファイル '{log_file_path}' を開けません（権限確認）: {e}", file=sys.stderr)
             raise # ファイルが開けない場合はハンドラ設定に進まない

        file_handler = logging.FileHandler(log_file_path, encoding='utf-8', mode='a') # 追記モード
        file_handler.setFormatter(formatter)
        handlers.append(file_handler)
        log_target += f" and File ('{log_file_path}')"
        print(f"DEBUG [utils]: FileHandler created for '{log_file_path}'") # デバッグ出力
    except Exception as e:
        print(f"警告 [utils]: ログファイル '{log_file_path}' のハンドラ設定に失敗しました: {e}", file=sys.stderr)
        # ファイル設定失敗時はコンソールのみで続行

    # basicConfig を使ってハンドラとレベルを設定 (force=True で再設定可能に)
    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', # basicConfig でのフォーマット指定も念のため
        handlers=handlers,
        force=True # ★★★ 既存設定を強制上書き ★★★
    )
    # Playwrightの冗長なログを抑制
    logging.getLogger('playwright').setLevel(logging.WARNING)
    # ログ設定完了メッセージを出力（ハンドラ設定後なのでログに出力されるはず）
    # getLogger(__name__) で utils ロガーを取得して出力
    current_logger = logging.getLogger(__name__)
    current_logger.info(f"Standalone logger setup complete. Level: {logging.getLevelName(log_level)}. Target: {log_target}")
    print(f"DEBUG [utils]: Logging setup finished. Root handlers: {logging.getLogger().handlers}") # デバッグ出力

# --- load_input_from_json, extract_text_from_pdf_sync, download_pdf_async, write_results_to_file は変更なし ---
# (これらの関数は省略)
def load_input_from_json(filepath: str) -> Dict[str, Any]:
    """指定されたJSONファイルから入力データを読み込む。"""
    logger.info(f"入力ファイル '{filepath}' の読み込みを開始します...")
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        # 必須キーのチェック
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
        raise # エラーを再送出
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
                # テキスト抽出 (ソートして論理的な順序に)
                page_text = page.get_text("text", sort=True)
                if page_text:
                    text_parts.append(page_text.strip())
                # else:
                #     logger.debug(f"ページ {page_num + 1}/{len(doc)} からテキストは抽出されませんでした。")
                page_elapsed = (time.monotonic() - page_start_time) * 1000
                logger.debug(f"ページ {page_num + 1} 処理完了 ({page_elapsed:.0f}ms)。")
            except Exception as page_e:
                logger.warning(f"ページ {page_num + 1} の処理中にエラー: {page_e}")
                text_parts.append(f"--- Error processing page {page_num + 1}: {page_e} ---")

        full_text = "\n--- Page Separator ---\n".join(text_parts)
        # 空白行を除去して整形
        cleaned_text = '\n'.join([line.strip() for line in full_text.splitlines() if line.strip()])
        logger.info(f"PDFテキスト抽出完了。総文字数 (整形後): {len(cleaned_text)}")
        # テキストが全く抽出できなかった場合もNoneではなく空文字列を返すか、あるいはその旨を示すメッセージを返すのが良いかもしれない
        return cleaned_text if cleaned_text else "(No text extracted from PDF)"
    except fitz.fitz.TryingToReadFromEmptyFileError: # fitz.fitz... PyMuPDF 1.24+
         logger.error("PDF処理エラー: ファイルデータが空または破損しています。")
         return "Error: PDF data is empty or corrupted."
    except fitz.fitz.FileDataError as e: # fitz.fitz...
         logger.error(f"PDF処理エラー (PyMuPDF FileDataError): {e}", exc_info=False) # トレースバックは不要な場合も
         return f"Error: PDF file data error - {e}"
    except RuntimeError as e:
        # PyMuPDFの他のランタイムエラー（メモリ不足など）
        logger.error(f"PDF処理エラー (PyMuPDF RuntimeError): {e}", exc_info=True)
        return f"Error: PDF processing failed (PyMuPDF RuntimeError) - {e}"
    except Exception as e:
        logger.error(f"PDFテキスト抽出中に予期せぬエラーが発生しました: {e}", exc_info=True)
        return f"Error: Unexpected error during PDF text extraction - {e}"
    finally:
        if doc:
            try:
                doc.close()
                logger.debug("PDFドキュメントを閉じました。")
            except Exception as close_e:
                # クローズエラーは警告レベルに留める
                logger.warning(f"PDFドキュメントのクローズ中にエラーが発生しました (無視): {close_e}")

async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    """指定されたURLからPDFを非同期でダウンロードし、バイトデータを返す。失敗時はNoneを返す。"""
    logger.info(f"PDFを非同期でダウンロード中: {url} (Timeout: {config.PDF_DOWNLOAD_TIMEOUT}ms)")
    try:
        # 一般的なブラウザに近いヘッダーを設定
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br, zstd', # 圧縮を受け入れる
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        # リクエスト実行 (fail_on_status_code=False で 4xx/5xx でもエラーにしない)
        response = await api_request_context.get(url, headers=headers, timeout=config.PDF_DOWNLOAD_TIMEOUT, fail_on_status_code=False)

        # ステータスコード確認
        if not response.ok:
            logger.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}")
            try:
                # エラーレスポンスのボディをデバッグログに出力 (最初の500文字)
                error_body = await response.text(timeout=5000) # ボディ読み取りにもタイムアウト
                logger.debug(f"エラーレスポンスボディ (一部): {error_body[:500]}")
            except Exception as body_err:
                logger.warning(f"エラーレスポンスボディの読み取り中にエラーが発生しました: {body_err}")
            return None # 失敗時はNoneを返す

        # Content-Type確認 (小文字化して比較)
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type:
            logger.warning(f"レスポンスのContent-TypeがPDFではありません ({url}): '{content_type}'。ダウンロードは続行しますが、後続処理で失敗する可能性があります。")
            # ここでNoneを返すか、続行するかは要件による
            # return None

        # レスポンスボディ取得
        body = await response.body()
        if not body:
             logger.warning(f"PDFダウンロード成功 ({url}) Status: {response.status} ですが、レスポンスボディが空です。")
             return None
        logger.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes")
        return body # 成功時はバイトデータを返す

    except PlaywrightTimeoutError:
        logger.error(f"PDFダウンロード中にタイムアウトが発生しました ({url})。設定タイムアウト: {config.PDF_DOWNLOAD_TIMEOUT}ms")
        return None
    except Exception as e:
        logger.error(f"PDF非同期ダウンロード中に予期せぬエラーが発生しました ({url}): {e}", exc_info=True)
        return None

def write_results_to_file(results: List[Dict[str, Any]], filepath: str):
    """実行結果を指定されたファイルに書き込む。"""
    logger.info(f"実行結果を '{filepath}' に書き込みます...")
    try:
        # 出力ディレクトリが存在しない場合は作成
        output_dir = os.path.dirname(filepath)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"出力ディレクトリ '{output_dir}' を作成しました。")

        with open(filepath, "w", encoding="utf-8") as file:
            file.write("--- Web Runner 実行結果 ---\n\n")
            for i, res in enumerate(results):
                step_num = res.get('step', i + 1)
                action_type = res.get('action', 'Unknown')
                status = res.get('status', 'Unknown')
                selector = res.get('selector') # セレクター情報取得

                file.write(f"--- Step {step_num}: {action_type} ({status}) ---\n")
                if selector: # セレクターがあれば表示
                    file.write(f"Selector: {selector}\n")
                if res.get('iframe_selector'): # iframeセレクターがあれば表示
                    file.write(f"IFrame Selector (for switch): {res.get('iframe_selector')}\n")
                if res.get('required_state'): # 要素探索時の状態指定があれば表示
                    file.write(f"Required Element State: {res.get('required_state')}\n")

                if status == "error":
                     # エラーメッセージと詳細情報を書き込む
                     file.write(f"Message: {res.get('message')}\n")
                     # full_error が message と異なる場合のみ詳細として出力
                     if res.get('full_error') and res.get('full_error') != res.get('message'):
                          file.write(f"Details: {res.get('full_error')}\n")
                     if res.get('error_screenshot'):
                          file.write(f"Screenshot: {res.get('error_screenshot')}\n")
                     # トレースバック情報があれば出力 (デバッグに有用)
                     if res.get('traceback'):
                         file.write(f"Traceback:\n{res.get('traceback')}\n")

                elif status == "success":
                    # 成功時の詳細情報を pop しながら書き込む (元の辞書を変更しないようにコピー)
                    details_to_write = res.copy()
                    # 共通情報を削除
                    for key in ['step', 'status', 'action', 'selector', 'iframe_selector', 'required_state']:
                        details_to_write.pop(key, None)

                    # --- アクションタイプに応じた整形出力 ---
                    if action_type == 'get_all_attributes':
                        attr_name = details_to_write.pop('attribute', 'N/A')
                        url_list = details_to_write.pop('url_list', None)
                        pdf_texts = details_to_write.pop('pdf_texts', None)
                        scraped_texts = details_to_write.pop('scraped_texts', None)
                        attr_list = details_to_write.pop('attribute_list', None)

                        file.write(f"Requested Attribute/Content: {attr_name}\n")

                        # 結果リストの最大長を取得
                        list_lengths = [len(lst) for lst in [url_list, pdf_texts, scraped_texts, attr_list] if lst is not None]
                        max_len = max(list_lengths) if list_lengths else 0

                        if max_len > 0:
                            file.write(f"Results ({max_len} items found):\n")
                            for idx in range(max_len):
                                file.write(f"  [{idx+1}]\n")
                                # 各リストから安全に値を取得
                                current_url = url_list[idx] if url_list and idx < len(url_list) else None
                                pdf_content = pdf_texts[idx] if pdf_texts and idx < len(pdf_texts) else None
                                scraped_content = scraped_texts[idx] if scraped_texts and idx < len(scraped_texts) else None
                                attr_content = attr_list[idx] if attr_list and idx < len(attr_list) else None

                                if current_url is not None:
                                     file.write(f"    URL: {current_url}\n")

                                # コンテンツや属性値を出力
                                content_written = False
                                if pdf_content is not None:
                                    prefix = "PDF Content"
                                    if isinstance(pdf_content, str) and pdf_content.startswith("Error:"):
                                        file.write(f"      -> {prefix} (Error): {pdf_content}\n")
                                    elif pdf_content == "(No text extracted from PDF)":
                                        file.write(f"      -> {prefix}: (No text extracted)\n")
                                    else:
                                        file.write(f"      -> {prefix} (Length: {len(pdf_content or '')}):\n")
                                        indented_content = "\n".join(["        " + line for line in str(pdf_content).splitlines()])
                                        file.write(indented_content + "\n")
                                    content_written = True
                                if scraped_content is not None: # pdf と scraped は通常排他だが両方出力
                                    prefix = "Page Content"
                                    if isinstance(scraped_content, str) and scraped_content.startswith("Error"):
                                        file.write(f"      -> {prefix} (Error): {scraped_content}\n")
                                    else:
                                        file.write(f"      -> {prefix} (Length: {len(scraped_content or '')}):\n")
                                        indented_content = "\n".join(["        " + line for line in str(scraped_content).splitlines()])
                                        file.write(indented_content + "\n")
                                    content_written = True
                                if attr_content is not None:
                                     # href, pdf, content 以外の汎用属性の場合
                                     file.write(f"      -> Attribute '{attr_name}' Value: {attr_content}\n")
                                     content_written = True

                                # if not content_written and current_url is not None:
                                #     file.write("      -> (No specific content/attribute requested or found for this item)\n")

                        else: # max_len == 0
                             file.write("Results: (No items found matching the selector)\n")

                    elif action_type == 'get_all_text_contents':
                        text_list_result = details_to_write.pop('text_list', [])
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text) for text in text_list_result if text is not None]
                            file.write(f"Result Text List ({len(valid_texts)} items):\n")
                            if valid_texts:
                                file.write('\n'.join(f"- {text}" for text in valid_texts) + "\n")
                            else:
                                file.write("(No text content found)\n")
                        else:
                             file.write("Result Text List: (Invalid format received)\n")

                    elif action_type == 'get_text_content' or action_type == 'get_inner_text':
                        text = details_to_write.pop('text', None)
                        file.write(f"Result Text:\n{text}\n")
                    elif action_type == 'get_inner_html':
                        html = details_to_write.pop('html', None)
                        file.write(f"Result HTML:\n{html}\n") # HTMLはそのまま出力
                    elif action_type == 'get_attribute':
                        attr_name = details_to_write.pop('attribute', ''); attr_value = details_to_write.pop('value', None)
                        file.write(f"Result Attribute ('{attr_name}'): {attr_value}\n")
                        pdf_text = details_to_write.pop('pdf_text', None)
                        if pdf_text:
                             prefix = "Extracted PDF Text"
                             if isinstance(pdf_text, str) and pdf_text.startswith("Error:"):
                                 file.write(f"{prefix} (Error): {pdf_text}\n")
                             elif pdf_text == "(No text extracted from PDF)":
                                 file.write(f"{prefix}: (No text extracted)\n")
                             else:
                                 file.write(f"{prefix}:\n{pdf_text}\n")
                    elif action_type == 'screenshot':
                         filename = details_to_write.pop('filename', None)
                         if filename: file.write(f"Screenshot saved to: {filename}\n")
                    elif action_type == 'click':
                         if details_to_write.get('new_page_opened'):
                              file.write(f"New page opened: {details_to_write.get('new_page_url')}\n")
                         else:
                              file.write("New page did not open within timeout.\n")
                         # 不要なキーを削除
                         details_to_write.pop('new_page_opened', None)
                         details_to_write.pop('new_page_url', None)

                    # 残りの詳細情報（汎用）を書き込む
                    if details_to_write:
                        file.write("Other Details:\n")
                        for key, val in details_to_write.items():
                             file.write(f"  {key}: {val}\n")

                elif status == "skipped" or status == "warning":
                    file.write(f"Message: {res.get('message', 'No message provided.')}\n")
                else: # Unknown status or other cases
                     file.write(f"Raw Data: {res}\n") # 不明な場合は生データを書き出す

                file.write("\n") # ステップ間の空行

        logger.info(f"結果の書き込みが完了しました: '{filepath}'")
    except IOError as e:
        logger.error(f"結果ファイル '{filepath}' の書き込み中にIOエラーが発生しました: {e}")
    except Exception as e:
        logger.error(f"結果の処理またはファイル書き込み中に予期せぬエラーが発生しました: {e}", exc_info=True)


# --- ▼▼▼ utils.py のテスト用コード（デバッグ目的で追加） ▼▼▼ ---
if __name__ == "__main__":
    print("--- Testing logging setup from utils.py ---")
    # ★★★ テスト用のログファイルパスを MCP_SERVER_LOG_FILE に合わせる ★★★
    TEST_LOG_FILE = config.MCP_SERVER_LOG_FILE
    print(f"Test log file path: {TEST_LOG_FILE}")

    # 既存のテストログファイルがあれば削除（追記モードなので必須ではないが、クリーンなテストのため）
    if os.path.exists(TEST_LOG_FILE):
        try:
            os.remove(TEST_LOG_FILE)
            print(f"Removed existing test log file: {TEST_LOG_FILE}")
        except Exception as e:
            print(f"Could not remove existing test log file: {e}")

    # setup_logging_for_standalone をテストログファイルで呼び出す
    try:
        setup_logging_for_standalone(log_file_path=TEST_LOG_FILE)

        # ロギングが機能するかテスト
        test_logger = logging.getLogger("utils_test")
        print("\nAttempting to log messages...")
        test_logger.info("INFO message from utils_test.")
        test_logger.warning("WARNING message from utils_test.")
        test_logger.error("ERROR message from utils_test.")

        print(f"\nLogging test complete.")
        print(f"Please check the console output above and the content of the file: {os.path.abspath(TEST_LOG_FILE)}") # 絶対パス表示
        print(f"Root logger handlers: {logging.getLogger().handlers}")

    except Exception as e:
        print(f"\n--- Error during logging test ---")
        print(f"{type(e).__name__}: {e}")
        traceback.print_exc()
# --- ▲▲▲ utils.py のテスト用コード ▲▲▲ ---