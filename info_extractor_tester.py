# info_extractor_tester.py:

import os
import google.generativeai as genai
import pathlib
from dotenv import load_dotenv
import logging
import traceback
import time
import json # JSONを扱うためにインポート

# --- 初期設定 ---
load_dotenv()
api_key = os.getenv('API_KEY')
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

if not api_key:
    logging.error("API_KEYが環境変数に見つかりません。.envファイルを確認してください。")
    exit()

try:
    genai.configure(api_key=api_key, transport='rest')
except Exception as e:
    logging.error(f"Geminiの設定中にエラーが発生しました: {e}")
    exit()

# --- Geminiモデル設定 ---
# ★★★ 必要に応じてモデル名を変更 ★★★
MODEL_NAME = "gemini-2.0-flash" # または "gemini-1.5-flash-latest" など
try:
    model = genai.GenerativeModel(model_name=MODEL_NAME)
    logging.info(f"Geminiモデル '{MODEL_NAME}' を初期化しました。")
except Exception as e:
    logging.error(f"指定されたGeminiモデル '{MODEL_NAME}' の初期化に失敗しました: {e}")
    exit()


def extract_element_info(html_content: str, instruction: str, max_retries=3, retry_delay=5) -> dict | None:
    """
    与えられたHTMLコンテンツと自然言語指示に基づき、Gemini APIを使用して
    要素特定情報をJSON形式で抽出します。

    Args:
        html_content (str): 解析対象のHTMLコンテンツ。
        instruction (str): 抽出したい要素を示す自然言語の指示。
        max_retries (int): APIエラー時の最大リトライ回数。
        retry_delay (int): リトライ間の待機時間（秒）。

    Returns:
        dict | None: 抽出された要素情報の辞書、または失敗時にNone。
    """
    logging.info("-" * 30)
    logging.info(f"指示に基づいて要素情報を抽出中: '{instruction}'")
    logging.info(f"HTMLコンテンツ長: {len(html_content)} 文字")

    # --- プロンプト設計 (要素情報抽出用) ---
    prompt = f"""
あなたはHTMLコンテンツとユーザーの指示を理解するエキスパートです。
以下のHTMLコンテンツとユーザーの指示を読み、指示された要素を特定するために最も役立つ情報をJSON形式で抽出してください。

# HTMLコンテンツ
```html
{html_content}

ユーザーの指示

「{instruction}」

あなたのタスク

ユーザーの指示に合致する要素を特定するための情報を以下のJSONフォーマットに従って抽出してください。
可能な限り多くのフィールドを埋めてください。特にtext, role, test_idがあれば重要です。
もし要素がリストの一部（例：検索結果リストのN番目）だと判断でき、順序が重要そうな場合は common_selector と index (0始まり) を設定してください。
該当する要素が見つからない、または指示が不明瞭な場合は、 "error": "Element not found or instruction unclear" というキーを持つJSONオブジェクトを返してください。

出力JSONフォーマット (この形式のJSONオブジェクトのみを出力すること)
{{
  "text": "要素に含まれる完全なテキスト or 部分テキスト (もしあれば、String)",
  "role": "要素のARIAロール (例: 'button', 'link', 'textbox', もしあれば、String)",
  "test_id": "data-testidなどのテスト用ID (もしあれば、String)",
  "placeholder": "プレースホルダー属性の値 (もしあれば、String)",
  "aria_label": "aria-label属性の値 (もしあれば、String)",
  "common_selector": "もし要素がリストの一部なら、そのリスト要素全体に共通するCSSセレクター (例: '#rso .g', なければnull)",
  "index": "リスト内の順序 (0始まりの整数、common_selectorとセットで使う、なければnull)",
  "tag_name": "要素のHTMLタグ名 (例: 'a', 'button', 'input', String)",
  "other_attributes": {{ "属性名": "属性値", ... }} // 上記以外で特定に役立ちそうな属性 (Object, なければ空の{{}})
}}
出力JSON:""" # 出力を促す接尾辞 (マークダウンは不要)
    extracted_info = None
    for attempt in range(max_retries):
        try:
            logging.info(f"Gemini APIへのリクエスト送信 (試行 {attempt + 1}/{max_retries})...")
            start_time = time.monotonic()

            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]
            # ★★★ JSONモードを試す (利用可能なモデルか確認が必要) ★★★
            # モデルが対応していれば、より安定してJSONを出力する可能性がある
            # 対応していない場合は通常の generate_content を使う
            if hasattr(genai.GenerationConfig, 'response_mime_type') and MODEL_NAME in ["gemini-1.5-pro-latest", "gemini-1.5-flash-latest"]: # 例
                generation_config = genai.GenerationConfig(
                    response_mime_type="application/json",
                    # temperature=0.1 # 必要なら設定
                )
                response = model.generate_content(
                    prompt,
                    generation_config=generation_config,
                    safety_settings=safety_settings
                )
                logging.info("Gemini APIをJSONモードで呼び出しました。")
            else:
                # JSONモード非対応または不明なモデルの場合は通常モード
                logging.info("Gemini APIを通常モードで呼び出します (JSONモード非対応の可能性)。")
                response = model.generate_content(
                    prompt,
                    safety_settings=safety_settings
                )


            end_time = time.monotonic()
            logging.info(f"Gemini API応答受信。処理時間: {end_time - start_time:.2f} 秒")

            # --- 応答の解析 ---
            if response.candidates and response.text:
                raw_response_text = response.text.strip()
                logging.debug(f"生の応答テキスト: {raw_response_text}")
                # 応答がマークダウンのコードブロックで囲まれている場合、中身を抽出
                if raw_response_text.startswith("```json") and raw_response_text.endswith("```"):
                    json_text = raw_response_text[len("```json"): -len("```")].strip()
                elif raw_response_text.startswith("```") and raw_response_text.endswith("```"):
                    json_text = raw_response_text[len("```"): -len("```")].strip()
                else:
                    json_text = raw_response_text # マークダウンがない場合はそのまま

                try:
                    extracted_info = json.loads(json_text)
                    # 簡単なバリデーション (辞書型かどうか)
                    if isinstance(extracted_info, dict):
                        logging.info("JSON形式の情報を正常に抽出・解析しました。")
                        break # 成功したのでリトライループを抜ける
                    else:
                        logging.warning(f"GeminiがJSON形式でない応答を返しました: {type(extracted_info)}")
                        extracted_info = {"error": "Invalid format from LLM", "raw_response": json_text}
                        # 成功ではないのでリトライを続ける可能性がある

                except json.JSONDecodeError as e:
                    logging.warning(f"Geminiの応答JSONの解析に失敗しました: {e}")
                    logging.warning(f"解析しようとしたテキスト: {json_text}")
                    extracted_info = {"error": "JSONDecodeError from LLM response", "raw_response": json_text}
                    # 失敗したのでリトライする
            else:
                logging.warning("Gemini APIからの応答が空か、候補がありませんでした。")
                logging.debug(f"詳細な応答: {response}")
                error_details = "Unknown error or empty response"
                # ... (エラー詳細取得の試み - selector_tester.py と同様) ...
                extracted_info = {"error": error_details}
                # 失敗したのでリトライする

        except Exception as e:
            logging.error(f"Gemini API呼び出し中にエラーが発生しました (試行 {attempt + 1}): {e}")
            logging.debug(traceback.format_exc())
            extracted_info = {"error": f"API call failed: {e}"}
            if attempt == max_retries - 1:
                logging.error("最大リトライ回数に達しました。")
                return extracted_info # 最後のエラー情報を返す

        # リトライが必要な場合 (ループの最後で判定)
        if attempt < max_retries - 1 and (extracted_info is None or extracted_info.get("error")):
            logging.warning(f"{retry_delay}秒待機してリトライします...")
            time.sleep(retry_delay)
        elif extracted_info is not None and not extracted_info.get("error"):
            break # 成功したので抜ける

    # --- 最終的な結果を返す ---
    if extracted_info and not extracted_info.get("error"):
        # 不要なキーやnull値を除去するなどの後処理をここで行っても良い
        logging.info(f"抽出された情報: {json.dumps(extracted_info, ensure_ascii=False, indent=2)}")
        return extracted_info
    else:
        logging.error("要素情報の抽出に失敗しました。")
        # 失敗した場合も、最後のエラー情報を含む辞書を返す
        return extracted_info if extracted_info else {"error": "Extraction failed after retries"}
