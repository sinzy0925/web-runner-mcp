# --- ファイル: utils.py ---
"""
JSON読み込み、PDF処理、ロギング設定などの汎用ヘルパー関数。
"""
import json
import logging # logging をインポート
import os
import sys
import asyncio
import fitz  # PyMuPDF
from playwright.async_api import Locator, APIRequestContext, TimeoutError as PlaywrightTimeoutError
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

# --- 依存する設定ファイルをインポート ---
import config

# --- ロガー取得 ---
# 各モジュールでgetLoggerを使用し、ルートロガーの設定を引き継ぐ
logger = logging.getLogger(__name__)

# --- ロギング設定関数 (main.pyでの単体実行用) ---
def setup_logging_for_standalone(log_file_path: str = config.LOG_FILE):
    """Web-Runner単体実行用のロギング設定を行います。"""
    # 既存のハンドラをクリア
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8', mode='a'),
            logging.StreamHandler()
        ]
    )
    logger.info(f"Standalone ロガー設定完了。ログファイル: {log_file_path}") # logger を使用

# --- 入力JSON読み込み ---
def load_input_from_json(filepath: str) -> Dict[str, Any]:
    """指定されたJSONファイルから入力データを読み込む。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "target_url" not in data or "actions" not in data:
            raise ValueError("JSONファイルに必須キー 'target_url' または 'actions' がありません。")
        if not isinstance(data.get("actions"), list):
             raise ValueError("'actions' はリスト形式である必要があります。")
        logger.info(f"入力ファイル '{filepath}' を正常に読み込みました。") # logger を使用
        return data
    except FileNotFoundError:
        logger.error(f"入力ファイルが見つかりません: {filepath}") # logger を使用
        raise
    except json.JSONDecodeError as e:
        logger.error(f"JSONファイルの形式が正しくありません ({filepath}): {e}") # logger を使用
        raise
    except ValueError as ve:
        logger.error(f"入力データの形式が不正です ({filepath}): {ve}") # logger を使用
        raise
    except Exception as e:
        logger.error(f"入力ファイルの読み込み中に予期せぬエラーが発生しました ({filepath}): {e}", exc_info=True) # logger を使用
        raise

# --- PDFテキスト抽出 (同期) ---
def extract_text_from_pdf_sync(pdf_data: bytes) -> Optional[str]:
    """PDFのバイトデータからテキストを抽出する (同期的)"""
    doc = None
    try:
        logger.info("PDFデータからテキスト抽出中...") # logger を使用
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        text = ""
        logger.info(f"PDFページ数: {len(doc)}") # logger を使用
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text", sort=True)
            if page_text:
                text += page_text + "\n--- Page Separator ---\n"
            else:
                logger.warning(f"ページ {page_num + 1} からテキスト抽出できず。") # logger を使用
        logger.info(f"PDFテキスト抽出完了。文字数: {len(text)}") # logger を使用
        cleaned_text = '\n'.join([line.strip() for line in text.splitlines() if line.strip()])
        return cleaned_text
    except fitz.FitzError as e:
        logger.error(f"PDFファイルの処理中にエラー (PyMuPDF): {e}", exc_info=True) # logger を使用
        return f"Error: Failed to process PDF - {e}"
    except Exception as e:
        logger.error(f"PDFテキスト抽出中に予期せぬエラー: {e}", exc_info=True) # logger を使用
        return f"Error: Unexpected error during PDF extraction - {e}"
    finally:
        if doc:
            try:
                doc.close()
                logger.debug("PDFドキュメントを閉じました。") # logger を使用
            except Exception as close_e:
                logger.error(f"PDFドキュメントのクローズ中にエラー: {close_e}") # logger を使用

# --- PDFダウンロード (非同期) ---
async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    """指定されたURLからPDFを非同期でダウンロードし、バイトデータを返す"""
    logger.info(f"PDFを非同期でダウンロード中: {url}") # logger を使用
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = await api_request_context.get(url, headers=headers, timeout=config.PDF_DOWNLOAD_TIMEOUT)
        if not response.ok:
            logger.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}") # logger を使用
            try:
                body_text = await response.text(timeout=5000)
                logger.debug(f"エラーレスポンスボディ (一部): {body_text[:500]}") # logger を使用
            except Exception as body_err:
                 logger.error(f"エラーレスポンスボディ読み取りエラー: {body_err}") # logger を使用
            return None
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type:
            logger.warning(f"Content-TypeがPDFではありません ({url}): {content_type}。処理を試みますが失敗する可能性があります。") # logger を使用
        body = await response.body()
        logger.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes") # logger を使用
        return body
    except PlaywrightTimeoutError:
         logger.error(f"PDFダウンロード中にタイムアウト ({url})") # logger を使用
         return None
    except Exception as e:
        logger.error(f"PDF非同期ダウンロード中にエラー ({url}): {e}", exc_info=True) # logger を使用
        return None

# --- iframeセレクター生成 (非同期) ---
async def generate_iframe_selector_async(iframe_locator: Locator) -> Optional[str]:
    """iframe要素のLocatorから、特定しやすいセレクター文字列を生成する試み (非同期版)。"""
    try:
        attrs = await asyncio.gather(
            iframe_locator.get_attribute('id', timeout=200),
            iframe_locator.get_attribute('name', timeout=200),
            iframe_locator.get_attribute('src', timeout=200),
            return_exceptions=True
        )
        iframe_id, iframe_name, iframe_src = [a if not isinstance(a, Exception) else None for a in attrs]
        if iframe_id: return f'iframe[id="{iframe_id}"]'
        if iframe_name: return f'iframe[name="{iframe_name}"]'
        if iframe_src: return f'iframe[src="{iframe_src}"]'
    except Exception as e:
        logger.debug(f"iframe属性取得中にエラー（無視）: {e}") # logger を使用
    return None

# --- 結果ファイル書き込み ---
def write_results_to_file(results: List[Dict[str, Any]], filepath: str):
    """実行結果を指定されたファイルに書き込む。"""
    logger.info(f"実行結果を '{filepath}' に書き込みます...") # logger を使用
    try:
        output_dir = os.path.dirname(filepath)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir, exist_ok=True)
            logger.info(f"出力ディレクトリ '{output_dir}' を作成しました。") # logger を使用

        with open(filepath, "w", encoding="utf-8") as file:
            file.write("--- Web Runner 実行結果 ---\n\n")
            for i, res in enumerate(results):
                step_num = res.get('step', i + 1)
                action_type = res.get('action', 'Unknown')
                status = res.get('status', 'Unknown')

                file.write(f"--- Step {step_num}: {action_type} ({status}) ---\n") # ステップ情報を行頭に

                if status == "error":
                     if res.get('selector'): file.write(f"Selector: {res.get('selector')}\n")
                     file.write(f"Message: {res.get('message')}\n")
                     if res.get('full_error'): file.write(f"Details: {res.get('full_error')}\n")
                     if res.get('error_screenshot'): file.write(f"Screenshot: {res.get('error_screenshot')}\n")
                elif status == "success":
                    # --- ▼▼▼ 汎用的な成功結果の書き込みを追加 ▼▼▼ ---
                    details_to_write = {k: v for k, v in res.items() if k not in ['step', 'status', 'action']}
                    # --- ▲▲▲ 汎用的な成功結果の書き込みを追加 ▲▲▲ ---

                    # 特定アクションの詳細書き込み
                    if action_type == 'get_all_attributes':
                        attr_name = res.get('attribute')
                        data_list, key_name = (res.get('url_lists', []), 'url_lists') if 'url_lists' in res else (res.get('attribute_list', []), 'attribute_list')
                        if data_list:
                            printdata = '\n'.join(f"- {str(item)}" for item in data_list if item is not None) # 各行にハイフン追加
                            if printdata:
                                file.write(f"Result ({key_name} for '{attr_name}'):\n{printdata}\n")
                                # details_to_write からリスト自体は削除 (個別表示したため)
                                details_to_write.pop(key_name, None)
                                details_to_write.pop('attribute', None)

                        pdf_texts = res.get('pdf_texts')
                        if pdf_texts:
                             valid_pdf_texts = [t for t in pdf_texts if isinstance(t, str)]
                             if valid_pdf_texts:
                                file.write(f"Extracted PDF Texts:\n")
                                file.write('\n\n--- Next PDF Text ---\n\n'.join(valid_pdf_texts))
                                file.write('\n')
                             details_to_write.pop('pdf_texts', None) # 書き込んだので削除
                    elif action_type == 'get_all_text_contents':
                        text_list_result = res.get('text_list')
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text).strip() for text in text_list_result if text is not None and str(text).strip()]
                            if valid_texts:
                                printdata = '\n'.join(f"- {text}" for text in valid_texts)
                                file.write(f"Result Text List:\n{printdata}\n")
                            details_to_write.pop('text_list', None)
                    elif action_type == 'get_text_content':
                        text = res.get('text')
                        if text is not None: file.write(f"Result Text: {str(text).strip()}\n")
                        details_to_write.pop('text', None)
                    elif action_type == 'get_inner_text':
                        text = res.get('text')
                        if text is not None: file.write(f"Result Text: {str(text).strip()}\n")
                        details_to_write.pop('text', None)
                    elif action_type == 'get_inner_html':
                        html = res.get('html')
                        if html is not None: file.write(f"Result HTML:\n{str(html)}\n")
                        details_to_write.pop('html', None)
                    elif action_type == 'get_attribute':
                        attr_name = res.get('attribute'); attr_value = res.get('value')
                        file.write(f"Result Attribute ('{attr_name}'): {attr_value}\n")
                        details_to_write.pop('attribute', None)
                        details_to_write.pop('value', None)
                        pdf_text = res.get('pdf_text')
                        if pdf_text:
                            file.write(f"Extracted PDF Text:\n{pdf_text}\n")
                            details_to_write.pop('pdf_text', None)

                    # --- ▼▼▼ 残りの詳細情報（汎用）を書き込む ▼▼▼ ---
                    if details_to_write:
                        file.write("Other Details:\n")
                        for key, val in details_to_write.items():
                            # セレクターはよく見るので先頭に
                            if key == 'selector':
                                file.write(f"  Selector: {val}\n")
                        for key, val in details_to_write.items():
                            if key != 'selector': # セレクター以外
                                file.write(f"  {key}: {val}\n")
                    # --- ▲▲▲ 残りの詳細情報（汎用）を書き込む ▲▲▲ ---

                elif status == "skipped":
                    file.write(f"Message: {res.get('message')}\n")
                else: # Unknown status
                     file.write(f"Raw Data: {res}\n")

                file.write("\n") # ステップ間の空行

        logger.info(f"結果の書き込み完了: '{filepath}'") # logger を使用
    except IOError as e:
        logger.error(f"結果ファイル '{filepath}' の書き込み中にエラー: {e}") # logger を使用
    except Exception as e:
        logger.error(f"結果の処理またはファイル書き込み中に予期せぬエラー: {e}", exc_info=True) # logger を使用