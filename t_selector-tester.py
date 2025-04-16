import pathlib
from dotenv import load_dotenv
import logging
import traceback
import time # 実行時間計測用 (任意)
import os
import sys
import site
import os # os モジュールもインポート

# --- ▼▼▼ ここから追加 ▼▼▼ ---
print("--- Python Executable ---")
print(sys.executable) # どのPythonが実行されているか確認
print("--- sys.path ---")
for p in sys.path:
    print(p)
print("--- site packages ---")
# site.getsitepackages() はリストを返すことがあるのでループで表示
site_packages_paths = site.getsitepackages()
if isinstance(site_packages_paths, list):
    for p in site_packages_paths:
        print(p)
else:
    print(site_packages_paths) # リストでない場合はそのまま表示
print("--- user site packages ---")
print(site.getusersitepackages())
print("--- CWD ---")
print(os.getcwd()) # 現在の作業ディレクトリも確認
print("-" * 20)

import google.generativeai as genai

# --- 初期設定 ---
# .envファイルから環境変数を読み込む
load_dotenv()
api_key = os.getenv('API_KEY')

# ログの設定
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# APIキーのチェック
if not api_key:
    logging.error("API_KEYが環境変数に見つかりません。.envファイルを確認してください。")
    exit()

# Geminiクライアントの設定 (Rest Transport推奨)
try:
    genai.configure(api_key=api_key, transport='rest')
except Exception as e:
    logging.error(f"Geminiの設定中にエラーが発生しました: {e}")
    exit()

# --- Geminiモデル設定 ---
# ユーザーが実際に使用するモデルを指定 (Gemini 1.5 Proを想定)
MODEL_NAME = "gemini-2.0-flash" # 必要に応じて利用可能なモデル名に変更してください
try:
    model = genai.GenerativeModel(model_name=MODEL_NAME)
    logging.info(f"Geminiモデル '{MODEL_NAME}' を初期化しました。")
except Exception as e:
    logging.error(f"指定されたGeminiモデル '{MODEL_NAME}' の初期化に失敗しました: {e}")
    exit()


