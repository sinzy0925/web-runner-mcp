# --- ファイル: web_runner_mcp_server.py (修正版) ---

import json
import logging # logging をインポート
import traceback # 詳細なエラー出力用
from typing import Any, Dict, List, Literal, Union

# MCP SDK と Pydantic をインポート
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.logging import configure_logging # MCPのログ設定関数
from pydantic import BaseModel, Field, HttpUrl

# --- ▼▼▼ 修正 ▼▼▼ ---
# 分割した Web-Runner のコア関数と設定をインポート
try:
    import config # 設定値を参照するため
    import utils  # ロギング設定などに使う可能性
    # playwright_handler -> playwright_launcher をインポート
    from playwright_launcher import run_playwright_automation_async # メインの実行関数
except ImportError as import_err:
    logging.critical(f"致命的エラー: Web-Runnerの必須モジュール (config, utils, playwright_launcher) のインポートに失敗しました: {import_err}")
    logging.critical("config.py, utils.py, playwright_launcher.py が同じディレクトリにあるか、PYTHONPATHに含まれているか確認してください。")
    import sys
    sys.exit(1)
# --- ▲▲▲ 修正 ▲▲▲ ---

# --- ロガー取得 ---
logger = logging.getLogger(__name__)

# --- 入力スキーマ定義 (変更なし) ---
class ActionStep(BaseModel):
    action: str = Field(..., description="実行するアクション名 (例: 'click', 'input', 'get_text_content')")
    selector: str | None = Field(None, description="アクション対象のCSSセレクター (要素操作アクションの場合)")
    iframe_selector: str | None = Field(None, description="iframeを対象とする場合のCSSセレクター (switch_to_iframeの場合)")
    value: str | float | int | bool | None = Field(None, description="入力する値 (input) や待機時間 (sleep)、スクリーンショットファイル名など")
    attribute_name: str | None = Field(None, description="取得する属性名 (get_attribute, get_all_attributesの場合)")
    option_type: Literal['value', 'index', 'label'] | None = Field(None, description="ドロップダウン選択方法 (select_optionの場合)")
    option_value: str | int | None = Field(None, description="選択する値/インデックス/ラベル (select_optionの場合)")
    wait_time_ms: int | None = Field(None, description="このアクション固有の最大待機時間 (ミリ秒)。省略時はdefault_timeout_msが使われる。")

class WebRunnerInput(BaseModel):
    target_url: Union[HttpUrl, str] = Field(..., description="自動化を開始するWebページのURL (文字列も許容)")
    actions: List[ActionStep] = Field(..., description="実行するアクションステップのリスト", min_length=1)
    headless: bool = Field(True, description="ヘッドレスモードで実行するかどうか (デフォルトはTrue)") # MCP経由ではTrueがデフォルトで良さそう
    slow_mo: int = Field(0, description="各操作間の待機時間 (ミリ秒)", ge=0)
    default_timeout_ms: int | None = Field(None, description=f"デフォルトのアクションタイムアウト(ミリ秒)。省略時はサーバー設定 ({config.DEFAULT_ACTION_TIMEOUT}ms) が使われる。")

# --- FastMCP サーバーインスタンス作成 (変更なし) ---
mcp = FastMCP(
    name="WebRunnerServer",
    instructions="Webサイトの自動操作とデータ抽出を実行するサーバーです。URLと一連のアクションを指定してください。",
    dependencies=["playwright", "PyMuPDF", "fitz", "playwright-stealth"] # 依存関係にstealth追加
)

