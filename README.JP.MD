# Web-Runner-mcp: AIのための高度なWebブラウザ操作プロトコル

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
<!-- バッジは適宜追加・修正 -->

**Web-Runner-mcp** は、Playwrightの強力なブラウザ自動化機能を、標準化された **Model Context Protocol (MCP)** を通じてAIエージェントや他のアプリケーションから容易に利用可能にするためのPythonプロジェクトです。

![Web-Runner Logo](./Web-Runner.png)


## 目次

*   [概要](#概要)
*   [なぜ Web-Runner-mcp なのか？](#なぜ-web-runner-mcp-なのか)
*   [主な機能](#主な機能)
    *   [サポートされるアクション](#サポートされるアクション)
    *   [PDFテキスト抽出](#pdfテキスト抽出)
    *   [エラーハンドリング](#エラーハンドリング)
*   [使い方](#使い方)
    *   [1. セットアップ](#1-セットアップ)
    *   [2. サーバーの起動 (SSEモード例)](#2-サーバーの起動-sseモード例)
    *   [3. Web-Runner用 JSONデータの作成](#3-web-runner用-jsonデータの作成)
        *   [ステップ1: JSONジェネレーターの準備](#ステップ1-jsonジェネレーターの準備)
        *   [ステップ2: 操作対象のCSSセレクターを取得する](#ステップ2-操作対象のcssセレクターを取得する)
        *   [ステップ3: json_generator.html で操作手順を作成する](#ステップ3-json_generatorhtml-で操作手順を作成する)
        *   [ステップ4: JSONファイルの配置](#ステップ4-jsonファイルの配置)
    *   [4. コマンドラインからの実行 (テスト用)](#4-コマンドラインからの実行-テスト用)
    *   [5. GUIクライアントからの実行](#5-guiクライアントからの実行)
    *   [6. AIアプリケーションからの利用](#6-aiアプリケーションからの利用)
*   [JSONフォーマット (参考)](#jsonフォーマット-参考)
*   [他のツールとの比較](#他のツールとの比較)
*   [将来の展望](#将来の展望)
*   [貢献 (Contributing)](#貢献-contributing)
*   [ライセンス (License)](#ライセンス-license)

---

## 概要

今日のAIエージェントにとってWebからの情報収集や操作は不可欠ですが、既存のツールには限界がありました。単純なコンテンツ取得や検索結果リストの取得はできても、ログインが必要なサイト、複雑なJavaScriptで描画されるページ、iframe構造、PDFコンテンツの操作は困難でした。また、Playwrightのような低レベルAPIをLLMから直接、安定して制御することも大きな挑戦でした。

Web-Runner-mcpは、この課題に対する新しいアプローチを提案します。

LLMに個々のブラウザ操作を指示させる代わりに、実行したい一連の操作シーケンスをJSON形式で定義し、それをMCPサーバーに渡して実行させます。
現バージョンでは、LLMを利用せずに、JSONファイルの指示に従い、Web-Runner-mcpサーバーが確実にブラウザ操作を実行します。

これは、AIがWebと対話する方法における**「小さな革命」**かもしれません。これまでAIがアクセスできなかった、より深く複雑なWebの世界への扉を開きます。

## なぜ Web-Runner-mcp なのか？

*   **高度なWeb操作:**
    *   **ログイン:** 認証が必要なWebサイトへのアクセスと操作。
    *   **PDF:** リンク先のPDFをダウンロードし、テキストコンテンツを抽出。
    *   **Iframe:** ネストされたiframe内の要素も探索・操作（動的探索機能）。
    *   **複数タブ/ページ:** クリックによって開かれた新しいページへの追従。
    *   **動的コンテンツ:** JavaScriptで生成される要素の待機と操作。
*   **多彩なデータ抽出:**
    *   `innerText`, `textContent`, `innerHTML` による柔軟なテキスト/HTML取得。
    *   `getAttribute` による特定の属性値取得。
    *   `getAllAttributes`, `getAllTextContents` による複数要素からの効率的なデータ収集（動的探索機能）。
*   **宣言的な操作定義:**
    *   実行したい手順をJSONで記述。
    *   操作の再現性とデバッグが容易に。
*   **MCP準拠:**
    *   標準化されたプロトコルにより、様々なMCPクライアント (Difyカスタムツール、Python AIエージェントフレームワーク、自作クライアントなど) との連携が可能。
    *   クライアントとサーバーの関心を分離。
*   **確実な実行:**
    *   Playwrightによる安定したブラウザ操作実行。
    *   適切な待機処理とエラーハンドリング。

## 主な機能

*   **MCPサーバー (`web_runner_mcp_server.py`):** Python (`FastMCP`ベース) で実装され、Web-Runnerの機能を`execute_web_runner`ツールとして公開。
*   **Web-Runnerコア (`playwright_handler.py`, `utils.py`, `config.py`):** Playwright (async) を使用し、入力JSONに基づいてブラウザ操作を実行するコアロジックと設定、ユーティリティ関数。動的なiframe探索やPDF処理などを担当。
*   **Web-Runner単体実行 (`main.py`):** MCPサーバーを経由せずに、コマンドラインから直接Web-Runnerを実行するためのエントリーポイント（デバッグや単体テスト用）。
*   **MCPクライアントコア (`web_runner_mcp_client_core.py`):** プログラム（AIエージェントなど）からMCPサーバーを呼び出すためのコア関数 (`execute_web_runner_via_mcp`) を提供。
*   **GUIクライアント (`web_runner_mcp_client_GUI.py`):** JSONファイルを選択して手動実行したり、JSONジェネレーターを起動したりするための便利なGUIインターフェース。

### サポートされるアクション

*   `click`: 要素をクリック
*   `input`: 要素にテキストを入力
*   `hover`: 要素にマウスオーバー
*   `get_inner_text`, `get_text_content`, `get_inner_html`: テキスト/HTMLを取得 (単一要素)
*   `get_attribute`: 属性値を取得 (単一要素)
*   `get_all_attributes`, `get_all_text_contents`: 属性値/テキストをリストで取得 (複数要素、iframe内も探索)
*   `wait_visible`: 要素が表示されるまで待機
*   `select_option`: ドロップダウンリストを選択
*   `screenshot`: ページ全体または要素のスクリーンショットを保存 (サーバー側)
*   `scroll_page_to_bottom`, `scroll_to_element`: スクロール操作
*   `wait_page_load`: ページ読み込み完了を待機
*   `sleep`: 指定時間待機
*   `switch_to_iframe`, `switch_to_parent_frame`: iframe間の移動 (明示指定)

### PDFテキスト抽出

`get_attribute(href=...)` や `get_all_attributes(href=...)` で取得したPDFリンクの内容を自動でダウンロードし、テキストを抽出して結果に含めます。

### エラーハンドリング

各ステップでのエラー情報と、エラー発生時のスクリーンショットパス (サーバー側ファイルシステム上のパス) を結果に記録します。

## 使い方

### 1. セットアップ

**(1) リポジトリのクローン:**

```bash
git clone https://github.com/sinzy0925/web-runner-mcp.git
cd web-runner-mcp
```

**(2) Python環境の準備 (Python 3.12 推奨):**
```bash
# 仮想環境を作成 (例: venv312)
python -m venv venv312
# 仮想環境をアクティベート
# Windows PowerShell
.\venv312\Scripts\Activate
# Linux/macOS
source venv312/bin/activate
```

**(3) 依存ライブラリのインストール:**
```bash
requirements.txt ファイルを使ってインストールします。
pip install -r requirements.txt
```

**(4) Playwright ブラウザのインストール:**
```bash
playwright install
```

### 2. サーバーの起動 (SSEモード例)
### 検証できてません。動くかどうか不明です。
### Dify連携など、ネットワーク経由でアクセスする場合は、SSEモードでサーバーを起動します。
```bash
# web_runner_mcp_server.py を直接実行
python web_runner_mcp_server.py --transport sse --host 0.0.0.0 --port 8000
```

- --host 0.0.0.0 は、他のマシンからアクセス可能にする場合に指定します。ローカルのみなら 127.0.0.1 (デフォルト)で構いません。
- --port 8000 は、サーバーが待ち受けるポート番号です。
- サーバーログは web_runner_mcp_server.log に出力されます（デフォルト設定）。

## 3. Web-Runner用 JSONデータの作成
付属の json_generator.html を使うと、ブラウザ上で対話的にJSONを作成できます。

**ステップ1: JSONジェネレーターの準備**
1. プロジェクトフォルダ内にある json_generator.html ファイルをダブルクリックし、Webブラウザで開きます。

**ステップ2: 操作対象のCSSセレクターを取得する**
1. 自動操作を行いたいWebサイトを別のブラウザタブまたはウィンドウで開きます。
2. そのページでデベロッパーツールを開きます (F12キー or 右クリック > 「検証」/「要素の検査」)。
3. デベロッパーツールの要素選択アイコン (↖) をクリックします。
4. Webページ上で操作したい要素（ボタン、入力欄など）をクリックします。
5. デベロッパーツール上でハイライトされたHTML要素を右クリックし、[コピー] > [セレクターをコピー] を選択します。

**ステップ3: json_generator.html で操作手順を作成する**
1. json_generator.html に戻り、「1. 自動化を開始するURL:」にWebサイトのURLを入力します。
2. 「2. 操作ステップ」で以下を入力します。
- 対象要素のCSSセレクター: コピーしたセレクターを貼り付けます。
- 操作: 実行したいアクションを選択します。
- 追加パラメータ: 必要に応じて入力します（value, attribute_name など）。
3. 「ステップを追加」ボタンで操作を追加し、上記2を繰り返します。
4. 「入力データ生成 (JSON)」ボタンをクリックし、生成されたJSONを確認します。
5. 「input.json をダウンロード」ボタンでJSONファイルをダウンロードします。

**ステップ4: JSONファイルの配置**
1. ダウンロードしたJSONファイルを、プロジェクトフォルダ内の json/ フォルダに置きます。ファイル名は自由に設定できます（例: input.json）。

## 4. コマンドラインからの実行 (テスト用)
GUIクライアントを使わずに、プログラムからの利用を想定したコア関数 (web_runner_mcp_client_core.py) を使って、コマンドラインから直接Web-Runnerを実行することも可能です。これは、AIエージェントなど、プログラムから呼び出す際の動作確認に役立ちます。
1. 実行したいJSONファイルを json/ フォルダに用意します (例: tdnet.json)。
2. ターミナル（仮想環境アクティベート済み）で以下のコマンドを実行します。
```bash
python web_runner_mcp_client_core.py --jsonfile json/tdnet.json --no-headless --slowmo 500
```
- --jsonfile: 実行するJSONファイルのパスを指定します (デフォルトは json/tdnet.json)。
- --no-headless: ブラウザを表示して実行する場合に指定します（デフォルトは表示）。
- --headless: ヘッドレスで実行する場合。
- --slowmo: (オプション) 各操作間の待機時間をミリ秒で指定します (例: --slowmo 500)。
- --output: (オプション) 結果を出力するファイルパスを指定します (デフォルトは output_web_runner.txt)。
実行結果（成功時は取得データ、失敗時はエラー情報）がコンソールに出力され、指定されたファイルにも書き込まれます。

## 5. GUIクライアントからの実行
手動でのテストやデバッグには、GUIクライアント (web_runner_mcp_client_GUI.py) が便利です。
1. ターミナル（仮想環境アクティベート済み）で以下のコマンドを実行します。
```bash
python web_runner_mcp_client_GUI.py
```
2. 起動したウィンドウのドロップダウンリストから実行したいJSONファイルを選択します。
3. 「実行 ▶」ボタンをクリックします。
4. 実行結果が下のテキストエリアに表示されます。
5. 「JSONジェネレーター」ボタンで json_generator.html を開くこともできます。

## 6. AIアプリケーションからの利用
他のPythonスクリプトやAIエージェントフレームワークから利用する場合は、web_runner_mcp_client_core.py 内の execute_web_runner_via_mcp 関数をインポートして使用します。
```python
import asyncio
import json
import sys # sysを追加
# web_runner_mcp_client_core.py がインポートパスにあることを確認
try:
    from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError:
    print("Error: web_runner_mcp_client_core.py not found.")
    # エラー処理またはパス設定が必要
    sys.exit(1) # 例

async def run_task():
    input_data = {
        "target_url": "https://example.com",
        "actions": [
            {"action": "get_text_content", "selector": "h1"},
            {"action": "get_attribute", "selector": "img", "attribute_name": "src"}
        ]
        # オプションでタイムアウトなども指定可能
        # "default_timeout_ms": 15000
    }
    # ヘッドレスモードをTrueに、SlowMoを50msに設定して実行
    success, result_or_error = await execute_web_runner_via_mcp(
        input_data, headless=True, slow_mo=50 # headless, slow_mo を指定
    )

    if success and isinstance(result_or_error, str):
        print("Task successful! Result (JSON):")
        try:
            result_dict = json.loads(result_or_error)
            print(json.dumps(result_dict, indent=2, ensure_ascii=False))
            # --- ここで結果をLLMに渡すなどの処理 ---
            # llm_prompt = f"以下のWebサイト操作結果を分析してください:\n```json\n{result_or_error}\n```"
            # llm_response = await call_llm(llm_prompt)
        except json.JSONDecodeError:
            print("Error: Response from server is not valid JSON:")
            print(result_or_error)
    else:
        print("Task failed:")
        print(result_or_error) # エラー情報 (辞書) を表示
        # --- ここでエラー情報をLLMに渡すなどの処理 ---
        # error_prompt = f"Webサイト操作に失敗しました。エラー内容:\n{result_or_error}\n原因を推測してください。"
        # llm_response = await call_llm(error_prompt)

if __name__ == "__main__":
    asyncio.run(run_task())
```


## 他のツールとの比較
- 一般的なWebスクレイピングライブラリ (BeautifulSoup, Scrapy): 静的なHTMLの解析には優れていますが、JavaScriptの実行、ログイン、複雑なユーザー操作、iframe、PDFの扱いは困難または不可能です。Web-Runner-mcpはPlaywrightベースのため、これらの高度な操作に対応します。
- Playwright-MCP: Playwrightの低レベルAPIを直接MCPツールとして公開します。非常に柔軟ですが、LLMから安定して制御するには複雑なプロンプトエンジニアリングと状態管理が必要です。Web-Runner-mcpは、操作シーケンスをJSONで定義することで、より宣言的で信頼性の高いインターフェースを提供します。
- シンプルなWeb取得ツール (例: URLコンテンツ取得): 特定のURLの内容を取得するだけなら簡単ですが、複数ステップの操作やインタラクションはできません。Web-Runner-mcpは、複数ステップのワークフローを実行できます。

## 将来の展望
- LLMによるJSON自動生成: 自然言語の指示からWeb-Runner用のJSONを自動生成する機能の統合。
- 対応アクションの拡充: より多くのPlaywright機能（ファイルアップロード、Cookie操作など）への対応。
- Difyカスタムツールとしての正式サポート: HTTP/SSEインターフェースを安定化させ、Difyマーケットプレイスへの登録などを目指す。
- エラーハンドリングとリカバリの強化: より詳細なエラー分析と、可能であれば自動リトライなどのリカバリ機能。

## 貢献 (Contributing)
- バグ報告、機能提案、プルリクエストを歓迎します！ 詳細は CONTRIBUTING.md をご覧ください (まだ存在しない場合は作成予定)。

## ライセンス (License)
このプロジェクトは MIT License の下で公開されています。[LICENSE](LICENSE)