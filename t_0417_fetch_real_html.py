from playwright_stealth import stealth_async
import asyncio
import os
import logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from bs4 import BeautifulSoup, Comment
import traceback
import re # 属性削除の正規表現用
from urllib.parse import urlparse # URLからファイル名を生成するため

# --- 設定 ---
# ★★★ テストしたいURLをこのリストに追加・変更してください ★★★
TARGET_URLS = [
    "https://www.google.co.jp/",
    "https://www.google.com/search?q=mastra", # Google検索結果
    "https://chatgpt-lab.com/n/nc1a8b4bb4911" # ブログ記事
    # "https://..." # 他のテストしたいURLを追加
]

OUTPUT_DIR = Path("./real_html_outputs") # HTMLファイルの保存先ディレクトリ
DO_CLEANUP = True # ★HTMLのクリーンアップを行うかどうか (True/False)

# --- Playwright関連設定 ---
VIEWPORT_WIDTH = 1920
VIEWPORT_HEIGHT = 1080
PAGE_LOAD_TIMEOUT = 30000
WAIT_AFTER_LOAD = 3000 # 動的コンテンツ読み込みのための追加待機時間 (ミリ秒)
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36'

# --- HTMLクリーンアップ設定 (DO_CLEANUP=True の場合に有効) ---
TAGS_TO_REMOVE = ['script', 'style', 'link', 'meta', 'header', 'footer', 'nav', 'aside', 'iframe', 'noscript'] # iframe, noscript も追加
ATTRIBUTES_TO_REMOVE = [
    'jsaction', 'jscontroller', 'jsname', 'jsmodel', 'jsshadow', 'jsslot',
    'data-ved', 'data-jsarwt', 'data-usm', 'data-lm', 'ping',
    'nonce', 'crossorigin', 'integrity', 'referrerpolicy', 'decoding', 'loading',
    'aria-hidden', # 非表示要素は削除した方が良い場合も (ただしラベル等は残したい場合もある)
    r'^on.*$' # イベントハンドラ
]
REMOVE_COMMENTS = True
PRETTIFY_HTML = True # 出力するHTMLを整形するかどうか

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def sanitize_filename(url: str) -> str:
    """URLから安全なファイル名を生成する"""
    try:
        parsed = urlparse(url)
        # ホスト名 + パスの一部 + クエリの一部 を組み合わせる
        # パスやクエリがない場合はホスト名のみ
        path_part = parsed.path.strip('/').replace('/', '_')[:50] if parsed.path else ""
        query_part = parsed.query.replace('&', '_').replace('=', '-')[:50] if parsed.query else ""
        filename = f"{parsed.netloc}_{path_part}_{query_part}"
        # 長すぎる場合や不要文字を除去
        filename = re.sub(r'[^a-zA-Z0-9_-]', '_', filename).strip('_')
        filename = filename[:150] # 最大長制限
        if not filename: # 空になった場合はデフォルト名
            filename = "default_page"
        return f"{filename}.html"
    except Exception:
        # エラー時はデフォルト名
        logging.warning(f"URLからのファイル名生成に失敗: {url}", exc_info=True)
        return "default_page.html"

