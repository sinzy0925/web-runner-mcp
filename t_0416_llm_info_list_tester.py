# llm_info_list_tester.py:

import os
import google.generativeai as genai
import pathlib
from dotenv import load_dotenv
import logging
import traceback
import time
import json

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


def get_element_info_list(html_content: str, instruction: str, max_retries=3, retry_delay=5) -> str | None:
    """
    与えられたHTMLコンテンツと自然言語指示に基づき、Gemini APIを使用して
    要素特定情報の候補リストをJSON文字列形式で生成します。

    Args:
        html_content (str): 解析対象のHTMLコンテンツ。
        instruction (str): 抽出したい要素を示す自然言語の指示。
        max_retries (int): APIエラー時の最大リトライ回数。
        retry_delay (int): リトライ間の待機時間（秒）。

    Returns:
        str | None: 生成されたJSON文字列 (リスト形式)、または失敗時にNone。
    """
    logging.info("-" * 30)
    logging.info(f"指示に基づいて要素情報リストを生成中: '{instruction}'")
    logging.info(f"HTMLコンテンツ長: {len(html_content)} 文字")

    # --- プロンプト設計 (複数候補リスト生成用) ---
    prompt = f"""
あなたはHTMLコンテンツとユーザーの指示を理解するエキスパートWebアナリストです。
以下のHTMLコンテンツとユーザーの指示を読み、指示された要素を特定するために**考えられる複数の方法**とその**有効性の確信度（high, medium, low）**をJSON形式でリストアップしてください。

# HTMLコンテンツ
```html
{html_content}

ユーザーの指示

「{instruction}」

あなたのタスク

ユーザーの指示に合致する要素を特定するために、以下の情報をできる限り詳細に、かつ複数の可能性を考慮して抽出・推測し、それぞれの確信度と共にJSONオブジェクトの配列（リスト）として出力してください。リストは確信度の高い順に並べてください。

抽出/推測する情報 (各オブジェクト内):

type: 特定方法の種類 (文字列: "role_and_text", "test_id", "text_exact", "placeholder", "aria_label", "css_selector_candidate", "nth_child" のいずれか)

value: 特定に必要な値 (文字列、数値、またはnull)

name: ロールと組み合わせる名前（テキスト） (type="role_and_text" の場合のみ、文字列またはnull)

level: heading ロールのレベル (type="role_and_text", role="heading" の場合のみ、数値またはnull)

common_selector: N番目指定の際の共通CSSセレクター (type="nth_child" の場合のみ、文字列またはnull)

index: N番目指定の際の0始まりインデックス (type="nth_child" の場合のみ、数値またはnull)

confidence: この特定方法が有効である確信度 (文字列: "high", "medium", "low" のいずれか)

出力JSONフォーマット (オブジェクトの配列、確信度順):

[
  {{
    "type": "...",
    "value": "...",
    "name": "...", // typeに応じて存在、なければnull
    "level": ..., // typeに応じて存在、なければnull
    "common_selector": "...", // typeに応じて存在、なければnull
    "index": ..., // typeに応じて存在、なければnull
    "confidence": "high | medium | low"
  }},
  {{ ... }} // 他の候補
]

該当する要素が見つからない、または特定できないと判断した場合は、空の配列 [] を返してください。
重要: 出力は上記のJSON配列形式のみとし、他の説明やマークダウンは含めないでください。

出力JSON:""" # 出力を促す接尾辞 (マークダウンは不要)

    generated_json_string = None
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
            # ★★★ JSONモードを試す ★★★
            # if hasattr(genai.GenerationConfig, 'response_mime_type') and MODEL_NAME in ["gemini-1.5-pro-latest", "gemini-1.5-flash-latest"]:
            #      generation_config = genai.GenerationConfig(
            #          response_mime_type="application/json",
            #      )
            #      response = model.generate_content(prompt, generation_config=generation_config, safety_settings=safety_settings)
            #      logging.info("Gemini APIをJSONモードで呼び出しました。")
            # else:
            logging.info("Gemini APIを通常モードで呼び出します。") # 現状は通常モードでテスト
            response = model.generate_content(prompt, safety_settings=safety_settings)

            end_time = time.monotonic()
            logging.info(f"Gemini API応答受信。処理時間: {end_time - start_time:.2f} 秒")

            # --- 応答の解析 ---
            if response.candidates and response.text:
                raw_response_text = response.text.strip()
                logging.debug(f"生の応答テキスト:\n{raw_response_text}")

                # マークダウンのコードブロックを除去 (```json ... ``` や ``` ... ```)
                json_text = raw_response_text
                if json_text.startswith("```json") and json_text.endswith("```"):
                    json_text = json_text[len("```json"): -len("```")].strip()
                elif json_text.startswith("```") and json_text.endswith("```"):
                    json_text = json_text[len("```"): -len("```")].strip()

                # JSONリストとしてパース可能か試す
                try:
                    parsed_json = json.loads(json_text)
                    if isinstance(parsed_json, list):
                        # 成功！ 整形して文字列として保持
                        generated_json_string = json.dumps(parsed_json, indent=2, ensure_ascii=False)
                        logging.info("JSONリスト形式の情報を正常に抽出・解析しました。")
                        break # 成功したのでリトライループを抜ける
                    else:
                        logging.warning(f"GeminiがJSONリストでない形式を返しました: {type(parsed_json)}")
                        generated_json_string = f'{{"error": "LLM did not return a list", "raw_response": {json.dumps(raw_response_text)}}}' # エラー情報
                except json.JSONDecodeError as e:
                    logging.warning(f"Geminiの応答JSONの解析に失敗しました: {e}")
                    logging.warning(f"解析しようとしたテキスト:\n{json_text}")
                    generated_json_string = f'{{"error": "JSONDecodeError", "raw_response": {json.dumps(raw_response_text)}}}' # エラー情報
            else:
                logging.warning("Gemini APIからの応答が空か、候補がありませんでした。")
                # ... (エラー詳細取得の試み) ...
                generated_json_string = f'{{"error": "Empty response from API"}}' # エラー情報

        except Exception as e:
            logging.error(f"Gemini API呼び出し中にエラーが発生しました (試行 {attempt + 1}): {e}")
            logging.debug(traceback.format_exc())
            generated_json_string = f'{{"error": "API call exception", "details": "{e}"}}' # エラー情報
            if attempt == max_retries - 1:
                logging.error("最大リトライ回数に達しました。")
                return generated_json_string # 最後のエラー情報を返す

        # リトライが必要な場合
        if attempt < max_retries - 1:
            is_likely_error = False
            try:
                # 簡単なエラーチェック
                if generated_json_string and json.loads(generated_json_string).get("error"):
                    is_likely_error = True
            except:
                is_likely_error = True # パースできない場合もエラーとみなす

            if is_likely_error:
                logging.warning(f"{retry_delay}秒待機してリトライします...")
                time.sleep(retry_delay)
            else:
                break # 解析エラーでもなく、中身があれば（空リスト[]含む）、成功とみなし抜ける

    # --- 最終的な結果を返す ---
    if generated_json_string:
        logging.info("LLMからの応答を処理しました。")
        return generated_json_string
    else:
        logging.error("要素情報リストの生成に失敗しました。")
        return f'{{"error": "Extraction failed after retries"}}'