# --- MCPツール定義 (修正: インポート元変更) ---
@mcp.tool()
async def execute_web_runner(
    input_args: WebRunnerInput,
    ctx: Context
) -> str:
    """
    指定されたURLとアクションリストに基づいてWebブラウザ自動化タスクを実行し、
    結果をJSON文字列として返します。
    """
    await ctx.info(f"Received task for URL: {input_args.target_url} with {len(input_args.actions)} actions.")
    await ctx.debug(f"Input arguments (raw): {input_args.model_dump()}") # Pydantic v2

    try:
        target_url_str = str(input_args.target_url) # URLを文字列に変換
        # アクションリストを辞書のリストに変換
        actions_list = [step.model_dump(exclude_none=True) for step in input_args.actions]
        # 有効なデフォルトタイムアウトを決定
        effective_default_timeout = input_args.default_timeout_ms if input_args.default_timeout_ms is not None else config.DEFAULT_ACTION_TIMEOUT
        await ctx.info(f"Using effective default timeout: {effective_default_timeout}ms")

        await ctx.debug(f"Calling playwright_launcher.run_playwright_automation_async with:")
        await ctx.debug(f"  target_url='{target_url_str}'")
        await ctx.debug(f"  actions_count={len(actions_list)}")
        await ctx.debug(f"  headless_mode={input_args.headless}")
        await ctx.debug(f"  slow_motion={input_args.slow_mo}")
        await ctx.debug(f"  default_timeout={effective_default_timeout}")

        # --- ▼▼▼ 修正 ▼▼▼ ---
        # playwright_handler -> playwright_launcher のコア関数を呼び出す
        success, results = await run_playwright_automation_async(
            target_url=target_url_str,
            actions=actions_list,
            headless_mode=input_args.headless,
            slow_motion=input_args.slow_mo,
            default_timeout=effective_default_timeout
        )
        # --- ▲▲▲ 修正 ▲▲▲ ---

        # 結果をJSON文字列に変換 (エラー時も含む)
        # ensure_ascii=False で日本語がエスケープされないようにする
        results_json = json.dumps(results, indent=2, ensure_ascii=False)
        await ctx.debug(f"Task finished. Success: {success}. Results JSON (first 500 chars): {results_json[:500]}...")

        if success:
            await ctx.info("Task completed successfully.")
            return results_json
        else:
            # 失敗した場合、ToolErrorを送出するが、そのメッセージに結果JSONを含める
            await ctx.error("Task failed. Returning error information via ToolError.")
            # ToolErrorのメッセージにJSONを含めることで、クライアント側で詳細を確認できるようにする
            raise ToolError(f"Web-Runner task failed. Details in JSON content: {results_json}")

    except ImportError as e:
         # サーバー起動時のインポートエラーは上で処理されるはずだが、念のため
         err_msg = f"Server configuration error: Failed to load core Web-Runner module ({e})"
         await ctx.error(err_msg)
         raise ToolError(err_msg)
    except Exception as e:
        # 予期せぬエラーが発生した場合
        error_traceback = traceback.format_exc()
        await ctx.error(f"Unhandled exception during Web-Runner execution: {type(e).__name__} - {e}")
        await ctx.error(f"Traceback:\n{error_traceback}")
        # クライアントにはエラーが発生したことと、サーバーログを確認するよう促すメッセージを含むエラー情報を返す
        error_result = [{
            "step": "MCP Tool Execution",
            "status": "error",
            "message": f"Unhandled server error occurred: {type(e).__name__}. Please check server logs for details.",
            "error_type": type(e).__name__,
            "raw_error": str(e)
        }]
        error_json = json.dumps(error_result, indent=2, ensure_ascii=False)
        raise ToolError(f"Unhandled server error during execution. Details: {error_json}")

# --- サーバー起動設定 (変更なし) ---
if __name__ == "__main__":
    import typer

    cli_app = typer.Typer()

    @cli_app.command()
    def main(
        transport: str = typer.Option(
            "stdio", "--transport", "-t",
            help="Transport protocol (stdio or sse)",
        ),
        host: str = typer.Option(
            "127.0.0.1", "--host", help="Host for SSE server"
        ),
        port: int = typer.Option(
            8000, "--port", "-p", help="Port for SSE server"
        ),
        log_level: str = typer.Option(
            "INFO", "--log-level", help="Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)"
        )
    ):
        """Web-Runner MCP Server"""
        transport_lower = transport.lower()
        if transport_lower not in ["stdio", "sse"]:
            print(f"エラー: 無効なトランスポートタイプ '{transport}'。'stdio' または 'sse' を指定してください。", file=sys.stderr)
            raise typer.Exit(code=1)

        log_level_upper = log_level.upper()
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level_upper not in valid_log_levels:
            print(f"エラー: 無効なログレベル '{log_level}'。{valid_log_levels} のいずれかを指定してください。", file=sys.stderr)
            raise typer.Exit(code=1)

        # --- MCPログ設定 (ファイル出力有効化) ---
        mcp.settings.log_level = log_level_upper # type: ignore
        configure_logging(mcp.settings.log_level)

        # ファイルハンドラを追加
        try:
            # 出力ディレクトリがなければ作成
            log_dir = os.path.dirname(config.MCP_SERVER_LOG_FILE)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            file_handler = logging.FileHandler(config.MCP_SERVER_LOG_FILE, encoding='utf-8', mode='a') # 追記モード
            # フォーマットを少し詳細に
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(process)d:%(threadName)s] %(name)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            # ルートロガーに追加することで、すべてのロガーの出力がファイルに行くようにする
            logging.getLogger().addHandler(file_handler)
            # ルートロガーのレベルも設定 (これが全体の閾値になる)
            logging.getLogger().setLevel(log_level_upper)
            logger.info(f"ファイルロギングを有効化しました: {config.MCP_SERVER_LOG_FILE}")
        except Exception as log_file_err:
             logger.error(f"ファイルロギングの設定に失敗しました ({config.MCP_SERVER_LOG_FILE}): {log_file_err}")

        logger.info(f"MCPロガーを設定しました。レベル: {log_level_upper}")
        # --- ▲▲▲ MCPログ設定 (ファイル出力有効化) ▲▲▲ ---

        logger.info(f"Web-Runner MCP サーバーを {transport_lower} トランスポートで起動します...")
        if transport_lower == "sse":
             mcp.settings.host = host
             mcp.settings.port = port
             logger.info(f"SSEサーバーが http://{host}:{port} で待機します")
        # MCPサーバーを実行
        mcp.run(transport=transport_lower) # type: ignore

    # Typerアプリケーションを実行
    cli_app()