def cleanup_html(html_content: str) -> str:
    """HTMLコンテンツから不要な要素や属性を削除する"""
    logging.info("HTMLのクリーンアップ処理を開始...")
    try:
        soup = BeautifulSoup(html_content, 'lxml')

        # 1. コメント削除
        if REMOVE_COMMENTS:
            comments = soup.find_all(string=lambda text: isinstance(text, Comment))
            count = len(comments)
            for comment in comments:
                comment.extract()
            if count > 0: logging.info(f"{count} 個のコメントを削除しました。")

        # 2. 指定タグ削除
        removed_tags_count = 0
        for tag_name in TAGS_TO_REMOVE:
            tags = soup.find_all(tag_name)
            count = len(tags)
            if count > 0:
                for tag in tags:
                    tag.decompose()
                logging.debug(f"タグ '{tag_name}' を {count} 個削除しました。")
                removed_tags_count += count
        if removed_tags_count > 0: logging.info(f"合計 {removed_tags_count} 個の指定タグを削除しました。")

        # 3. 指定属性削除
        removed_attrs_count = 0
        tags_with_attrs = soup.find_all(True)
        patterns = []
        literal_attrs = set()
        # パターンとリテラルを分離
        for item in ATTRIBUTES_TO_REMOVE:
            if isinstance(item, str) and any(c in item for c in '^$*+?.'): # 簡単な正規表現判定
                try:
                    patterns.append(re.compile(item))
                except re.error:
                    logging.warning(f"無効な正規表現パターンをスキップ: {item}")
            elif isinstance(item, str):
                literal_attrs.add(item)

        for tag in tags_with_attrs:
            attrs_to_delete = []
            if not hasattr(tag, 'attrs'): continue # 稀に属性を持たない要素がある

            for attr_name in list(tag.attrs.keys()): # イテレート中に変更するためリスト化
                should_remove = False
                if attr_name in literal_attrs:
                    should_remove = True
                else:
                    for pattern in patterns:
                        if pattern.match(attr_name):
                            should_remove = True
                            break
                if should_remove:
                    attrs_to_delete.append(attr_name)

            if attrs_to_delete:
                 # logging.debug(f"  Deleting attributes from <{tag.name}>: {attrs_to_delete}")
                 for attr_name in attrs_to_delete:
                     del tag[attr_name]
                     removed_attrs_count += 1

        if removed_attrs_count > 0: logging.info(f"合計 {removed_attrs_count} 個の不要な属性を削除しました。")

        # 処理後のHTMLを取得
        if PRETTIFY_HTML:
            cleaned_html = soup.prettify()
        else:
            cleaned_html = str(soup)
        logging.info("HTMLのクリーンアップ処理が完了しました。")
        return cleaned_html

    except ImportError:
         logging.error("BeautifulSoup4 または lxml がインストールされていません。")
         return html_content # クリーンアップせず元のHTMLを返す
    except Exception as e:
        logging.error(f"HTMLクリーンアップ中にエラー: {e}")
        logging.debug(traceback.format_exc())
        return html_content # エラー時も元のHTMLを返す

async def fetch_and_save_html(url: str, output_dir: Path):
    """指定されたURLからHTMLを取得し、(オプションでクリーンアップして)保存する"""
    logging.info("-" * 30)
    logging.info(f"処理開始: {url}")
    playwright = None
    browser = None
    html_content = None
    output_filename = sanitize_filename(url)
    output_path = output_dir / output_filename

    try:
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=False) # 基本はヘッドレスで取得
        context = await browser.new_context(
            viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
            user_agent=USER_AGENT,
            locale='ja-JP'
        )
        await stealth_async(context)
        page = await context.new_page()

        logging.info(f"ナビゲーション中...")
        await page.goto(url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT)
        logging.info(f"ページ読み込み完了。追加待機 {WAIT_AFTER_LOAD}ms...")
        await asyncio.sleep(WAIT_AFTER_LOAD / 1000)

        logging.info("ページ全体のHTMLを取得中...")
        html_content = await page.content()
        logging.info(f"取得したHTMLサイズ (クリーンアップ前): {len(html_content)} bytes")

        # --- クリーンアップ実行 ---
        if DO_CLEANUP and html_content:
            html_to_save = cleanup_html(html_content)
        elif html_content:
             html_to_save = html_content # クリーンアップしない場合はそのまま
             if PRETTIFY_HTML: # クリーンアップしなくても整形はする
                 try:
                     soup = BeautifulSoup(html_to_save, 'lxml')
                     html_to_save = soup.prettify()
                 except Exception as e:
                     logging.warning(f"HTMLの整形に失敗: {e}")
        else:
             html_to_save = None

        # --- ファイル保存 ---
        if html_to_save:
            output_dir.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(html_to_save)
            logging.info(f"HTMLを保存しました: '{output_path.resolve()}'")
            logging.info(f"保存したHTMLサイズ: {len(html_to_save)} bytes")
        else:
            logging.warning("保存するHTMLコンテンツがありません。")

    except PlaywrightTimeoutError:
        logging.error(f"タイムアウトしました: {url}")
    except PlaywrightError as e:
         logging.error(f"Playwrightエラー: {e}")
    except Exception as e:
        logging.error(f"予期せぬエラー: {e}")
        logging.debug(traceback.format_exc())
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        logging.info(f"処理完了: {url}")

async def main():
    logging.info(f"実際のWebサイトからHTMLを取得します (保存先: {OUTPUT_DIR})")
    logging.info(f"HTMLクリーンアップ: {'有効' if DO_CLEANUP else '無効'}")
    for url in TARGET_URLS:
        await fetch_and_save_html(url, OUTPUT_DIR)
    logging.info("全てのURLの処理が完了しました。")

if __name__ == "__main__":
    asyncio.run(main())