#--- テストケース定義 (期待値をJSON形式に変更) ---

TEST_CASES = [
{
"name": "Google Search Box",
"html_file": "test_html/google_search.html",
"instruction": "検索キーワードを入力するテキストエリア",
"expected_info": { # ★期待する情報
"role": "textbox", # textarea は textbox ロールを持つことが多い
"tag_name": "textarea",
"aria_label": "検索",
"other_attributes": {"title": "検索", "id": "APjFqb", "class": "gLFyf"} # idもotherに含めるか要検討
}
},
{
"name": "Google Search Button",
"html_file": "test_html/google_search.html",
"instruction": "「Google 検索」ボタン",
"expected_info": {
"text": "Google 検索", # value属性が表示テキストになっている
"role": "button",
"tag_name": "input",
"aria_label": "Google 検索",
"other_attributes": {"name": "btnK", "class": "gNO89b", "type": "submit"}
}
},
{
"name": "Google Result 1 Link",
"html_file": "test_html/google_results.html",
"instruction": "最初の検索結果のタイトルへのリンク",
"expected_info": {
"text": "検索結果タイトル 1", # リンク内のテキスト
"role": "link",
"tag_name": "a",
"common_selector": "#rso .g", # または "#rso > div.g" など、結果ブロックの共通セレクター
"index": 0,
"other_attributes": {"href": "https://example-result1.com/"}
}
},
{
"name": "Google Result 2 Link", # ★2番目のテストケースを追加
"html_file": "test_html/google_results.html",
"instruction": "2番目の検索結果のリンク",
"expected_info": {
"text": "Example Result 2",
"role": "link",
"tag_name": "a",
"common_selector": "#rso .g", # 1番目と同じ共通セレクター
"index": 1, # ★index が 1 になる
"other_attributes": {"href": "https://example-result2.net/"}
}
},
{
"name": "Google Next Page Link",
"html_file": "test_html/google_results.html",
"instruction": "「次へ」のページネーションリンク",
"expected_info": {
"text": "次へ",
"role": "link",
"tag_name": "a",
"other_attributes": {"id": "pnnext"}
}
},
{
"name": "News Article Title",
"html_file": "test_html/news_article.html",
"instruction": "記事の見出し (タイトル)",
"expected_info": {
"text": "今日のビッグニュース",
"role": "heading", # h1 は heading ロール
"tag_name": "h1",
"other_attributes": {"class": "article-title main", "itemprop": "headline"}
}
},
{
"name": "News Share Button",
"html_file": "test_html/news_article.html",
"instruction": "共有ボタン",
"expected_info": {
"text": "共有",
"role": "button",
"tag_name": "button",
"test_id": "share-button", # data-testid を抽出してほしい
"other_attributes": {"class": "share-button"}
}
},
{
"name": "News Non-existent Element",
"html_file": "test_html/news_article.html",
"instruction": "記事へのコメント投稿フォーム",
"expected_info": { # ★期待値としてエラーを示す
"error": "Element not found or instruction unclear"
}
},
]

