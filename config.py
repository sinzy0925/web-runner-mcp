# --- ファイル: config.py ---
"""
スクリプト全体で使用する設定値と定数を定義します。
"""

# --- Playwright 関連設定 ---
DEFAULT_ACTION_TIMEOUT = 10000  # デフォルトのアクションタイムアウト (ミリ秒)
IFRAME_LOCATOR_TIMEOUT = 10000  # iframe存在確認のタイムアウト (ミリ秒)
PDF_DOWNLOAD_TIMEOUT = 60000    # PDFダウンロードのタイムアウト (ミリ秒)
NEW_PAGE_EVENT_TIMEOUT = 10000  # 新しいページが開くのを待つタイムアウト (ミリ秒)

# --- 動的探索関連設定 ---
DYNAMIC_SEARCH_MAX_DEPTH = 2    # iframe探索の最大深度

# --- ファイルパス・ディレクトリ名 ---
LOG_FILE               = 'output_web_runner.log'
DEFAULT_INPUT_FILE     = 'input.json'
DEFAULT_SCREENSHOT_DIR = 'screenshots'
RESULTS_OUTPUT_FILE    = 'output_results.txt'

# --- その他 ---
# 必要に応じて他の設定値を追加
MCP_SERVER_LOG_FILE    = 'output/web_runner_mcp.log' # MCPサーバー用ログファイル名 (例)
MCP_CLIENT_OUTPUT_FILE = 'output/web_runner_mcp.txt' # MCPクライアントのデフォルト出力ファイル名