# --- ファイル: web_runner_mcp_server.py ---

import json
import logging # logging をインポート
from typing import Any, Dict, List, Literal, Union

# MCP SDK と Pydantic をインポート
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.fastmcp.utilities.logging import configure_logging # MCPのログ設定関数
from pydantic import BaseModel, Field, HttpUrl, AnyUrl

# --- 分割した Web-Runner のコア関数と設定をインポート ---
try:
    import config # 設定値を参照するため
    import utils  # ロギング設定などに使う可能性
    from playwright_handler import run_playwright_automation_async # メインの実行関数
except ImportError as import_err:
    logging.error(f"Error: Failed to import required Web-Runner modules (config, utils, playwright_handler): {import_err}")
    logging.error("Please ensure config.py, utils.py, and playwright_handler.py are in the same directory or accessible via PYTHONPATH.")
    import sys
    sys.exit(1)

# --- ロガー取得 ---
logger = logging.getLogger(__name__)

# --- 入力スキーマ定義 ---
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
    headless: bool = Field(True, description="ヘッドレスモードで実行するかどうか (デフォルトはTrue)")
    slow_mo: int = Field(0, description="各操作間の待機時間 (ミリ秒)", ge=0)
    default_timeout_ms: int | None = Field(None, description=f"デフォルトのアクションタイムアウト(ミリ秒)。省略時はサーバー設定 ({config.DEFAULT_ACTION_TIMEOUT}ms) が使われる。")

# --- FastMCP サーバーインスタンス作成 ---
mcp = FastMCP(
    name="WebRunnerServer",
    instructions="Webサイトの自動操作とデータ抽出を実行するサーバーです。URLと一連のアクションを指定してください。",
    dependencies=["playwright", "PyMuPDF", "fitz"]
)

# --- MCPツール定義 ---
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
    await ctx.debug(f"Input arguments (Pydantic model): {input_args}")

    try:
        target_url_str = str(input_args.target_url)
        actions_list = [step.model_dump(exclude_none=True) for step in input_args.actions]
        effective_default_timeout = input_args.default_timeout_ms if input_args.default_timeout_ms is not None else config.DEFAULT_ACTION_TIMEOUT
        await ctx.info(f"Using effective default timeout: {effective_default_timeout}ms")

        await ctx.debug(f"Calling playwright_handler.run_playwright_automation_async with:")
        await ctx.debug(f"  target_url='{target_url_str}'")
        await ctx.debug(f"  actions={actions_list}")
        await ctx.debug(f"  headless_mode={input_args.headless}")
        await ctx.debug(f"  slow_motion={input_args.slow_mo}")
        await ctx.debug(f"  default_timeout={effective_default_timeout}")

        # --- playwright_handler のコア関数を呼び出す ---
        success, results = await run_playwright_automation_async(
            target_url=target_url_str,
            actions=actions_list,
            headless_mode=input_args.headless,
            slow_motion=input_args.slow_mo,
            default_timeout=effective_default_timeout
        )

        results_json = json.dumps(results, indent=2, ensure_ascii=False)
        await ctx.debug(f"Task finished. Success: {success}. Results JSON (first 500 chars): {results_json[:500]}...")

        if success:
            await ctx.info("Task completed successfully.")
            return results_json
        else:
            await ctx.error("Task failed. Returning error information.")
            raise ToolError(f"Web-Runner task failed. See details in the result content. JSON: {results_json}")

    except ImportError as e:
         await ctx.error(f"Import error within tool execution: {e}")
         raise ToolError(f"Server configuration error: Failed to load core Web-Runner module ({e})")
    except Exception as e:
        await ctx.error(f"Unhandled exception during Web-Runner execution: {e}")#, exc_info=True)
        error_result = [{"step": "MCP Tool Execution", "status": "error", "message": f"Unhandled server error: {type(e).__name__} - {e}"}]
        error_json = json.dumps(error_result, indent=2, ensure_ascii=False)
        raise ToolError(f"Unhandled server error during execution. Details: {error_json}")

# --- サーバー起動設定 ---
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
            "INFO", "--log-level", help="Logging level (DEBUG, INFO, etc.)"
        )
    ):
        """Web-Runner MCP Server"""
        transport_lower = transport.lower()
        if transport_lower not in ["stdio", "sse"]:
            logger.error(f"Invalid transport type: '{transport}'. Must be 'stdio' or 'sse'.")
            raise typer.Exit(code=1)

        log_level_upper = log_level.upper()
        valid_log_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if log_level_upper not in valid_log_levels:
            logger.error(f"Invalid log level: '{log_level}'. Must be one of {valid_log_levels}.")
            raise typer.Exit(code=1)

        # --- ▼▼▼ MCPログ設定 (ファイル出力有効化) ▼▼▼ ---
        mcp.settings.log_level = log_level_upper # type: ignore
        configure_logging(mcp.settings.log_level)

        # ★★★ ファイルハンドラを追加 (コメントアウト解除) ★★★
        try:
            file_handler = logging.FileHandler(config.MCP_SERVER_LOG_FILE, encoding='utf-8', mode='a')
            # サーバーログにはプロセス名なども含めると分かりやすいかも
            file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(process)d] %(name)s - %(message)s')
            file_handler.setFormatter(file_formatter)
            logging.getLogger().addHandler(file_handler) # ルートロガーに追加
            logging.getLogger().setLevel(log_level_upper) # ルートロガーのレベルも設定
            logger.info(f"File logging enabled for: {config.MCP_SERVER_LOG_FILE}")
        except Exception as log_file_err:
             logger.error(f"Failed to configure file logging for {config.MCP_SERVER_LOG_FILE}: {log_file_err}")

        logger.info(f"MCP Logger configured with level: {log_level_upper}")
        # --- ▲▲▲ MCPログ設定 (ファイル出力有効化) ▲▲▲ ---

        logger.info(f"Starting Web-Runner MCP Server with {transport_lower} transport...")
        if transport_lower == "sse":
             mcp.settings.host = host
             mcp.settings.port = port
             logger.info(f"SSE server listening on http://{host}:{port}")
        mcp.run(transport=transport_lower) # type: ignore

    cli_app()