def get_selector_from_html(html_content: str, instruction: str, max_retries=3, retry_delay=5) -> str | None:
    """
    与えられたHTMLコンテンツと自然言語指示に基づき、Gemini APIを使用して
    対応するCSSセレクターを生成します。

    Args:
        html_content (str): 解析対象のHTMLコンテンツ。
        instruction (str): 抽出したい要素を示す自然言語の指示。
        max_retries (int): APIエラー時の最大リトライ回数。
        retry_delay (int): リトライ間の待機時間（秒）。

    Returns:
        str | None: 生成されたCSSセレクター文字列、または失敗時にNone。
    """
    #logging.info("-" * 30)
    #logging.info(f"指示に基づいてセレクターを生成中: '{instruction}'")
    logging.info(f"HTMLコンテンツ長: {len(html_content)} 文字")

    # --- プロンプト設計 ---
    # トークン数を抑えるため、HTMLは簡略化するか関連部分のみ渡すのが理想だが、
    # ここではテストとしてそのまま渡すことを想定。長すぎる場合は切り詰める必要あり。
    # MAX_HTML_LENGTH = 30000 # 例: 必要に応じて文字数制限
    # if len(html_content) > MAX_HTML_LENGTH:
    #     logging.warning(f"HTMLコンテンツが長すぎるため、先頭{MAX_HTML_LENGTH}文字に切り詰めます。")
    #     html_content = html_content[:MAX_HTML_LENGTH] + "\n... (truncated)"

    prompt = f"""
あなたは、ブラウザ(Chrome, Firefox, Edgeなど)に付属しているDevTools (開発者ツール) の要素選択機能と全く同じ挙動をするCSSセレクター生成AIです。
与えられたHTML構造に対して、最も具体的で、かつ変更に強いCSSセレクターを生成してください。
DevToolsの機能と同様に、以下の点を考慮してください。

1. 一意性: 生成されるセレクターは、与えられたHTML構造内で目的の要素を一意に識別できる必要があります。
2. 簡潔性: 必要以上に複雑なセレクターは避け、できるだけ短いセレクターを生成してください。
3. 安定性: IDセレクター、クラスセレクター、属性セレクターなどを適切に組み合わせ、HTML構造の変更に強いセレクターを作成してください。IDセレクターが存在する場合は、それを優先的に使用してください。
4. 詳細度: DevToolsが推奨するセレクターを参考に、詳細度を調整してください。
5. 擬似クラス: :hover, :activeなどの擬似クラスは、指示がない限り使用しないでください。
6. 擬似要素: ::before, ::afterなどの擬似要素は、指示がない限り使用しないでください。
7. 複合セレクター: 子セレクター (>)、隣接兄弟セレクター (+)、一般兄弟セレクター (~) などを適切に使用し、要素間の関係性を表現してください。
8. セレクターの種類: 生成するセレクターは、CSS3セレクターを基本として、最新のCSSセレクターも利用可能です。
9. 属性セレクター: 属性セレクターを用いる際は、[属性名]、[属性名="値"]、[属性名~="値"]、[属性名|="値"]、[属性名^="値"]、[属性名$="値"]、[属性名*="値"] などのオプションを、要素を一意に特定するために必要な場合にのみ使用します。
10. 指定された要素: どの要素に対してセレクターを生成するのか、明確に指示してください。必要であれば、要素の内容や属性など、要素を特定するための情報を追加してください。

# HTMLコンテンツ
```html
{html_content}
```

# ユーザーの指示
「{instruction}」に対応するCSSセレクターを見つけてください。


# 出力に関する厳密なルール
- **CSSセレクター文字列のみ**を出力してください。
- 他の説明、前置き、 ```css ... ``` のようなマークダウン修飾は**一切含めないでください**。
+ - **要素のタグ名（例: `div`, `a`, `input`, `button`）** を含めてください。
- 可能な限り、ID属性 (`#id`) や一意性の高い属性（例: `data-testid`）を持つセレクターを優先してください。
- 複数の要素が該当する場合は、指示内容から最も妥当と思われる最初の要素を対象とするセレクターを生成してください。
- 該当する要素が見つからない、または一意に特定できない場合は、文字列 `SELECTOR_NOT_FOUND` とだけ出力してください。

CSSセレクター:""" 
    #print(prompt)

    # 出力を促す接尾辞


    generated_selector = None
    for attempt in range(max_retries):
        try:
            logging.info(f"Gemini APIへのリクエスト送信 (試行 {attempt + 1}/{max_retries})...")
            start_time = time.monotonic()

            # generate_content を使用 (ストリーミングは不要)
            # Safety Settingsを調整する必要があるかもしれない（HTMLコンテンツによるブロック回避）
            # 参考: https://ai.google.dev/gemini-api/docs/safety-settings
            safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
            ]

            response = model.generate_content(
                prompt,
                safety_settings=safety_settings,
                # generation_config = genai.GenerationConfig(temperature=0.1) # 必要なら温度などを調整
                )

            end_time = time.monotonic()
            logging.info(f"Gemini API応答受信。処理時間: {end_time - start_time:.2f} 秒")

            # --- 応答の解析 ---
            # response.text で結果を取得するのが最もシンプル
            if response.candidates and response.text:
                # 前後の空白やマークダウンを除去する処理を追加
                raw_selector = response.text.strip()
                # ```css ... ``` や ``` ... ``` のようなマークダウンを除去
                if raw_selector.startswith("```") and raw_selector.endswith("```"):
                    raw_selector = raw_selector.split('\n', 1)[1].rsplit('\n', 1)[0].strip()
                # css というプレフィックスを除去 (小文字)
                if raw_selector.lower().startswith("css"):
                    raw_selector = raw_selector[3:].strip()
                # セレクターとして基本的な形式か確認（簡易）
                if raw_selector and (raw_selector.startswith(('#', '.', '[')) or raw_selector[0].isalpha()):
                    generated_selector = raw_selector
                elif raw_selector == "SELECTOR_NOT_FOUND":
                    generated_selector = "SELECTOR_NOT_FOUND"
                else:
                    logging.warning(f"Geminiが予期しない形式の応答を返しました: '{raw_selector}'")
                    generated_selector = None # 不正な形式はNone扱い
            else:
                # エラーやブロックなどの詳細情報をログに出力
                logging.warning("Gemini APIからの応答が空か、候補がありませんでした。")
                logging.debug(f"詳細な応答: {response}")
                # 応答から安全なエラー情報を取得する試み
                error_details = "Unknown error or empty response"
                if response.prompt_feedback:
                    error_details = f"Prompt feedback: {response.prompt_feedback}"
                elif response.candidates and response.candidates[0].finish_reason:
                    error_details = f"Finish reason: {response.candidates[0].finish_reason}"
                logging.warning(f"詳細: {error_details}")
                generated_selector = None

            # 有効な応答が得られたらリトライループを抜ける
            if generated_selector is not None:
                break

        except Exception as e:
            logging.error(f"Gemini API呼び出し中にエラーが発生しました (試行 {attempt + 1}): {e}")
            logging.debug(traceback.format_exc())
            if attempt == max_retries - 1:
                logging.error("最大リトライ回数に達しました。")
                return None # 最大リトライ回数に達したら諦める

        logging.warning(f"{retry_delay}秒待機してリトライします...")
        time.sleep(retry_delay)

    # --- 結果の最終処理 ---
    if generated_selector == "SELECTOR_NOT_FOUND":
        logging.warning("Geminiによると、指示に合致するセレクターは見つかりませんでした。")
        return None
    elif generated_selector:
        logging.info(f"生成されたセレクター: '{generated_selector}'")
        return generated_selector
    else:
        logging.error("セレクターの生成に失敗しました。")
        return None
    #HTMLは実際のWebサイトから取得したものをファイルに保存し、読み込む形式を推奨

