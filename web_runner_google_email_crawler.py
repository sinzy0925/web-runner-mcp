# --- ファイル: generate_and_run_google_crawl.py (結果処理 再々修正版) ---
import asyncio
import json
import sys
import os
import time
import argparse
import traceback
from pathlib import Path
from typing import List, Set, Dict, Any, Optional, Tuple, Union
import re # <<< 正規表現モジュールをインポート

# --- インポート (変更なし) ---
try: from web_runner_mcp_client_core import execute_web_runner_via_mcp
except ImportError: print("Error: web_runner_mcp_client_core.py not found..."); sys.exit(1)
try:
    import config
    import utils
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
    DEFAULT_OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
except ImportError: print("Warning: config/utils not found..."); utils = None; DEFAULT_OUTPUT_FILE = None

# --- 定数 (次へセレクターを実績値に戻す) ---
GOOGLE_SEARCH_URL = "https://www.google.com/"
SEARCH_BOX_SELECTOR = "#APjFqb"
SEARCH_BUTTON_SELECTOR = "body > div.L3eUgb > div.o3j99.ikrT4e.om7nvf > form > div:nth-child(1) > div.A8SBwf > div.FPdoLc.lJ9FBc > center > input.gNO89b"
EMAIL_EXTRACT_SELECTOR = "#rso div.kb0PBd.A9Y9g.jGGQ5e span a"
# 「次へ」セレクターを実績値に戻す
NEXT_PAGE_SELECTOR = "#pnnext > span.oeN89d"
# NEXT_PAGE_SELECTOR = "#pnnext" # こちらの方が安定する場合もある

DEFAULT_WAIT_MS = 5000
EMAIL_EXTRACT_WAIT_MS = 7000
DEFAULT_SLEEP_SEC = 2

# --- JSON生成ヘルパー関数 (次へセレクター変更) ---
def generate_google_crawl_json(search_term: str, max_pages: int) -> Dict[str, Any]:
    actions: List[Dict[str, Any]] = []
    actions.append({"memo": "検索ボックスに入力", "action": "input", "selector": SEARCH_BOX_SELECTOR, "value": search_term})
    actions.append({"memo": "検索ボタンをクリック", "action": "click", "selector": SEARCH_BUTTON_SELECTOR, "wait_time_ms": DEFAULT_WAIT_MS})
    actions.append({"memo": f"検索結果表示待機 ({DEFAULT_SLEEP_SEC}秒)", "action": "sleep", "value": DEFAULT_SLEEP_SEC})
    for page_num in range(1, max_pages + 1):
        actions.append({"memo": f"ページ {page_num} メール抽出", "action": "get_all_attributes", "selector": EMAIL_EXTRACT_SELECTOR, "attribute_name": "mail", "wait_time_ms": EMAIL_EXTRACT_WAIT_MS})
        if page_num < max_pages:
            actions.append({
                "memo": f"ページ {page_num + 1} へ移動",
                "action": "click",
                "selector": NEXT_PAGE_SELECTOR, # <<< 実績値に戻したセレクター
                "wait_time_ms": DEFAULT_WAIT_MS
            })
            actions.append({"memo": f"ページ遷移待機 ({DEFAULT_SLEEP_SEC}秒)", "action": "sleep", "value": DEFAULT_SLEEP_SEC})
    return {"target_url": GOOGLE_SEARCH_URL, "actions": actions}

