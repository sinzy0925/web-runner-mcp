# --- ファイル: web_runner_mcp_client_core.py (ファイル出力修正版) ---

import asyncio
import sys
import json
from pathlib import Path
import anyio
import platform
import traceback
from typing import Optional, Dict, Any, Tuple, Union, List
import argparse

# MCP クライアントライブラリ
from mcp import ClientSession, StdioServerParameters, types as mcp_types
from mcp.client.stdio import stdio_client

# --- ▼▼▼ 追加 ▼▼▼ ---
# 設定ファイルとユーティリティ関数をインポート
try:
    import config
    import utils # ★★★ utils モジュールをインポート ★★★
    DEFAULT_OUTPUT_FILE = Path(config.MCP_CLIENT_OUTPUT_FILE)
except ImportError:
    print("Warning: config.py or utils.py not found. Using default output filename './output_web_runner.txt'")
    DEFAULT_OUTPUT_FILE = Path("./output_web_runner.txt")
    # utils がない場合は、ファイル書き込み部分でエラーになる可能性がある
    utils = None # プレースホルダ
# --- ▲▲▲ 追加 ▲▲▲ ---


# 定数
SERVER_SCRIPT = Path("./web_runner_mcp_server.py")
DEFAULT_SLOW_MO = 0

# --- コア関数: Web-RunnerをMCP経由で実行 ---
async def execute_web_runner_via_mcp(
    input_json_data: Dict[str, Any],
    headless: bool = False, # デフォルトをFalse (表示) に変更
    slow_mo: int = DEFAULT_SLOW_MO
) -> Tuple[bool, Union[str, Dict[str, Any]]]:
    """
    指定されたJSONデータをWeb-Runner MCPサーバーに送信し、実行結果を取得する。
    """
    print("--- Executing Web Runner via MCP ---")
    print(f"Input data (type: {type(input_json_data)}): {str(input_json_data)[:200]}...")
    print(f"Headless: {headless}, SlowMo: {slow_mo}")

    if not SERVER_SCRIPT.exists():
        error_msg = f"Error: Server script not found at {SERVER_SCRIPT}"
        print(error_msg)
        return False, {"error": error_msg}

    # --- サーバーに渡す引数を構築 ---
    tool_arguments = {
        "input_args": {
            "target_url": input_json_data.get("target_url"),
            "actions": input_json_data.get("actions", []),
            "headless": headless,
            "slow_mo": slow_mo,
            "default_timeout_ms": input_json_data.get("default_timeout_ms")
        }
    }
    if not tool_arguments["input_args"]["target_url"] or not tool_arguments["input_args"]["actions"]:
         error_msg = "Error: 'target_url' or 'actions' missing in input_json_data."
         print(error_msg)
         return False, {"error": error_msg}

    print("Preparing server parameters...")
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_SCRIPT), "--transport", "stdio", "--log-level", "INFO"],
    )
    print(f"Server command: {sys.executable} {SERVER_SCRIPT} --transport stdio --log-level INFO")

    session: Optional[ClientSession] = None
    try:
        print("Connecting to server via stdio_client...")
        async with stdio_client(server_params) as streams:
            print("DEBUG: stdio_client context entered.")
            read_stream, write_stream = streams
            print("DEBUG: Got streams from stdio_client.")
            print("Creating ClientSession...")
            async with ClientSession(read_stream, write_stream) as session:
                print("DEBUG: ClientSession context entered.")
                print("Initializing session...")
                await session.initialize()
                print("DEBUG: Initialization complete.")

                print("Calling 'execute_web_runner' tool...")
                print(f"DEBUG: Calling tool with arguments: {str(tool_arguments)[:500]}...")

                tool_result: mcp_types.CallToolResult = await session.call_tool(
                    name="execute_web_runner",
                    arguments=tool_arguments
                )
                print("DEBUG: Tool call finished.")

                if tool_result.isError:
                    print("--- Tool Execution Error ---")
                    error_content = "Unknown error format received from server."
                    if tool_result.content and isinstance(tool_result.content[0], mcp_types.TextContent):
                        error_content = tool_result.content[0].text
                        print(f"Received error details:\n{error_content}")
                        try:
                           error_data = json.loads(error_content)
                           if "JSON: " in error_content:
                               json_part = error_content.split("JSON: ", 1)[1]
                               try: error_data = json.loads(json_part)
                               except json.JSONDecodeError: pass
                           return False, {"error": "MCP tool execution failed", "details": error_data}
                        except json.JSONDecodeError:
                           return False, {"error": "MCP tool execution failed", "raw_details": error_content}
                    else:
                        print(error_content)
                        return False, {"error": error_content}
                else:
                    print("--- Tool Execution Success ---")
                    if tool_result.content and isinstance(tool_result.content[0], mcp_types.TextContent):
                        result_json_string = tool_result.content[0].text
                        return True, result_json_string
                    else:
                        print("No content received from server.")
                        return False, {"error": "No content received from server"}

    except Exception as e:
        print(f"--- An Exception Occurred During MCP Communication ---")
        error_msg = f"{type(e).__name__}: {e}"
        print(error_msg)
        print("--- Traceback ---")
        traceback.print_exc()
        return False, {"error": f"MCP communication error: {error_msg}"}
    finally:
        print("--- Web Runner via MCP Finished ---")