TEST_CASES = [
    {
    "name": "Google Search Box",
    "html_file": "test_html/google_search.html", # ファイルパスを指定
    "instruction": "検索キーワードを入力するテキストエリア",
    "expected_selector": "#APjFqb" # 期待されるセレクター (検証用)
    },
    {
    "name": "Google Search Button",
    "html_file": "test_html/google_search.html",
    "instruction": "「Google 検索」と書かれたボタン",
    # "expected_selector": ".gNO89b"
    "expected_selector": "body > div.L3eUgb > div.o3j99.ikrT4e.om7nvf > form > div:nth-child(1) > div.A8SBwf > div.FPdoLc.lJ9FBc > center > input.gNO89b" # より特定的なセレクター
    },
    {
        "name": "Google Result 1 Link",
        "html_file": "test_html/google_results.html",
        "instruction": "複数の検索結果の文字列リンクに共通するセレクター",
        "expected_selector": "#rso > div:nth-child(3) > div > div > div.kb0PBd.A9Y9g.jGGQ5e > div > div:nth-child(2) > div > div > span > a" # 今回のHTML構造に合わせた例
    },
    {
        "name": "Google Next Page Link",
        "html_file": "test_html/google_results.html",
        "instruction": "画面下部にある　次へ　リンク",
        "expected_selector": "#pnnext > span.oeN89d"
    },
    {
    "name": "News Article Title",
    "html_file": "test_html/news_article.html",
    "instruction": "記事の見出し (タイトル)",
    "expected_selector": "h1.article-title" # h1 などでも良い場合がある
    },
    {
    "name": "News Login Link",
    "html_file": "test_html/news_article.html",
    "instruction": "「続きを読むにはログイン」というテキストのリンク",
    "expected_selector": "#login-link"
    },
    {
    "name": "News Share Button",
    "html_file": "test_html/news_article.html",
    "instruction": "共有するためのボタン",
    "expected_selector": "button[data-testid='share-button']"
    },
    {
    "name": "News Non-existent Element",
    "html_file": "test_html/news_article.html",
    "instruction": "記事へのコメント投稿フォーム",
    "expected_selector": None # 見つからないはず
    },
    # --- 必要に応じて、より複雑なサイトのテストケースを追加 ---
    # {
    # "name": "EC Site Product Image",
    # "html_file": "test_html/ec_product.html",
    # "instruction": "商品のメイン画像",
    # "expected_selector": "#main-product-image" # 例
    # },
    ]