TEST_CASES = [
{
"name": "Google Search Box",
"html_file": "real_html_outputs/www_google_co_jp.html",
"instruction": "検索キーワードを入力するテキストエリア",
# 期待値は今回は直接比較しないが、参考として残す
"expected_info_comment": { "role": "textbox", "tag_name": "textarea", "aria_label": "検索" }
},
{
"name": "Google Search Button",
"html_file": "real_html_outputs/www_google_co_jp.html",
"instruction": "「Google 検索」ボタン",
"expected_info_comment": { "text": "Google 検索", "role": "button", "tag_name": "input" }
},
{
"name": "Google Result 1 Link",
"html_file": "real_html_outputs/www_google_com_search_q-mastra.html",
"instruction": "最初の検索結果のタイトルへのリンク",
"expected_info_comment": { "text": "検索結果タイトル 1", "role": "link", "tag_name": "a", "index": 0 }
},
{
"name": "Google Result 2 Link",
"html_file": "real_html_outputs/www_google_com_search_q-mastra.html",
"instruction": "2番目の検索結果のリンク",
"expected_info_comment": { "text": "Example Result 2", "role": "link", "tag_name": "a", "index": 1 }
},
{
"name": "Google Next Page Link",
"html_file": "real_html_outputs/www_google_com_search_q-mastra.html",
"instruction": "「次へ」のページネーションリンク",
"expected_info_comment": { "text": "次へ", "role": "link", "tag_name": "a", "other_attributes": {"id": "pnnext"} }
},
{
"name": "News Article Title",
"html_file": "real_html_outputs/chatgpt-lab_com_n_nc1a8b4bb4911.html",
"instruction": "記事の見出し (タイトル)",
"expected_info_comment": { "text": "今日のビッグニュース", "role": "heading", "tag_name": "h1" }
},
{
"name": "News Share Button",
"html_file": "real_html_outputs/chatgpt-lab_com_n_nc1a8b4bb4911.html",
"instruction": "共有ボタン",
"expected_info_comment": { "text": "共有", "role": "button", "tag_name": "button", "test_id": "share-button" }
},
{
"name": "News Non-existent Element",
"html_file": "real_html_outputs/chatgpt-lab_com_n_nc1a8b4bb4911.html",
"instruction": "記事へのコメント投稿フォーム",
"expected_info_comment": "[]" # 空のリストが返ることを期待
},
]

