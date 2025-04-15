# --- ファイル: utils.py ---
"""
JSON読み込み、PDF処理、ロギング設定などの汎用ヘルパー関数。
"""
import json
import logging
import os
import sys
import asyncio
import time # ★ time をインポート
import fitz  # PyMuPDF
from playwright.async_api import Locator, APIRequestContext, TimeoutError as PlaywrightTimeoutError
from typing import Optional, Dict, Any, List
from urllib.parse import urljoin

import config

logger = logging.getLogger(__name__)

def setup_logging_for_standalone(log_file_path: str = config.LOG_FILE):
    """Web-Runner単体実行用のロギング設定を行います。"""
    # 既存のハンドラをすべて削除
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    # 新しいハンドラを設定 (ファイルとコンソール)
    logging.basicConfig(
        level=logging.INFO, # INFOレベル以上を記録
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', # ログフォーマット
        handlers=[
            logging.FileHandler(log_file_path, encoding='utf-8', mode='a'), # ファイル追記モード
            logging.StreamHandler() # コンソール出力
        ]
    )
    # Playwrightの冗長なログを抑制 (必要に応じて調整)
    logging.getLogger('playwright').setLevel(logging.WARNING)
    logger.info(f"Standalone ロガー設定完了。ログファイル: {log_file_path}")

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

# --- ▼▼▼ PDFテキスト抽出関数 (例外処理修正) ▼▼▼ ---
def extract_text_from_pdf_sync(pdf_data: bytes) -> Optional[str]:
    """PDFのバイトデータからテキストを抽出する (同期的)。エラー時はNoneを返すかエラーメッセージ文字列を返す。"""
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
                else:
                    logger.debug(f"ページ {page_num + 1}/{len(doc)} からテキストは抽出されませんでした。")
                page_elapsed = (time.monotonic() - page_start_time) * 1000
                logger.debug(f"ページ {page_num + 1} 処理完了 ({page_elapsed:.0f}ms)。")
            except Exception as page_e:
                logger.warning(f"ページ {page_num + 1} の処理中にエラー: {page_e}")
                text_parts.append(f"--- Error processing page {page_num + 1}: {page_e} ---")

        full_text = "\n--- Page Separator ---\n".join(text_parts)
        # 空白行を除去して整形
        cleaned_text = '\n'.join([line.strip() for line in full_text.splitlines() if line.strip()])
        logger.info(f"PDFテキスト抽出完了。総文字数 (整形後): {len(cleaned_text)}")
        return cleaned_text if cleaned_text else None # テキストがなければNoneを返す
    except RuntimeError as e: # ★★★ fitz.FitzError の代わりに RuntimeError を捕捉 ★★★
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
# --- ▲▲▲ PDFテキスト抽出関数 (例外処理修正) ▲▲▲ ---

async def download_pdf_async(api_request_context: APIRequestContext, url: str) -> Optional[bytes]:
    """指定されたURLからPDFを非同期でダウンロードし、バイトデータを返す。失敗時はNoneを返す。"""
    logger.info(f"PDFを非同期でダウンロード中: {url} (Timeout: {config.PDF_DOWNLOAD_TIMEOUT}ms)")
    try:
        # 一般的なブラウザに近いヘッダーを設定
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Accept': 'application/pdf,text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br, zstd',
            'Accept-Language': 'ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7'
        }
        # リクエスト実行
        response = await api_request_context.get(url, headers=headers, timeout=config.PDF_DOWNLOAD_TIMEOUT)

        # ステータスコード確認
        if not response.ok:
            logger.error(f"PDFダウンロード失敗 ({url}) - Status: {response.status} {response.status_text}")
            try:
                # エラーレスポンスのボディをデバッグログに出力 (最初の500文字)
                error_body = await response.text(timeout=5000) # ボディ読み取りにもタイムアウト
                logger.debug(f"エラーレスポンスボディ (一部): {error_body[:500]}")
            except Exception as body_err:
                logger.error(f"エラーレスポンスボディの読み取り中にエラーが発生しました: {body_err}")
            return None # 失敗時はNoneを返す

        # Content-Type確認 (小文字化して比較)
        content_type = response.headers.get('content-type', '').lower()
        if 'application/pdf' not in content_type:
            logger.warning(f"レスポンスのContent-TypeがPDFではありません ({url}): '{content_type}'。ダウンロードは続行しますが、後続処理で失敗する可能性があります。")

        # レスポンスボディ取得
        body = await response.body()
        logger.info(f"PDFダウンロード成功 ({url})。サイズ: {len(body)} bytes")
        return body # 成功時はバイトデータを返す

    except PlaywrightTimeoutError:
        logger.error(f"PDFダウンロード中にタイムアウトが発生しました ({url})。設定タイムアウト: {config.PDF_DOWNLOAD_TIMEOUT}ms")
        return None
    except Exception as e:
        logger.error(f"PDF非同期ダウンロード中に予期せぬエラーが発生しました ({url}): {e}", exc_info=True)
        return None

