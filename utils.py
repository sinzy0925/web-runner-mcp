# --- ファイル: utils.py ---
"""
JSON読み込み、PDF処理、ロギング設定などの汎用ヘルパー関数。
"""
import json
import logging
import os
import sys
import asyncio
import fitz  # PyMuPDF
from playwright.async_api import Locator, APIRequestContext, TimeoutError as PlaywrightTimeoutError
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

import config

logger = logging.getLogger(__name__)

def setup_logging_for_standalone(log_file_path: str = config.LOG_FILE):
    """Web-Runner単体実行用のロギング設定を行います。"""
    for handler in logging.root.handlers[:]: logging.root.removeHandler(handler)
    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(log_file_path, encoding='utf-8', mode='a'), logging.StreamHandler()]
    )
    logger.info(f"Standalone ロガー設定完了。ログファイル: {log_file_path}")

def load_input_from_json(filepath: str) -> Dict[str, Any]:
    """指定されたJSONファイルから入力データを読み込む。"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f: data = json.load(f)
        if "target_url" not in data or "actions" not in data: raise ValueError("JSON必須キー不足")
        if not isinstance(data.get("actions"), list): raise ValueError("'actions'はリスト形式必須")
        logger.info(f"入力ファイル '{filepath}' を正常に読み込みました。")
        return data
    except FileNotFoundError: logger.error(f"入力ファイルが見つかりません: {filepath}"); raise
    except json.JSONDecodeError as e: logger.error(f"JSON形式エラー ({filepath}): {e}"); raise
    except ValueError as ve: logger.error(f"入力データ形式不正 ({filepath}): {ve}"); raise
    except Exception as e: logger.error(f"入力ファイル読み込み中に予期せぬエラー ({filepath}): {e}", exc_info=True); raise

def extract_text_from_pdf_sync(pdf_data: bytes) -> Optional[str]:
    """PDFのバイトデータからテキストを抽出する (同期的)"""
    doc = None
    try:
        logger.info("PDFデータからテキスト抽出中...")
        doc = fitz.open(stream=pdf_data, filetype="pdf")
        text = ""; logger.info(f"PDFページ数: {len(doc)}")
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text("text", sort=True)
            if page_text: text += page_text + "\n--- Page Separator ---\n"
            else: logger.warning(f"ページ {page_num + 1} からテキスト抽出できず。")
        logger.info(f"PDFテキスト抽出完了。文字数: {len(text)}")
        return '\n'.join([line.strip() for line in text.splitlines() if line.strip()])
    except fitz.FitzError as e: logger.error(f"PDF処理エラー (PyMuPDF): {e}", exc_info=True); return f"Error: {e}"
    except Exception as e: logger.error(f"PDFテキスト抽出中に予期せぬエラー: {e}", exc_info=True); return f"Error: {e}"
    finally:
        if doc:
            try: doc.close(); logger.debug("PDFドキュメントを閉じました。")
            except Exception as close_e: logger.error(f"PDFドキュメントのクローズ中にエラー: {close_e}")

async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    """指定されたURLからPDFを非同期でダウンロードし、バイトデータを返す"""
    logger.info(f"PDFを非同期でダウンロード中: {url}")
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = await api_request_context.get(url, headers=headers, timeout=config.PDF_DOWNLOAD_TIMEOUT)
        if not response.ok:
            logger.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}")
            try: logger.debug(f"エラーレスポンスボディ (一部): {(await response.text(timeout=5000))[:500]}")
            except Exception as body_err: logger.error(f"エラーレスポンスボディ読み取りエラー: {body_err}")
            return None
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type: logger.warning(f"Content-Type非PDF ({url}): {content_type}。処理試行。")
        body = await response.body()
        logger.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes")
        return body
    except PlaywrightTimeoutError: logger.error(f"PDFダウンロード中にタイムアウト ({url})"); return None
    except Exception as e: logger.error(f"PDF非同期ダウンロード中にエラー ({url}): {e}", exc_info=True); return None

async def generate_iframe_selector_async(iframe_locator: Locator) -> Optional[str]:
    """iframe要素のLocatorから、特定しやすいセレクター文字列を生成する試み。"""
    try:
        attrs = await asyncio.gather(
            iframe_locator.get_attribute('id', timeout=200), iframe_locator.get_attribute('name', timeout=200),
            iframe_locator.get_attribute('src', timeout=200), return_exceptions=True
        )
        iframe_id, iframe_name, iframe_src = [a if not isinstance(a, Exception) else None for a in attrs]
        if iframe_id: return f'iframe[id="{iframe_id}"]'
        if iframe_name: return f'iframe[name="{iframe_name}"]'
        if iframe_src: return f'iframe[src="{iframe_src}"]'
    except Exception as e: logger.debug(f"iframe属性取得中にエラー（無視）: {e}")
    return None

# --- ▼▼▼ 結果ファイル書き込み (修正) ▼▼▼ ---
def write_results_to_file(results: List[Dict[str, Any]], filepath: str):
    """実行結果を指定されたファイルに書き込む。"""
    logger.info(f"実行結果を '{filepath}' に書き込みます...")
    try:
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

                file.write(f"--- Step {step_num}: {action_type} ({status}) ---\n")

                if status == "error":
                     if res.get('selector'): file.write(f"Selector: {res.get('selector')}\n")
                     file.write(f"Message: {res.get('message')}\n")
                     if res.get('full_error'): file.write(f"Details: {res.get('full_error')}\n")
                     if res.get('error_screenshot'): file.write(f"Screenshot: {res.get('error_screenshot')}\n")
                elif status == "success":
                    details_to_write = {k: v for k, v in res.items() if k not in ['step', 'status', 'action']}

                    # --- アクションタイプに応じた整形出力 ---
                    if 'selector' in details_to_write:
                        file.write(f"Selector: {details_to_write.pop('selector')}\n")

                    if action_type == 'get_all_attributes':
                        attr_name = details_to_write.pop('attribute', 'N/A') # 元の属性名も記録
                        file.write(f"Original Attribute Name: {attr_name}\n")

                        if 'url_lists' in details_to_write:
                            url_list = details_to_write.pop('url_lists', [])
                            if url_list:
                                file.write("Result (URL List):\n")
                                file.write('\n'.join(f"- {str(item)}" for item in url_list if item is not None) + "\n")
                        elif 'attribute_list' in details_to_write:
                             attr_list = details_to_write.pop('attribute_list', [])
                             if attr_list:
                                 file.write(f"Result (Attribute List for '{attr_name}'):\n")
                                 file.write('\n'.join(f"- {str(item)}" for item in attr_list if item is not None) + "\n")

                        # ★ pdf_texts, scraped_texts を個別キーとして書き出す
                        if 'pdf_texts' in details_to_write:
                             pdf_texts = details_to_write.pop('pdf_texts', [])
                             valid_pdf_texts = [t for t in pdf_texts if t and isinstance(t, str)]
                             if valid_pdf_texts:
                                 file.write("Extracted PDF Texts:\n")
                                 file.write('\n\n--- Next PDF Text ---\n\n'.join(valid_pdf_texts) + '\n')
                             else:
                                 file.write("Extracted PDF Texts: (None or errors only)\n")
                        if 'scraped_texts' in details_to_write:
                             scraped_texts = details_to_write.pop('scraped_texts', [])
                             if scraped_texts:
                                 file.write("Scraped Page Texts:\n")
                                 # エラーメッセージもそのまま書き出す
                                 file.write('\n\n--- Next Page Text ---\n\n'.join(str(t) if t is not None else '(No text)' for t in scraped_texts) + '\n')

                    elif action_type == 'get_all_text_contents':
                        text_list_result = details_to_write.pop('text_list', [])
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text).strip() for text in text_list_result if text is not None and str(text).strip()]
                            if valid_texts:
                                printdata = '\n'.join(f"- {text}" for text in valid_texts)
                                file.write(f"Result Text List:\n{printdata}\n")
                    elif action_type == 'get_text_content':
                        text = details_to_write.pop('text', None)
                        if text is not None: file.write(f"Result Text: {str(text).strip()}\n")
                    elif action_type == 'get_inner_text':
                        text = details_to_write.pop('text', None)
                        if text is not None: file.write(f"Result Text: {str(text).strip()}\n")
                    elif action_type == 'get_inner_html':
                        html = details_to_write.pop('html', None)
                        if html is not None: file.write(f"Result HTML:\n{str(html)}\n")
                    elif action_type == 'get_attribute':
                        attr_name = details_to_write.pop('attribute', ''); attr_value = details_to_write.pop('value', None)
                        file.write(f"Result Attribute ('{attr_name}'): {attr_value}\n")
                        pdf_text = details_to_write.pop('pdf_text', None)
                        if pdf_text: file.write(f"Extracted PDF Text:\n{pdf_text}\n")

                    # 残りの詳細情報（汎用）を書き込む
                    if details_to_write:
                        file.write("Other Details:\n")
                        for key, val in details_to_write.items():
                            file.write(f"  {key}: {val}\n")

                elif status == "skipped":
                    file.write(f"Message: {res.get('message')}\n")
                else: # Unknown status
                     file.write(f"Raw Data: {res}\n")

                file.write("\n") # ステップ間の空行

        logger.info(f"結果の書き込み完了: '{filepath}'")
    except IOError as e:
        logger.error(f"結果ファイル '{filepath}' の書き込み中にエラー: {e}")
    except Exception as e:
        logger.error(f"結果の処理またはファイル書き込み中に予期せぬエラー: {e}", exc_info=True)
# --- ▲▲▲ 結果ファイル書き込み (修正) ▲▲▲ ---