#--- テスト用HTMLファイルを作成するヘルパー関数 (変更なし) ---
#selector_tester.py からコピー

def create_test_html_files():
    """テスト用のHTMLファイルを test_html ディレクトリに作成"""
    html_dir = pathlib.Path("test_html")
    html_dir.mkdir(exist_ok=True)
    # --- Google検索トップページ ---
    google_html = """
    <!DOCTYPE html><html><head><title>Google</title></head><body>
    <form action="/search">
    <textarea class="gLFyf" id="APjFqb" title="検索" aria-label="検索" role="textbox"></textarea>
    <div><center>
    <input class="gNO89b" value="Google 検索" aria-label="Google 検索" name="btnK" role="button" type="submit" data-ved="0ahUKEwi...">
    <input class="RNmpXc" value="I'm Feeling Lucky" aria-label="I'm Feeling Lucky" name="btnI" role="button" type="submit" data-ved="0ahUKEwi...">
    </center></div>
    </form></body></html>
    """
    with open(html_dir / "google_search.html", "w", encoding="utf-8") as f: f.write(google_html)
    # --- 一般的なニュース記事ページ ---
    news_html = """
    <!DOCTYPE html><html><head><title>ニュース記事</title></head><body>
    <header>サイトヘッダー</header><main><article>
    <h1 class="article-title main" itemprop="headline" role="heading" aria-level="1">今日のビッグニュース</h1>
    <div class="content body" role="article">
    <p>記事の本文がここに続きます...</p><p>さらなる段落。</p>
    <a href="/login" id="login-link" style="color: blue;" role="link">続きを読むにはログイン</a>
    </div><footer>
    <button class="share-button" data-testid="share-button" role="button">共有</button>
    <button role="button">コメント</button>
    </footer></article></main>
    <footer>サイトフッター</footer></body></html>
    """
    with open(html_dir / "news_article.html", "w", encoding="utf-8") as f: f.write(news_html)
    # --- Google検索結果ページ風HTML ---
    google_results_html = """
    <!DOCTYPE html><html><head><title>検索結果 - Google Search</title></head><body><div id="main"><div id="cnt"><div id="rcnt"><div id="center_col"><div id="res"><div id="search"><div id="rso">
    <!-- 検索結果 1 -->
    <div class="g Ww4FFb vt6azd tF2Cxc asEBEc"><div><div class="kb0PBd cvP2Ce jGGQ5e"><div class="yuRUbf"><div><span>
    <a href="https://example-result1.com/" role="link"><br><h3 class="LC20lb MBeuO DKV0Md" role="heading" aria-level="3">検索結果タイトル 1</h3><div><cite>example-result1.com</cite></div></a>
    </span></div></div></div><div class="VwiC3b"><span>検索結果の説明文1がここに入ります。</span></div></div></div>
    <!-- 検索結果 2 -->
    <div class="g Ww4FFb vt6azd tF2Cxc asEBEc"><div><div class="kb0PBd cvP2Ce jGGQ5e"><div class="yuRUbf">
    <a href="https://example-result2.net/" role="link"><h3 class="LC20lb MBeuO DKV0Md" role="heading" aria-level="3">Example Result 2</h3><cite>example-result2.net</cite></a>
    </div></div><div class="VwiC3b"><span>Description for the second result goes here.</span></div></div></div>
    </div><div id="botstuff"><div class="navigation">
    <a id="pnnext" href="/search?q=..." role="link"><span style="display:block;margin-left:53px">次へ</span></a>
    </div></div></div></div></div></div></div></div></body></html>
    """
    with open(html_dir / "google_results.html", "w", encoding="utf-8") as f: f.write(google_results_html)
    logging.info(f"テスト用HTMLファイルを '{html_dir}' に作成/更新しました。")

    #--- テスト実行関数 (評価ロジックを修正) ---