#--- テスト用HTMLファイルを作成するヘルパー関数 ---

def create_test_html_files():
    """テスト用のHTMLファイルを test_html ディレクトリに作成"""
    html_dir = pathlib.Path("test_html")
    html_dir.mkdir(exist_ok=True)

    google_html = """
    """
    #with open(html_dir / "google_search.html", "w", encoding="utf-8") as f:
    #    f.write(google_html)










    news_html = """
    <!DOCTYPE html><html><head><title>ニュース記事</title></head><body>
    <header>サイトヘッダー</header>
    <main>
        <article>
        <h1 class="article-title main" itemprop="headline">今日のビッグニュース</h1>
        <div class="content body" role="article">
            <p>記事の本文がここに続きます...</p>
            <p>さらなる段落。</p>
            <a href="/login" id="login-link" style="color: blue;">続きを読むにはログイン</a>
        </div>
        <footer>
            <button class="share-button" data-testid="share-button">共有</button>
            <button>コメント</button>
        </footer>
        </article>
    </main>
    <footer>サイトフッター</footer></body></html>
    """
    with open(html_dir / "news_article.html", "w", encoding="utf-8") as f:
        f.write(news_html)

    logging.info(f"テスト用HTMLファイルを '{html_dir}' に作成/更新しました。")








    google_results_html = """
"""
    #with open(html_dir / "google_results.html", "w", encoding="utf-8") as f:
    #    f.write(google_results_html)


def run_all_tests():
    """定義されたすべてのテストケースを実行"""
    logging.info(">>> セレクター抽出テストを開始します <<<")
    create_test_html_files() # テスト用HTMLファイルを準備

    results = {"passed": 0, "failed": 0, "total": len(TEST_CASES)}

    for case in TEST_CASES:
        print("/" * 10)
        logging.info(f"テストケース実行: {case['name']}")
        logging.info(f"指示: {case['instruction']}")
        #logging.info(f"期待値: {case['expected_selector']}")

        html_path = pathlib.Path(case['html_file'])
        if not html_path.exists():
            logging.error(f"  エラー: HTMLファイルが見つかりません: {html_path}")
            results["failed"] += 1
            continue

        try:
            with open(html_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            logging.error(f"  エラー: HTMLファイルの読み込みに失敗しました: {e}")
            results["failed"] += 1
            continue

        # セレクター生成関数を呼び出し
        generated_selector = get_selector_from_html(html_content, case['instruction'])

        # --- 結果の評価 ---
        expected = case['expected_selector']
        passed = False
        if generated_selector == expected:
            passed = True
            logging.info(f"  結果: 一致しました ✅ ('{generated_selector}')")
        elif generated_selector is None and expected is None:
            passed = True
            logging.info(f"  結果: 期待通り見つかりませんでした ✅")
        #else:
        #    logging.warning(f"  結果: 不一致 ❌")
        #    logging.warning(f"    期待値: '{expected}'")
        #    logging.warning(f"    生成値: '{generated_selector}'")

        if passed:
            results["passed"] += 1
        else:
            results["failed"] += 1
        print("") # 各テストケースの区切りをログにも入れる



#--- メイン処理 ---
if __name__ == "__main__": # ← アンダースコアを2つずつ付ける
    run_all_tests()

