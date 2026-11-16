# OS Porter(Windows ⇄ Mac 自動変換)

Pythonソフトを Windows版⇄Mac版 に変換します。**変換方向はソースの内容から自動検知**します。

## 使い方
- Windows: `起動.bat` をダブルクリック(GUI)。ファイル/フォルダを `起動.bat` にドラッグ&ドロップでも可
- Mac: 初回のみ `chmod +x 起動.command` → ダブルクリック。または `python3 os_porter.py`
- CLI: `python os_porter.py <ファイル or フォルダ> [-d w2m|m2w] [-o 出力先] [--dry-run]`

出力は `<名前>_mac` / `<名前>_win`。同じ場所に `変換レポート.txt`(根拠・変換内容・手動対応箇所)が出ます。

## 自動検知
Windows系(winreg, msvcrt, winsound, pywin32, windll, os.startfile, `C:\`, APPDATA, Meiryo 等)と
Mac系(AppKit/pyobjc, osascript, afplay, ~/Library, open コマンド, Hiragino, <Command-> 等)の
特徴をスコア化して判定。同点のときは実行OSから推定(Mac上→Windows用 / Windows上→Mac用)。
`-d` またはGUIのラジオボタンで手動指定も可能。

## 変換される主な項目
| 項目 | Win→Mac | Mac→Win |
|---|---|---|
| ファイルを開く | os.startfile → open | open → os.startfile |
| 音 | winsound → afplay | afplay → winsound |
| 設定保存先 | APPDATA → ~/Library/Application Support | 逆変換 |
| フォント | Meiryo→Hiragino 等 | 逆変換 |
| tkinterキー | Control→Command, 右クリック, wheel delta | 逆変換 |
| パス区切り | `\` → `/` | – |
| open() | – | encoding="utf-8" を付与(cp932文字化け防止) |
| 起動スクリプト | .bat → .command(実行権限付き) | .command/.sh → .bat |
| requirements.txt | pywin32等を無効化 | pyobjc等を無効化 |

## 注意
定型パターンの置換です。winreg / pyobjc などOS固有APIは自動変換できず、レポートに行番号付きで出ます。必ず実機で動作確認してください。