# --- 結果処理ヘルパー関数 (エラー時のJSON抽出を修正) ---
def process_results(
    success: bool,
    result_data_or_error: Union[str, Dict[str, Any]]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    """
    Web-Runnerからの結果またはエラー情報を処理し、
    ユニークドメインメールアドレスリストと全ステップ結果リストを返す。
    エラー発生時でも、それまでに成功したメール抽出結果は集約する。
    """
    all_steps_results: List[Dict[str, Any]] = []
    collected_emails: List[str] = []
    processed_domains: Set[str] = set()

    # --- 結果/エラーデータの解析 ---
    if success and isinstance(result_data_or_error, str):
        try:
            all_steps_results = json.loads(result_data_or_error)
            if not isinstance(all_steps_results, list):
                print("[エラー] Web-Runnerからの成功結果がリスト形式ではありません。")
                all_steps_results = [{"raw_success_result": result_data_or_error}]
        except json.JSONDecodeError:
            print("[エラー] Web-Runnerからの成功結果JSONの解析に失敗しました。")
            all_steps_results = [{"raw_success_result": result_data_or_error}]

    elif not success and isinstance(result_data_or_error, dict):
        print("[情報] Web-Runner実行中にエラーが発生しました。エラー詳細を解析します。")
        raw_details_str = result_data_or_error.get("raw_details")

        # --- ▼▼▼ エラー詳細からのJSON抽出ロジック (正規表現使用) ▼▼▼ ---
        if isinstance(raw_details_str, str):
            # "raw_error" の値を取得し、そこからJSONリストを抽出する試み
            raw_error_val = result_data_or_error.get("raw_error")
            if isinstance(raw_error_val, str):
                # "Details in JSON content: " の後の最初の '[' から最後の ']' までを抽出
                # (?s) は '.' が改行にもマッチするようにするフラグ
                match = re.search(r"Details in JSON content:\s*(\[(?s:.*)\])", raw_error_val)
                if match:
                    potential_json_str = match.group(1) # 括弧の中身を取得
                    print(f"  -> Extracted potential JSON from error (length {len(potential_json_str)}): {potential_json_str[:100]}...")
                    try:
                        # ここで抽出した文字列を直接パース
                        parsed_data = json.loads(potential_json_str)
                        if isinstance(parsed_data, list):
                            all_steps_results = parsed_data
                            print("  -> エラー詳細からステップ結果リストを抽出・解析しました。")
                        else:
                            print("  [警告] エラー詳細から抽出したJSONがリスト形式ではありません。")
                            all_steps_results = [{"parsed_error_detail": parsed_data}]
                    except json.JSONDecodeError as e:
                        print(f"  [警告] 抽出したJSON部分の解析に失敗しました。エラー: {e}")
                        # エラーが発生したJSON文字列をデバッグ用に記録
                        all_steps_results = [{"error_parsing_json_from_error": potential_json_str}]
                    except Exception as e:
                         print(f"  [警告] エラー詳細内のJSON処理中に予期せぬエラー: {e}")
                         all_steps_results = [result_data_or_error] # 元のエラー辞書
                else:
                    print("  [警告] エラー詳細内で 'Details in JSON content: [...]' パターンが見つかりません。")
                    all_steps_results = [result_data_or_error] # 元のエラー辞書
            else:
                print("  [警告] エラー詳細に 'raw_error' キーがないか、値が文字列ではありません。")
                all_steps_results = [result_data_or_error]
        else:
             # raw_details がないか文字列でない場合
             all_steps_results = [result_data_or_error] # 元のエラー辞書をそのままリストに
        # --- ▲▲▲ エラー詳細からのJSON抽出ロジック (正規表現使用) ▲▲▲ ---

    elif isinstance(result_data_or_error, str): # success=False で文字列の場合
        print("[エラー] Web-Runnerからエラー文字列を受け取りました。")
        all_steps_results = [{"error_string": result_data_or_error}]
    else: # その他の予期しない形式
        print("[エラー] 不明な結果/エラー形式を受け取りました。")
        all_steps_results = [{"unknown_result": str(result_data_or_error)}]

    # --- ステップ結果の処理とメール集約 (変更なし) ---
    print("\n--- 各ステップの結果サマリ (エラー発生時も含む) ---")
    if not all_steps_results:
        print("(有効なステップ結果がありません)")
        return [], []

    for step_result in all_steps_results:
        if not isinstance(step_result, dict): print(f"  [警告] 無効なステップデータ形式をスキップ: {type(step_result)}"); continue
        step_num = step_result.get("step", "?"); action = step_result.get("action", "N/A"); status = step_result.get("status", "N/A"); memo = step_result.get("memo", "")
        print(f"Step {step_num}: {action} ({status}) {f'- {memo}' if memo else ''}")
        if (action == "get_all_attributes" and step_result.get("attribute") == "mail" and status == "success"):
            page_emails = step_result.get("extracted_emails", [])
            if page_emails:
                print(f"  -> 抽出されたメールアドレス候補 (ドメインユニーク): {len(page_emails)} 件")
                added_count = 0
                for email in page_emails:
                    if isinstance(email, str) and '@' in email:
                        try:
                            domain = email.split('@', 1)[1].lower()
                            if domain and domain not in processed_domains: collected_emails.append(email); processed_domains.add(domain); added_count += 1
                        except IndexError: pass
                if added_count > 0: print(f"     -> うち {added_count} 件を最終リストに追加。")
            else: print("  -> このステップではメールアドレスは見つかりませんでした。")
        elif status == "error":
             message = step_result.get('message', 'No message'); selector = step_result.get('selector', 'N/A')
             print(f"  [エラー] Selector: {selector} - Message: {message}")
             if step_result.get('traceback'): print(f"  [エラー] 詳細なエラー情報はログファイルを確認してください。")

    return collected_emails, all_steps_results

# --- メイン実行関数 (変更なし) ---
async def main():
    # ... (ArgumentParser部分は変更なし) ...
    parser = argparse.ArgumentParser(description="Generate JSON and run Google Search Email Crawler via Web-Runner MCP")
    parser.add_argument("search_term", type=str, help="Google検索キーワード")
    parser.add_argument("--max_pages", type=int, default=3, help="クロール最大ページ数 (デフォルト: 3)")
    parser.add_argument('--headless', action=argparse.BooleanOptionalAction, default=False, help="ヘッドレス実行")
    parser.add_argument("--slowmo", type=int, default=0, help="SlowMo遅延 (ms, デフォルト: 0)")
    parser.add_argument("--output_json", type=Path, default=None, help="生成した実行用JSONを保存するパス (オプション)")
    parser.add_argument("--output_result", type=Path, default=DEFAULT_OUTPUT_FILE, help=f"Web-Runnerの実行結果を保存するパス (デフォルト: {DEFAULT_OUTPUT_FILE})")
    args = parser.parse_args()
    # --- 1. JSON生成 (変更なし) ---
    print("--- 1. 実行用JSONを生成中 ---")
    web_runner_input_json = generate_google_crawl_json(args.search_term, args.max_pages)
    print(f"生成されたアクション数: {len(web_runner_input_json['actions'])}")
    if args.output_json:
        try:
            args.output_json.parent.mkdir(parents=True, exist_ok=True)
            with open(args.output_json, 'w', encoding='utf-8') as f: json.dump(web_runner_input_json, f, indent=2, ensure_ascii=False)
            print(f"実行用JSONを保存しました: {args.output_json}")
        except Exception as e: print(f"[警告] 実行用JSONの保存に失敗しました: {e}")
    # --- 2. Web-Runner実行 (変更なし) ---
    print("\n--- 2. Web-Runner をMCP経由で実行中 ---")
    start_time = time.monotonic()
    success, result_or_error = await execute_web_runner_via_mcp(web_runner_input_json, headless=args.headless, slow_mo=args.slowmo)
    end_time = time.monotonic(); print(f"Web-Runner実行時間: {end_time - start_time:.2f} 秒")
    # --- 3. 結果処理 & 出力 (変更なし) ---
    print("\n--- 3. 実行結果を処理中 ---")
    final_unique_emails, all_steps_results_list = process_results(success, result_or_error)
    print("\n" + "=" * 30)
    print(f"--- 最終結果: ユニークドメインメールアドレス ({len(final_unique_emails)} 件) [配列形式] ---")
    print(json.dumps(sorted(final_unique_emails), indent=2, ensure_ascii=False))
    print("=" * 30)
    if args.output_result and utils:
        print(f"\nWeb-Runnerの全ステップ結果と最終メールリストをファイルに書き込みます: {args.output_result}")
        try:
            output_path = Path(args.output_result); output_path.parent.mkdir(parents=True, exist_ok=True)
            final_summary = {"Final Unique Domain Emails": sorted(final_unique_emails)}
            utils.write_results_to_file(all_steps_results_list, str(output_path), final_summary_data=final_summary)
            print("結果ファイルの書き込みが完了しました。")
        except Exception as e: print(f"[エラー] 結果ファイルの書き込みに失敗しました: {e}"); traceback.print_exc()
    elif args.output_result and not utils: print(f"[警告] utilsモジュールが見つからないため、結果ファイル '{args.output_result}' への書き込みをスキップします。")

if __name__ == "__main__":
    # ... (実行部分は変更なし) ...
    if sys.platform == "win32": pass
    try: asyncio.run(main())
    except KeyboardInterrupt: print("\n処理が中断されました。")
    except Exception as e: print(f"\n予期せぬエラーが発生しました: {e}"); traceback.print_exc()