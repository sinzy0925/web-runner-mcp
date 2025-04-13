import os
import fnmatch

def process_file(root, file, output_file):
    """
    指定されたファイルの内容を指定されたフォーマットで出力ファイルに書き込みます。
    """
    filepath = os.path.join(root, file)
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        with open(output_file, "a", encoding="utf-8") as outfile:
            outfile.write("\n\n\n---\n\n\n")
            outfile.write(f"- フォルダ名: {root}\n")
            outfile.write(f"- ファイル名: {file}\n")
            outfile.write("- 内容:\n")
            outfile.write(content)

    except Exception as e:
        print(f"エラー: {filepath} の処理中にエラーが発生しました: {e}")

def should_exclude(filepath, exclude_patterns):
    """
    指定されたファイルパスが除外パターンに一致するかどうかを判定します。
    """
    for pattern in exclude_patterns:
        if fnmatch.fnmatch(filepath, pattern):
            #print(filepath, pattern)
            return True
    return False

def main():
    target_extensions = [".py", ".js", ".html", ".css", ".json"]
    exclude_patterns = ["./venv312/*", ".git/*", "./output/*", "./codeA.py","./__pycache__/*"]  # 除外するファイル/ディレクトリのパターン
    output_file = "code_output.txt"  # 出力ファイル名

    for root, _, files in os.walk("."):
        for file in files:
            if any(file.endswith(ext) for ext in target_extensions):
                filepath = os.path.join(root, file)
                if not should_exclude(filepath, exclude_patterns):
                    process_file(root, file, output_file)  # ファイル名だけでなく、フォルダ名も渡す
                    print("処理:", filepath)
                #else:
                #    print(f"除外: {filepath}")

if __name__ == "__main__":
    main()