# --- このファイル単体でテストするための実行部分 ---
async def main():
    """テスト用のJSONファイルを読み込んでコア関数を呼び出し、結果をファイルに出力"""
    parser = argparse.ArgumentParser(description="Test Web-Runner MCP Client Core")
    parser.add_argument(
        "--jsonfile",
        type=Path,
        default=Path("./json/tdnet.json"), # デフォルトをtdnet.jsonにしてみる
        help="Path to the input JSON file for testing."
    )
    parser.add_argument(
        '--headless',
        action=argparse.BooleanOptionalAction,
        default=False, # デフォルトはブラウザ表示
        help="Run browser in headless mode."
    )
    parser.add_argument(
        "--slowmo",
        type=int,
        default=DEFAULT_SLOW_MO,
        help=f"Slow motion delay in milliseconds (default: {DEFAULT_SLOW_MO})."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
        help=f"Path to the output file (default: {DEFAULT_OUTPUT_FILE})."
    )
    args = parser.parse_args()
    test_json_file_path: Path = args.jsonfile
    output_file_path: Path = args.output
    slow_mo_value: int = args.slowmo

    print(f"Loading input JSON from: {test_json_file_path}")
    if not test_json_file_path.exists():
        print(f"Error: Test JSON file not found at {test_json_file_path}")
        return

    try:
        with open(test_json_file_path, 'r', encoding='utf-8') as f:
            test_input = json.load(f)
        print("Input JSON loaded successfully.")
    except Exception as e:
        print(f"Error loading JSON file: {e}")
        return

    success, result_or_error = await execute_web_runner_via_mcp(
        test_input,
        headless=args.headless,
        slow_mo=slow_mo_value
    )

    # ファイル書き込み処理
    print(f"\nWriting result to: {output_file_path}")
    if success and isinstance(result_or_error, str):
        try:
            result_data_list = json.loads(result_or_error)
            if isinstance(result_data_list, list) and utils: # ★★★ utils がインポートできているか確認 ★★★
                 utils.write_results_to_file(result_data_list, str(output_file_path))
            elif not utils:
                 print("Error: Cannot write results because 'utils' module failed to import.")
                 with open(output_file_path, 'w', encoding='utf-8') as f:
                     f.write("--- Execution Succeeded but Result Writing Failed (utils missing) ---\n")
                     f.write(result_or_error)
            else:
                 print("Error: Result JSON from server is not a list.")
                 with open(output_file_path, 'w', encoding='utf-8') as f:
                      f.write("--- Execution Failed ---\nReceived invalid result format (not a list):\n")
                      f.write(result_or_error)
        except json.JSONDecodeError:
            error_msg = f"Error: Received non-JSON success result:\n{result_or_error}"
            print(error_msg)
            with open(output_file_path, 'w', encoding='utf-8') as f:
                 f.write(error_msg)
        except Exception as write_e:
             print(f"Error writing result to file {output_file_path}: {write_e}")
             traceback.print_exc()
    else: # success is False
        print("\n--- Final Result (Error) ---")
        formatted_error = json.dumps(result_or_error, indent=2, ensure_ascii=False)
        print(formatted_error)
        with open(output_file_path, 'w', encoding='utf-8') as f:
            f.write(f"--- Execution Failed ---\n{formatted_error}")


if __name__ == "__main__":
    if platform.system() == "Windows":
        pass

    try:
        anyio.run(main)
    except Exception as e:
        print(f"Error running anyio task: {e}")
        traceback.print_exc()