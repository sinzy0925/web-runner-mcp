import asyncio
from playwright_stealth import stealth_async
import os
import logging
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError, Error as PlaywrightError
from bs4 import BeautifulSoup, Comment # BeautifulSoupをインポート

# --- 設定 ---
TARGET_URL = "https://www.google.com/search?q=mastra"  # ★取得したいページのURLに変更してください
OUTPUT_FILENAME = "simplified_html_output.html" # 出力ファイル名
VIEWPORT_WIDTH = 1920 # スクリーンショットと同じ幅にすると再現性が高まる可能性
VIEWPORT_HEIGHT = 1080
PAGE_LOAD_TIMEOUT = 60000  # ページ読み込みタイムアウト (ミリ秒)
WAIT_AFTER_LOAD = 10000 # ページ読み込み後に追加で待機する時間 (ミリ秒、動的コンテンツ読み込み用)

# 削除するタグのリスト
TAGS_TO_REMOVE = ['script', 'style', 'link', 'meta', 'header', 'footer', 'nav', 'aside']
# 削除する属性のリスト (正規表現パターンも可) - 例: イベントハンドラ、Google固有属性など
# 注意: id, class, name, href, src, aria-*, data-*, role, title, value, placeholder などは残す
ATTRIBUTES_TO_REMOVE = [
    'jsaction', 'jscontroller', 'jsname', 'jsmodel', 'jsshadow', 'jsslot',
    'data-ved', 'data-jsarwt', 'data-usm', 'data-lm', # Google固有属性の例
    'nonce',
    # イベントハンドラ属性 (onclick, onmouseover など)
    r'^on.*$' # 正規表現で on から始まる属性を削除
]
# コメントを削除するかどうか
REMOVE_COMMENTS = True

# --- ロギング設定 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

async def get_simplified_html(url: str) -> str | None:
    """指定されたURLからHTMLを取得し、不要な要素・属性を削除して返す"""
    logging.info(f"URLからHTMLを取得開始: {url}")
    simplified_html = None
    playwright = None
    browser = None

    try:
        playwright = await async_playwright().start()
        # Headless=False にして実際の表示を確認しながら調整するのも有効
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context(
            viewport={'width': VIEWPORT_WIDTH, 'height': VIEWPORT_HEIGHT},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            locale='ja-JP'
        )
        await stealth_async(context)
        page = await context.new_page()

        logging.info(f"ナビゲーション中 ({PAGE_LOAD_TIMEOUT}ms timeout)...")
        await page.goto(url, wait_until="load", timeout=PAGE_LOAD_TIMEOUT) # 'load' または 'domcontentloaded'
        logging.info(f"ページ読み込み完了。追加待機 {WAIT_AFTER_LOAD}ms...")
        await asyncio.sleep(WAIT_AFTER_LOAD / 1000) # 秒単位で待機

        logging.info("<body> 要素のHTMLを取得中...")
        # body要素のHTMLを取得 (outerHTMLだと<body>タグ自体も含む)
        # innerHTMLだとbodyの中身だけ
        html_content = await page.locator('body').inner_html()
        # もしくはページ全体のHTMLを取得する場合:
        # html_content = await page.content()

        logging.info(f"取得したHTMLサイズ: {len(html_content)} bytes")
        logging.info("HTMLの簡略化処理を開始...")

        # --- BeautifulSoup でHTMLをパースして処理 ---
        # html.parser より lxml の方が高速で頑健なことが多い
        soup = BeautifulSoup(html_content, 'lxml')

        # 1. コメントを削除
        if REMOVE_COMMENTS:
            comments = soup.find_all(string=lambda text: isinstance(text, Comment))
            for comment in comments:
                comment.extract()
            logging.info("コメントを削除しました。")

        # 2. 指定されたタグを削除
        removed_tags_count = 0
        for tag_name in TAGS_TO_REMOVE:
            tags = soup.find_all(tag_name)
            for tag in tags:
                tag.decompose() # タグとその中身を完全に削除
                removed_tags_count += 1
        logging.info(f"{len(TAGS_TO_REMOVE)} 種類のタグ ({removed_tags_count}個) を削除しました。")

        # 3. 指定された属性を削除
        removed_attrs_count = 0
        import re # 正規表現を使うためにインポート
        tags_with_attrs = soup.find_all(True) # 全てのタグを取得
        for tag in tags_with_attrs:
            attrs_to_delete = []
            for attr_name in tag.attrs:
                should_remove = False
                # 属性名リストと直接比較
                if attr_name in ATTRIBUTES_TO_REMOVE:
                    should_remove = True
                # 正規表現パターンと比較
                else:
                    for pattern in ATTRIBUTES_TO_REMOVE:
                        # パターンが正規表現かチェック (簡単な判定)
                        if isinstance(pattern, str) and (pattern.startswith('^') or pattern.endswith('$') or '*' in pattern or '+' in pattern or '?' in pattern):
                            try:
                                if re.match(pattern, attr_name):
                                    should_remove = True
                                    break
                            except re.error:
                                logging.warning(f"属性削除パターンが無効な正規表現です: {pattern}")
                                # パターンリストから削除するなど検討
                                ATTRIBUTES_TO_REMOVE.remove(pattern)
                if should_remove:
                    attrs_to_delete.append(attr_name)

            for attr_name in attrs_to_delete:
                del tag[attr_name]
                removed_attrs_count += 1
        logging.info(f"{removed_attrs_count} 個の不要な属性を削除しました。")

        # 処理後のHTMLを取得 (pretty=Trueで整形する)
        simplified_html = soup.prettify()
        logging.info(f"簡略化後のHTMLサイズ: {len(simplified_html)} bytes")

    except PlaywrightTimeoutError:
        logging.error(f"ページの読み込みまたは要素の取得がタイムアウトしました: {url}")
    except PlaywrightError as e:
         logging.error(f"Playwright関連のエラーが発生しました: {e}")
    except ImportError:
         logging.error("BeautifulSoup4 または lxml がインストールされていません。")
         logging.error("pip install beautifulsoup4 lxml を実行してください。")
    except Exception as e:
        logging.error(f"HTMLの取得または処理中に予期せぬエラーが発生しました: {e}")
        logging.debug(traceback.format_exc())
    finally:
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()
        logging.info("Playwrightリソースを解放しました。")

    return simplified_html

async def main():
    simplified_html = await get_simplified_html(TARGET_URL)

    if simplified_html:
        output_path = Path(OUTPUT_FILENAME)
        try:
            # 出力ディレクトリが存在しない場合は作成
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(simplified_html)
            logging.info(f"簡略化されたHTMLを '{output_path.resolve()}' に保存しました。")
        except IOError as e:
            logging.error(f"ファイルへの書き込み中にエラーが発生しました: {e}")
        except Exception as e:
            logging.error(f"ファイル書き込み中に予期せぬエラーが発生しました: {e}")
    else:
        logging.warning("簡略化されたHTMLを取得できなかったため、ファイルは生成されませんでした。")

if __name__ == "__main__":
    # Windowsで実行する場合の非同期イベントループポリシー設定 (必要なら)
    # if os.name == 'nt':
    #     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())