def run_all_tests():
    """定義されたすべてのテストケースを実行"""
    logging.info(">>> 要素情報抽出テストを開始します <<<")
    create_test_html_files()

    results = {"passed": 0, "failed": 0, "total": len(TEST_CASES)}
    failed_cases = []

    for case in TEST_CASES:
        print("=" * 50)
        logging.info(f"テストケース実行: {case['name']}")
        logging.info(f"指示: {case['instruction']}")
        logging.info(f"期待値:\n{json.dumps(case['expected_info'], ensure_ascii=False, indent=2)}")

        html_path = pathlib.Path(case['html_file'])
        if not html_path.exists():
            logging.error(f"  エラー: HTMLファイルが見つかりません: {html_path}")
            results["failed"] += 1
            failed_cases.append({"name": case['name'], "reason": "HTML file not found"})
            continue

        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            logging.error(f"  エラー: HTMLファイルの読み込みに失敗しました: {e}")
            results["failed"] += 1
            failed_cases.append({"name": case['name'], "reason": f"HTML read error: {e}"})
            continue

        # 要素情報抽出関数を呼び出し
        generated_info = extract_element_info(html_content, case['instruction'])

        # --- 結果の評価 ---
        expected = case['expected_info']
        passed = False
        reason = "Mismatch" # デフォルトの失敗理由

        if generated_info is None:
            reason = "LLM call failed or returned None"
            logging.error(f"  結果: 情報抽出に失敗しました ❌")
        elif isinstance(generated_info, dict) and "error" in generated_info:
            if "error" in expected and generated_info["error"] == expected["error"]:
                passed = True
                reason = "Expected error matched"
                logging.info(f"  結果: 期待通りエラーが返されました ✅ ({generated_info['error']})")
            else:
                reason = f"Unexpected error from LLM: {generated_info.get('error')}"
                logging.warning(f"  結果: LLMが予期しないエラーを返しました ❌ ({generated_info.get('error')})")
                logging.warning(f"    期待値: {expected}")
        elif isinstance(generated_info, dict):
            # ★★★ 期待値と生成値の比較 ★★★
            # ここでは単純に完全一致を見るが、より柔軟な比較も可能
            # 例えば、期待値に含まれるキーが生成値にも存在し、値が一致するかどうかなど
            if generated_info == expected:
                passed = True
                reason = "Exact match"
                logging.info(f"  結果: 完全に一致しました ✅")
            else:
                # 部分一致などの評価をここに入れることもできる
                # 例: 主要キー (text, role, index など) が一致するかチェック
                major_keys = ["text", "role", "index", "test_id"]
                partial_match = True
                mismatched_keys = []
                for key in major_keys:
                    if expected.get(key) != generated_info.get(key):
                        # index が両方 None の場合は一致とみなすなど、細かい調整が可能
                        if not (key == 'index' and expected.get(key) is None and generated_info.get(key) is None):
                            partial_match = False
                            mismatched_keys.append(key)

                if partial_match and not mismatched_keys: # 主要キーが一致
                    passed = True # 部分一致でも成功とみなす場合
                    reason = f"Partial match (Major keys matched)"
                    logging.info(f"  結果: 主要キーが一致しました (部分成功とみなす) ✅")
                    logging.debug(f"    生成値:\n{json.dumps(generated_info, ensure_ascii=False, indent=2)}")
                else:
                    reason = f"Mismatch (Keys: {mismatched_keys})"
                    logging.warning(f"  結果: 不一致 ❌ (Mismatched keys: {mismatched_keys})")
                    logging.warning(f"    期待値:\n{json.dumps(expected, ensure_ascii=False, indent=2)}")
                    logging.warning(f"    生成値:\n{json.dumps(generated_info, ensure_ascii=False, indent=2)}")
        else:
            reason = f"Unexpected return type: {type(generated_info)}"
            logging.error(f"  結果: 予期しない型が返されました ❌ ({type(generated_info)})")

        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1
            failed_cases.append({
                "name": case['name'],
                "reason": reason,
                "expected": expected,
                "generated": generated_info
            })
        print("-" * 30)

    print("=" * 50)
    logging.info(">>> 全テストケース完了 <<<")
    logging.info(f"結果: {results['passed']} / {results['total']} 件成功")
    if results['failed'] > 0:
        logging.warning(f"{results['failed']} 件失敗しました。")
        logging.warning("--- 失敗したケース詳細 ---")
        for failure in failed_cases:
            logging.warning(f"  Case: {failure['name']} ({failure['reason']})")
            # logging.warning(f"    Expected: {json.dumps(failure['expected'], ensure_ascii=False, indent=2)}")
            # logging.warning(f"    Generated: {json.dumps(failure['generated'], ensure_ascii=False, indent=2)}")
    print("=" * 50)


if __name__ == "__main__":
    run_all_tests()