#--- テスト用HTMLファイルを作成するヘルパー関数 (変更なし) ---
#info_extractor_tester.py からコピー

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

    #with open(html_dir / "google_search.html", "w", encoding="utf-8") as f: f.write(google_html)
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
    
    #with open(html_dir / "news_article.html", "w", encoding="utf-8") as f: f.write(news_html)
    
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
    #with open(html_dir / "google_results.html", "w", encoding="utf-8") as f: f.write(google_results_html)
    #logging.info(f"テスト用HTMLファイルを '{html_dir}' に作成/更新しました。")

    #--- テスト実行関数 (出力を評価する形式に変更) ---

def run_all_tests():
    """定義されたすべてのテストケースを実行し、LLMの出力を表示"""
    logging.info(">>> LLM 要素情報リスト抽出テストを開始します <<<")
    create_test_html_files()

    passed_count = 0
    failed_count = 0
    total_count = len(TEST_CASES)
    results_summary = []

    for i, case in enumerate(TEST_CASES):
        print("\n" + "=" * 50)
        logging.info(f"テストケース {i+1}/{total_count}: {case['name']}")
        logging.info(f"指示: {case['instruction']}")
        # logging.info(f"期待値コメント: {case.get('expected_info_comment', 'N/A')}") # 参考情報として

        html_path = pathlib.Path(case['html_file'])
        if not html_path.exists():
            logging.error(f"  エラー: HTMLファイルが見つかりません: {html_path}")
            failed_count += 1
            results_summary.append({"name": case['name'], "status": "FAIL (HTML not found)"})
            continue

        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            logging.error(f"  エラー: HTMLファイルの読み込みに失敗しました: {e}")
            failed_count += 1
            results_summary.append({"name": case['name'], "status": f"FAIL (HTML read error: {e})"})
            continue

        # 要素情報リスト生成関数を呼び出し
        generated_json_string = get_element_info_list(html_content, case['instruction'])

        # --- 出力の評価 ---
        status = "FAIL"
        parsed_output = None
        evaluation_notes = ""

        if generated_json_string:
            try:
                parsed_output = json.loads(generated_json_string)
                if isinstance(parsed_output, list):
                    if case['name'] == "News Non-existent Element" and len(parsed_output) == 0:
                        status = "PASS"
                        evaluation_notes = "期待通り空のリストが返されました。"
                    elif len(parsed_output) > 0:
                        # 簡単なチェック：リスト内の最初の要素に必要なキーがあるか
                        first_item = parsed_output[0]
                        if isinstance(first_item, dict) and "type" in first_item and "value" in first_item and "confidence" in first_item:
                            status = "PASS (Format OK)" # ★フォーマットOKを成功とみなす
                            evaluation_notes = f"{len(parsed_output)} 件の候補が生成されました。"
                        else:
                            evaluation_notes = "リスト内のオブジェクトの形式が不正です。"
                    elif case['name'] != "News Non-existent Element" and len(parsed_output) == 0:
                        evaluation_notes = "要素が見つかるはずが、空のリストが返されました。"
                    else: # Non-existent でないのに空リストの場合
                        evaluation_notes = "予期せず空のリストが返されました。"

                elif isinstance(parsed_output, dict) and "error" in parsed_output:
                    status = "FAIL (LLM Error)"
                    evaluation_notes = f"LLMがエラーを返しました: {parsed_output['error']}"
                else:
                    evaluation_notes = "期待しないJSON形式（リストではない）が返されました。"
            except json.JSONDecodeError:
                evaluation_notes = "返された文字列は有効なJSONではありません。"
            except Exception as e:
                evaluation_notes = f"出力の評価中に予期せぬエラー: {e}"
        else:
            evaluation_notes = "LLMから応答がありませんでした。"

        # ログ出力
        logging.info(f"  ステータス: {status}")
        if evaluation_notes:
            logging.info(f"  評価ノート: {evaluation_notes}")
        logging.info(f"  LLM出力 (整形後):\n{generated_json_string}") # 生成されたJSON文字列をそのまま表示

        results_summary.append({"name": case['name'], "status": status, "notes": evaluation_notes})
        if status.startswith("PASS"):
            passed_count += 1
        else:
            failed_count += 1
        print("-" * 30)

    print("\n" + "=" * 50)
    logging.info(">>> 全テストケース完了 <<<")
    logging.info(f"結果: {passed_count} / {total_count} 件成功 (フォーマットOK)")
    if failed_count > 0:
        logging.warning(f"{failed_count} 件失敗またはフォーマット不正。")
        logging.warning("--- 失敗/不正ケース詳細 ---")
        for result in results_summary:
            if not result['status'].startswith("PASS"):
                logging.warning(f"  Case: {result['name']} ({result['status']}) - Notes: {result['notes']}")
    print("=" * 50)

if __name__ == "__main__":
    run_all_tests()

