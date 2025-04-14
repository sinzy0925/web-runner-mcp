import time
from playwright.sync_api import sync_playwright
import logging

# ロギングの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    with sync_playwright() as p:
        # Chromiumブラウザを起動 (headless=Falseで表示)
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()
        logging.info("ブラウザを起動しました。")

        try:
            # DuckDuckGoにアクセス
            url = "https://duckduckgo.com/"
            logging.info(f"{url} にアクセスします...")
            page.goto(url, wait_until="domcontentloaded") # ページの基本構造が読み込まれるまで待機
            logging.info("アクセス完了。")

            # 検索ボックスの特定 (複数のセレクタ候補で安定性を高める)
            search_box_selector = '#search_form_input_homepage' # ホームページ用のID
            # 念のため、一般的な検索ボックスのname属性も用意
            search_box_selector_general = 'input[name="q"]'

            logging.info("検索ボックスを探しています...")
            try:
                # まずホームページ用のIDで試す
                search_box = page.locator(search_box_selector)
                # 要素が表示されるまで少し待つ (念のため)
                search_box.wait_for(state="visible", timeout=5000) # 5秒待機
                logging.info(f"検索ボックス ({search_box_selector}) を見つけました。")
            except Exception:
                logging.warning(f"{search_box_selector} が見つかりません。代替セレクタ {search_box_selector_general} で試します。")
                # 代替セレクタで試す
                search_box = page.locator(search_box_selector_general)
                search_box.wait_for(state="visible", timeout=5000) # 5秒待機
                logging.info(f"検索ボックス ({search_box_selector_general}) を見つけました。")

            # 検索語を入力
            search_term = "堺市 補助金"
            logging.info(f"検索ボックスに '{search_term}' を入力します...")
            search_box.fill(search_term)

            # Enterキーを押して検索を実行
            logging.info("Enterキーを押して検索を実行します...")
            search_box.press("Enter")

            # 検索結果が表示されるまで待機
            # 結果ページの主要なリンクのコンテナや特定の要素を待つ
            # DuckDuckGoの結果リンクは data-testid="result-title-a" を持つことが多い
            result_selector = 'a[data-testid="result-title-a"]'
            logging.info("検索結果の読み込みを待機しています...")
            page.wait_for_selector(result_selector, state="visible", timeout=15000) # 15秒待機
            logging.info("検索結果が表示されました。")

            # 結果リンクのhrefを取得
            logging.info("結果リンクのhrefを取得します...")
            # result_selectorに一致するすべての要素を取得
            link_elements = page.locator(result_selector)

            # 各要素からhref属性を抽出
            hrefs = []
            count = link_elements.count()
            logging.info(f"{count} 件の結果リンクが見つかりました。")
            for i in range(count):
                element = link_elements.nth(i)
                href = element.get_attribute('href')
                if href: # href属性が存在する場合のみ追加
                    hrefs.append(href)

            # 取得したhrefの配列をログに出力
            logging.info("取得したhref一覧:")
            # 1つずつ改行して表示する場合
            # for i, href_val in enumerate(hrefs):
            #     logging.info(f"  {i+1}: {href_val}")
            # 配列として表示する場合
            logging.info(hrefs)

            # 少し待機して結果を確認できるようにする（任意）
            logging.info("処理が完了しました。5秒後にブラウザを閉じます。")
            time.sleep(5)

        except Exception as e:
            logging.error(f"エラーが発生しました: {e}")
            # エラー発生時も確認のため少し待機（任意）
            time.sleep(5)

        finally:
            # ブラウザを閉じる
            browser.close()
            logging.info("ブラウザを閉じました。")

if __name__ == "__main__":
    main()