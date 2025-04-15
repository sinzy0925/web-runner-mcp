# --- ファイル: main.py (修正版) ---
"""
スクリプトのエントリーポイント。引数解析、初期化、実行、結果出力を行う。
MCPサーバーとは独立して、このファイル単体でも実行可能。
"""
import argparse
import asyncio
import logging
import os
import sys
import pprint

# --- 各モジュールをインポート ---
import config
import utils
# --- ▼▼▼ 修正 ▼▼▼ ---
# import playwright_handler -> playwright_launcher をインポート
import playwright_launcher
# --- ▲▲▲ 修正 ▲▲▲ ---

# --- エントリーポイント ---
if __name__ == "__main__":
    # --- 1. ロギング設定 (単体実行用) ---
    utils.setup_logging_for_standalone(config.LOG_FILE)

    # --- 2. コマンドライン引数解析 ---
    parser = argparse.ArgumentParser(
        description="JSON入力に基づき Playwright 自動化を実行 (iframe動的探索対応)。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--input",
        default=config.DEFAULT_INPUT_FILE,
        metavar="FILE",
        help="URL とアクションを含む JSON ファイルのパス。"
    )
    parser.add_argument(
        '--headless',
        action=argparse.BooleanOptionalAction, # --headless / --no-headless を使えるように
        default=False, # デフォルトは表示 (False)
        help="ブラウザをヘッドレスモードで実行 (--no-headless で表示)。"
    )
    parser.add_argument(
        '--slowmo',
        type=int,
        default=100,
        metavar="MS",
        help="各操作間の待機時間(ms)。"
    )
    args = parser.parse_args()

    # --- 3. 入力ファイルパス解決 ---
    input_arg = args.input
    json_file_path = input_arg
    # 入力がファイル名のみで、json/ ディレクトリに存在する場合、そちらを優先
    if not os.path.isabs(input_arg) and \
       not os.path.dirname(input_arg) and \
       os.path.exists(os.path.join('json', input_arg)):
        json_file_path = os.path.join('json', input_arg)
        logging.info(f"--input でファイル名のみ指定され、'json' ディレクトリ内に '{input_arg}' が見つかったため、'{json_file_path}' を使用します。")
    elif not os.path.exists(input_arg):
        logging.critical(f"指定された入力ファイル '{input_arg}' が見つかりません。") # criticalに変更
        sys.exit(1) # 単体実行時は見つからなければ終了

    # スクリーンショットディレクトリ作成
    os.makedirs(config.DEFAULT_SCREENSHOT_DIR, exist_ok=True)

    # --- 4. メイン処理実行 ---
    try:
        input_data = utils.load_input_from_json(json_file_path)
        target_url = input_data.get("target_url")
        actions = input_data.get("actions")

        # JSONファイル内のタイムアウト指定を優先、なければconfigの値
        effective_default_timeout = input_data.get("default_timeout_ms", config.DEFAULT_ACTION_TIMEOUT)
        logging.info(f"実行に使用するデフォルトアクションタイムアウト: {effective_default_timeout}ms")

        if not target_url or not actions:
            logging.critical(f"エラー: JSON '{json_file_path}' から target_url または actions を取得できませんでした。")
            sys.exit(1)

        # --- ▼▼▼ 修正 ▼▼▼ ---
        # Playwrightハンドラ -> ランチャー を呼び出し
        success, results = asyncio.run(playwright_launcher.run_playwright_automation_async(
            target_url=str(target_url), # URLは文字列として渡す
            actions=actions,
            headless_mode=args.headless, # BooleanOptionalAction の結果を渡す
            slow_motion=args.slowmo,
            default_timeout=effective_default_timeout
        ))
        # --- ▲▲▲ 修正 ▲▲▲ ---

        # --- 5. 結果表示・出力 ---
        print("\n--- 最終実行結果 ---")
        # pprintで見やすく整形して表示
        pprint.pprint(results)
        logging.info(f"最終実行結果(詳細):\n{pprint.pformat(results)}")

        # 結果ファイル書き込み (utils内の関数を使用)
        utils.write_results_to_file(results, config.RESULTS_OUTPUT_FILE) # 単体実行用出力ファイル

        sys.exit(0 if success else 1)

    except FileNotFoundError as e:
         logging.critical(f"入力ファイル処理中にエラー: {e}")
         sys.exit(1)
    except Exception as e:
        logging.critical(f"スクリプト実行の最上位で予期せぬエラーが発生: {e}", exc_info=True)
        sys.exit(1)