async def generate_iframe_selector_async(iframe_locator: Locator) -> Optional[str]:
    """(変更なし) iframe要素のLocatorから、特定しやすいセレクター文字列を生成する試み。"""
    try:
        # 属性取得を並行実行 (タイムアウト短め)
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
            # srcは長すぎる場合があるので、必要に応じて調整
            return f'iframe[src="{iframe_src}"]'

    except Exception as e:
        # 属性取得中のエラーはデバッグレベルでログ記録し、無視
        logger.debug(f"iframe属性取得中にエラー（無視）: {e}")

    # 適切なセレクターが見つからなければNoneを返す
    return None

# --- 結果ファイル書き込み (変更なし) ---
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

                if status == "error":
                     # エラーメッセージと詳細情報を書き込む
                     file.write(f"Message: {res.get('message')}\n")
                     if res.get('full_error') and res.get('full_error') != res.get('message'):
                          file.write(f"Details: {res.get('full_error')}\n")
                     if res.get('error_screenshot'):
                          file.write(f"Screenshot: {res.get('error_screenshot')}\n")
                elif status == "success":
                    # 成功時の詳細情報を pop しながら書き込む
                    details_to_write = {k: v for k, v in res.items() if k not in ['step', 'status', 'action', 'selector']}

                    # --- アクションタイプに応じた整形出力 ---
                    if action_type == 'get_all_attributes':
                        attr_name = details_to_write.pop('attribute', 'N/A')
                        url_list = details_to_write.pop('url_list', None) # URLリストを取得
                        pdf_texts = details_to_write.pop('pdf_texts', None)
                        scraped_texts = details_to_write.pop('scraped_texts', None)
                        attr_list = details_to_write.pop('attribute_list', None)

                        file.write(f"Requested Attribute: {attr_name}\n")

                        # URLリストが存在する場合、それと内容を組み合わせて出力
                        if url_list is not None:
                            file.write("Results (URL and Content/Attribute):\n")
                            max_len = max(len(url_list), len(pdf_texts or []), len(scraped_texts or []), len(attr_list or []))
                            # リストの長さが不一致の場合に備えて安全にアクセス
                            for idx in range(max_len):
                                current_url = url_list[idx] if idx < len(url_list) else "(URL index out of bounds)"
                                file.write(f"  [{idx+1}] URL: {current_url}\n")

                                pdf_content = pdf_texts[idx] if pdf_texts and idx < len(pdf_texts) else None
                                scraped_content = scraped_texts[idx] if scraped_texts and idx < len(scraped_texts) else None
                                attr_content = attr_list[idx] if attr_list and idx < len(attr_list) else None

                                if pdf_content is not None:
                                    prefix = "PDF Content"
                                    if isinstance(pdf_content, str) and pdf_content.startswith("Error:"):
                                        file.write(f"      -> {prefix} (Error): {pdf_content}\n")
                                    else:
                                        file.write(f"      -> {prefix} (Length: {len(pdf_content or '')}:\n")
                                        # 可読性のためインデントして複数行出力
                                        indented_content = "\n".join(["        " + line for line in str(pdf_content).splitlines()])
                                        file.write(indented_content + "\n")
                                elif scraped_content is not None:
                                    prefix = "Page Content"
                                    if isinstance(scraped_content, str) and scraped_content.startswith("Error"):
                                        file.write(f"      -> {prefix} (Error): {scraped_content}\n")
                                    else:
                                        file.write(f"      -> {prefix} (Length: {len(scraped_content or '')}:\n")
                                        indented_content = "\n".join(["        " + line for line in str(scraped_content).splitlines()])
                                        file.write(indented_content + "\n")
                                elif attr_content is not None:
                                     # href, pdf, content 以外の汎用属性の場合
                                     file.write(f"      -> Attribute '{attr_name}' Value: {attr_content}\n")
                                # else:
                                #     # 何も関連情報がない場合
                                #     file.write("      -> (No relevant content/attribute found for this URL index)\n") # 必要ならコメント解除

                        # URLリストがない場合（旧バージョン互換 or エラー）
                        elif attr_list is not None:
                             file.write(f"Result (Attribute List for '{attr_name}'):\n")
                             file.write('\n'.join(f"- {str(item)}" for item in attr_list if item is not None) + "\n")
                        elif pdf_texts is not None:
                             file.write("Extracted PDF Texts (URL mapping lost):\n")
                             file.write('\n\n--- Next PDF Text ---\n\n'.join(str(t) for t in pdf_texts if t is not None) + '\n')
                        elif scraped_texts is not None:
                             file.write("Scraped Page Texts (URL mapping lost):\n")
                             file.write('\n\n--- Next Page Text ---\n\n'.join(str(t) for t in scraped_texts if t is not None) + '\n')

                    elif action_type == 'get_all_text_contents':
                        text_list_result = details_to_write.pop('text_list', [])
                        if isinstance(text_list_result, list):
                            valid_texts = [str(text) for text in text_list_result if text is not None] # 空文字も含む可能性
                            if valid_texts:
                                file.write(f"Result Text List ({len(valid_texts)} items):\n")
                                file.write('\n'.join(f"- {text}" for text in valid_texts) + "\n")
                            else:
                                file.write("Result Text List: (No text content found)\n")
                    elif action_type == 'get_text_content' or action_type == 'get_inner_text':
                        text = details_to_write.pop('text', None)
                        file.write(f"Result Text: {text}\n")
                    elif action_type == 'get_inner_html':
                        html = details_to_write.pop('html', None)
                        file.write(f"Result HTML:\n{html}\n") # HTMLはそのまま出力
                    elif action_type == 'get_attribute':
                        attr_name = details_to_write.pop('attribute', ''); attr_value = details_to_write.pop('value', None)
                        file.write(f"Result Attribute ('{attr_name}'): {attr_value}\n")
                        pdf_text = details_to_write.pop('pdf_text', None)
                        if pdf_text:
                             file.write(f"Extracted PDF Text:\n{pdf_text}\n")

                    # 残りの詳細情報（汎用）を書き込む
                    if details_to_write:
                        file.write("Other Details:\n")
                        for key, val in details_to_write.items():
                             # 特定のキーは表示を調整するなどしても良い
                             file.write(f"  {key}: {val}\n")

                elif status == "skipped":
                    file.write(f"Message: {res.get('message', 'No message provided.')}\n")
                else: # Unknown status or other cases
                     file.write(f"Raw Data: {res}\n") # 不明な場合は生データを書き出す

                file.write("\n") # ステップ間の空行

        logger.info(f"結果の書き込みが完了しました: '{filepath}'")
    except IOError as e:
        logger.error(f"結果ファイル '{filepath}' の書き込み中にIOエラーが発生しました: {e}")
    except Exception as e:
        logger.error(f"結果の処理またはファイル書き込み中に予期せぬエラーが発生しました: {e}", exc_info=True)