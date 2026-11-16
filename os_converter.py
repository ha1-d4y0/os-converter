#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OS Porter - Python ソフトを Windows <-> Mac で相互変換するツール

  * 変換元のソースを解析して「Windows向け / Mac向け」を自動検知
  * Windows→Mac / Mac→Windows のどちらか適切な方向へ自動で切り替え
  * 対象: .py / requirements.txt / 起動.bat <-> 起動.command (フォルダ丸ごとも可)
  * 自動変換できない箇所は「変換レポート.txt」に行番号つきで出力

使い方:
  python os_porter.py                      ... GUI を起動
  python os_porter.py <ファイル or フォルダ>  ... 自動検知して変換
  python os_porter.py <パス> -d w2m|m2w    ... 方向を手動指定
  python os_porter.py <パス> --dry-run     ... 判定のみ(書き出さない)
  python os_porter.py <パス> -o <出力先>
"""
import argparse
import os
import platform
import re
import shutil
import stat
import sys
from pathlib import Path

W2M, M2W = "w2m", "m2w"
DIR_LABEL = {W2M: "Windows → Mac", M2W: "Mac → Windows"}
SKIP_DIRS = {".git", "__pycache__", "venv", ".venv", "node_modules", "build", "dist", ".idea", ".vscode"}
SKIP_FILES = {".DS_Store", "Thumbs.db"}
TAG = "[OS Porter]"

# ----------------------------------------------------------------------
# 1. 自動検知
# ----------------------------------------------------------------------
WIN_MARKERS = [
    (r"\b(?:import|from)\s+(?:winreg|_winreg)\b", 3, "winreg"),
    (r"\bmsvcrt\b", 3, "msvcrt"),
    (r"\bwinsound\b", 3, "winsound"),
    (r"\b(?:win32api|win32con|win32gui|win32com|win32file|win32process|pythoncom|pywintypes)\b", 3, "pywin32"),
    (r"\bwindll\b", 3, "ctypes.windll"),
    (r"\bos\.startfile\b", 3, "os.startfile"),
    (r"[A-Za-z]:\\\\|[A-Za-z]:\\\w", 2, "ドライブレター付きパス"),
    (r"%[A-Za-z_]+%|\b(?:APPDATA|LOCALAPPDATA|USERPROFILE|PROGRAMFILES)\b", 2, "Windows環境変数"),
    (r"os\.system\(\s*[\"']cls[\"']", 2, "cls"),
    (r"creationflags|CREATE_NO_WINDOW|STARTUPINFO|startupinfo", 2, "subprocess Windowsフラグ"),
    (r"iconbitmap", 1, "iconbitmap(.ico)"),
    (r"[\"'](?:Meiryo UI|Meiryo|MS Gothic|MS UI Gothic|MS PGothic|Yu Gothic UI|Yu Gothic|Segoe UI|Consolas)[\"']", 1, "Windowsフォント"),
    (r"\.exe[\"']", 1, ".exe 参照"),
    (r"[\"']explorer(?:\.exe)?[\"']", 1, "explorer"),
]
MAC_MARKERS = [
    (r"\b(?:import|from)\s+(?:AppKit|Foundation|Quartz|objc|Cocoa|CoreFoundation|PyObjCTools)\b", 3, "pyobjc系"),
    (r"\bosascript\b", 3, "osascript"),
    (r"\bafplay\b", 3, "afplay"),
    (r"\b(?:pbcopy|pbpaste)\b", 2, "pbcopy/pbpaste"),
    (r"~/Library|[\"']/Library/", 2, "~/Library"),
    (r"/Applications/|/Volumes/|/opt/homebrew|/usr/local/bin", 2, "Mac固有パス"),
    (r"[\"']open[\"']\s*,", 2, "open コマンド"),
    (r"[\"'](?:Hiragino[^\"']*|Osaka|Menlo|Helvetica Neue|SF Pro|SF Mono|Monaco)[\"']", 1, "Macフォント"),
    (r"<Command-", 1, "<Command-> バインド"),
    (r"os\.system\(\s*[\"']clear[\"']", 1, "clear"),
    (r"[\"']darwin[\"']", 1, "darwin 判定"),
]
WIN_REQ = {"pywin32", "pypiwin32", "pywinauto", "wmi", "comtypes", "winshell", "win10toast", "windows-curses",
           "pywin32-ctypes", "winotify", "pyreadline3", "win32-setctime", "pythonnet"}
MAC_REQ = {"py2app", "rumps", "applescript", "appscript", "pync", "mac-notifications", "pasteboard"}


def file_kind(p: Path) -> str:
    n, s = p.name.lower(), p.suffix.lower()
    if s == ".py":
        return "py"
    if n.startswith("requirements") and s == ".txt":
        return "req"
    if s in (".bat", ".cmd"):
        return "bat"
    if s in (".command", ".sh"):
        return "sh"
    return "other"


def read_text(p: Path):
    raw = p.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc).replace("\r\n", "\n").replace("\r", "\n"), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1").replace("\r\n", "\n"), "latin-1"


def score_text(text, markers, ev):
    total = 0
    for rx, w, label in markers:
        c = len(re.findall(rx, text))
        if c:
            total += w * min(c, 3)
            ev[label] = ev.get(label, 0) + c
    return total


def detect(files):
    """files: [(Path, kind, text)] -> (win_score, mac_score, win_ev, mac_ev)"""
    ws = ms = 0
    wev, mev = {}, {}
    for p, kind, text in files:
        if kind == "py":
            ws += score_text(text, WIN_MARKERS, wev)
            ms += score_text(text, MAC_MARKERS, mev)
        elif kind == "req":
            for l in text.splitlines():
                s = l.strip()
                if not s or s.startswith(("#", "-")):
                    continue
                name = re.split(r"[<>=!~;\[\s@]", s, maxsplit=1)[0].lower().replace("_", "-")
                if name in WIN_REQ:
                    ws += 2; wev[f"requirements:{name}"] = 1
                if name.startswith("pyobjc") or name in MAC_REQ:
                    ms += 2; mev[f"requirements:{name}"] = 1
        elif kind == "bat":
            ws += 2; wev["バッチファイル"] = wev.get("バッチファイル", 0) + 1
        elif kind == "sh":
            ms += 2 if p.suffix.lower() == ".command" else 1
            mev["シェル/.command"] = mev.get("シェル/.command", 0) + 1
    return ws, ms, wev, mev


def decide(ws, ms, forced=None):
    """方向決定。内容で判定、同点なら実行OSから推定 (Mac上→Windows用 / Windows上→Mac用)。"""
    if forced in (W2M, M2W):
        return forced, "手動指定"
    if ws > ms:
        return W2M, "ソース内容から自動検知 (Windows向けコードと判定)"
    if ms > ws:
        return M2W, "ソース内容から自動検知 (Mac向けコードと判定)"
    if platform.system() == "Darwin":
        return M2W, "内容で判定不能 → 実行OS(Mac)から推定"
    return W2M, "内容で判定不能 → 実行OS(Windows/他)から推定"


# ----------------------------------------------------------------------
# 2. 変換ルール
# ----------------------------------------------------------------------
class Rule:
    def __init__(self, desc, pattern, repl, flags=0, imports=()):
        self.desc, self.rx, self.repl, self.imports = desc, re.compile(pattern, flags), repl, imports


def ensure_import(text, mod):
    if re.search(rf"^\s*import\s+(?:[\w.]+\s*,\s*)*{mod}\b", text, re.M):
        return text
    lines = text.split("\n")
    idx, last_future = None, -1
    for i, l in enumerate(lines[:300]):
        if l.startswith("from __future__"):
            last_future = i
        elif re.match(r"(import|from)\s+\S", l):
            idx = i
            break
    if idx is None:
        idx = last_future + 1
        if last_future < 0:
            while idx < len(lines) and (lines[idx].startswith("#") or not lines[idx].strip()):
                idx += 1
    lines.insert(idx, f"import {mod}")
    return "\n".join(lines)


def _font_rule(table, desc):
    names = sorted(table, key=len, reverse=True)
    rx = r"""(["'])(""" + "|".join(re.escape(n) for n in names) + r""")\1"""
    return Rule(desc, rx, lambda m: f"{m.group(1)}{table[m.group(2)]}{m.group(1)}")


def _fix_path(m):
    s = m.group(0)
    if re.search(r"\\{1,2}[dswDSWb](?!\w)", s):  # 正規表現らしきものは触らない
        return s
    return s.replace("\\\\", "/").replace("\\", "/")


FONT_W2M = {"Meiryo UI": "Hiragino Sans", "Meiryo": "Hiragino Sans", "MS UI Gothic": "Osaka", "MS Gothic": "Osaka",
            "MS PGothic": "Osaka", "Yu Gothic UI": "Hiragino Sans", "Yu Gothic": "Hiragino Sans",
            "Yu Mincho": "Hiragino Mincho ProN", "Segoe UI": "Helvetica Neue", "Consolas": "Menlo",
            "Courier New": "Courier"}
FONT_M2W = {"Hiragino Sans": "Meiryo UI", "Hiragino Kaku Gothic ProN": "Meiryo", "Hiragino Kaku Gothic Pro": "Meiryo",
            "Hiragino Maru Gothic ProN": "Meiryo", "Hiragino Mincho ProN": "Yu Mincho", "Osaka": "MS Gothic",
            "Helvetica Neue": "Segoe UI", "Menlo": "Consolas", "SF Pro": "Segoe UI", "SF Mono": "Consolas",
            "Monaco": "Consolas", "Courier": "Courier New", "Helvetica": "Arial", "San Francisco": "Segoe UI"}


def _startfile(m):
    return f'subprocess.run(["open", {m.group(1).strip()}])'


def _playsound(m):
    fn = "Popen" if "SND_ASYNC" in m.group(0) else "run"
    return f'subprocess.{fn}(["afplay", {m.group(1).strip()}])'


def _appsupport(m):
    sub = (m.group(1) or "").strip("/")
    base = 'os.environ.get("APPDATA", os.path.expanduser("~"))'
    return f'os.path.join({base}, "{sub}")' if sub else base


def _caches(m):
    sub = (m.group(1) or "").strip("/")
    base = 'os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))'
    return f'os.path.join({base}, "{sub}")' if sub else base


PASS_WIN_IMPORT = Rule(
    "Windows専用モジュールの import を無効化",
    r"^(\s*)((?:import|from)\s+(?:winreg|_winreg|msvcrt|winsound|win32\w*|pythoncom|pywintypes|wmi|comtypes|winshell)\b.*)$",
    lambda m: f"{m.group(1)}pass  # {TAG} Windows専用: {m.group(2).strip()}", re.M)
PASS_MAC_IMPORT = Rule(
    "Mac/Unix専用モジュールの import を無効化",
    r"^(\s*)((?:import|from)\s+(?:AppKit|Foundation|Quartz|objc|Cocoa|CoreFoundation|PyObjCTools|fcntl|termios|pwd|grp)\b.*)$",
    lambda m: f"{m.group(1)}pass  # {TAG} Mac/Unix専用: {m.group(2).strip()}", re.M)

RULES = {
    W2M: [
        Rule("os.startfile → subprocess.run(['open', …])",
             r"\bos\.startfile\(\s*((?:[^()\n]|\([^()\n]*\))*?)\s*\)", _startfile, 0, ("subprocess",)),
        Rule("winsound.PlaySound → afplay",
             r"\bwinsound\.PlaySound\(\s*([^,()\n]+?)\s*(?:,[^()\n]*)?\)", _playsound, 0, ("subprocess",)),
        Rule("winsound.Beep/MessageBeep → ベル文字",
             r"\bwinsound\.(?:Beep|MessageBeep)\([^()\n]*\)", lambda m: 'print("\\a", end="", flush=True)'),
        Rule("os.system('cls') → 'clear'", r"""(os\.system\(\s*)(["'])cls\2""",
             lambda m: f"{m.group(1)}{m.group(2)}clear{m.group(2)}"),
        Rule("os.system('pause') → input()", r"""os\.system\(\s*["']pause(?:\s*>\s*nul)?["']\s*\)""",
             lambda m: 'input("Enterキーで続行...")'),
        Rule("APPDATA系 → ~/Library/Application Support",
             r"""\bos\.(?:environ\[\s*["'](?:APPDATA|LOCALAPPDATA|PROGRAMDATA)["']\s*\]|(?:getenv|environ\.get)\(\s*["'](?:APPDATA|LOCALAPPDATA|PROGRAMDATA)["'][^()\n]*\))""",
             lambda m: 'os.path.expanduser("~/Library/Application Support")'),
        Rule("USERPROFILE → ~",
             r"""\bos\.(?:environ\[\s*["'](?:USERPROFILE|HOMEPATH)["']\s*\]|(?:getenv|environ\.get)\(\s*["'](?:USERPROFILE|HOMEPATH)["'][^()\n]*\))""",
             lambda m: 'os.path.expanduser("~")'),
        Rule("TEMP/TMP → tempfile.gettempdir()",
             r"""\bos\.(?:environ\[\s*["'](?:TEMP|TMP)["']\s*\]|(?:getenv|environ\.get)\(\s*["'](?:TEMP|TMP)["'][^()\n]*\))""",
             lambda m: "tempfile.gettempdir()", 0, ("tempfile",)),
        Rule("creationflags= を削除", r",\s*creationflags\s*=\s*[\w.]+(?:\s*\|\s*[\w.]+)*", ""),
        Rule("creationflags= を削除(先頭引数)", r"\bcreationflags\s*=\s*[\w.]+(?:\s*\|\s*[\w.]+)*\s*,?\s*", ""),
        Rule("DPI/AppUserModelID 設定を無効化",
             r"^(\s*)((?:ctypes\.)?windll\.\w+\.(?:SetProcessDpiAwareness|SetCurrentProcessExplicitAppUserModelID|SetProcessDPIAware)\(.*\))[ \t]*$",
             lambda m: f"{m.group(1)}pass  # {TAG} Windows専用: {m.group(2).strip()}", re.M),
        Rule("iconbitmap(.ico) を無効化", r"^(\s*)(.*\.iconbitmap\(.*\))[ \t]*$",
             lambda m: f"{m.group(1)}pass  # {TAG} macは.ico非対応: {m.group(2).strip()}", re.M),
        Rule("explorer → open", r"""(["'])explorer(?:\.exe)?\1""", lambda m: '"open"'),
        Rule("ツールの .exe を除去", r"""(["'])(ffmpeg|ffprobe|ffplay|yt-dlp|magick)\.exe\1""",
             lambda m: f"{m.group(1)}{m.group(2)}{m.group(1)}"),
        Rule("python(w).exe → python3", r"""(["'])pythonw?(?:\.exe)?\1""", lambda m: f"{m.group(1)}python3{m.group(1)}"),
        Rule("Ctrl → Command (tkinterバインド)", r"<Control-", "<Command-"),
        Rule("右クリック <Button-3> → <Button-2>", r"<(Button|ButtonRelease)-3>", lambda m: f"<{m.group(1)}-2>"),
        Rule("マウスホイール event.delta/120 → event.delta", r"\.delta\s*/\s*120\b", ".delta"),
        _font_rule(FONT_W2M, "フォント名を Mac 用へ置換"),
        Rule("パス区切り \\ → /", r"""(["'])(?:[\w .\-]+\\\\)+[\w .\-]*\1""", _fix_path),
        Rule("パス区切り(raw文字列) \\ → /", r"""\br(["'])(?:[\w .\-]+\\)+[\w .\-]*\1""", _fix_path),
        PASS_WIN_IMPORT,
    ],
    M2W: [
        Rule("subprocess(['open', x]) → os.startfile(x)",
             r"""\bsubprocess\.(?:run|call|Popen|check_call)\(\s*\[\s*["']open["']\s*,\s*([^\],]+?)\s*\]\s*(?:,\s*check\s*=\s*\w+\s*)?\)""",
             lambda m: f"os.startfile({m.group(1).strip()})", 0, ("os",)),
        Rule("subprocess(['afplay', x]) → winsound.PlaySound(x)",
             r"""\bsubprocess\.(?:run|call|Popen)\(\s*\[\s*["']afplay["']\s*,\s*([^\],]+?)\s*\]\s*(?:,[^()\n]*)?\)""",
             lambda m: f"winsound.PlaySound({m.group(1).strip()}, winsound.SND_FILENAME)", 0, ("winsound",)),
        Rule("os.system('clear') → 'cls'", r"""(os\.system\(\s*)(["'])clear\2""",
             lambda m: f"{m.group(1)}{m.group(2)}cls{m.group(2)}"),
        Rule("~/Library/Application Support → APPDATA",
             r"""\bos\.path\.expanduser\(\s*["']~/Library/Application Support(?:/([^"']*))?["']\s*\)""",
             _appsupport, 0, ("os",)),
        Rule("~/Library/Caches|Logs → LOCALAPPDATA",
             r"""\bos\.path\.expanduser\(\s*["']~/Library/(?:Caches|Logs)(?:/([^"']*))?["']\s*\)""",
             _caches, 0, ("os",)),
        Rule("python3 → python", r"""(["'])python3\1""", lambda m: f"{m.group(1)}python{m.group(1)}"),
        Rule("Command → Ctrl (tkinterバインド)", r"<Command-", "<Control-"),
        Rule("右クリック <Button-2> → <Button-3>", r"<(Button|ButtonRelease)-2>", lambda m: f"<{m.group(1)}-3>"),
        Rule("マウスホイール event.delta → event.delta/120",
             r"\b(event|evt|e)\.delta\b(?!\s*/)", lambda m: f"{m.group(1)}.delta / 120"),
        _font_rule(FONT_M2W, "フォント名を Windows 用へ置換"),
        PASS_MAC_IMPORT,
    ],
}

WARN = {
    W2M: [
        (r"\b(?:winreg|_winreg|msvcrt|win32\w+|pythoncom|pywintypes|wmi|comtypes|winshell)\b",
         "Windows専用API: Mac では別実装が必要 (例: plistlib / defaults / pyobjc / termios)"),
        (r"\bwindll\b", "ctypes.windll: Mac では使えません。別手段が必要"),
        (r"[A-Za-z]:\\\\|[A-Za-z]:\\\w", "ドライブレター付きパス: Mac のパスへ書き換えが必要"),
        (r"%[A-Za-z_]+%", "%VAR% 形式の環境変数: os.environ で取得してください"),
        (r"""["'][^"'\n]*\\\\\w[^"'\n]*["']""", "バックスラッシュ区切りのパスが残っています (os.path.join 推奨)"),
        (r"shell\s*=\s*True", "shell=True: dir/copy/del 等の Windows コマンドは Mac で動きません"),
        (r"startupinfo|STARTUPINFO", "STARTUPINFO: Windows専用。削除が必要"),
        (r"\.ico\b", ".ico: Mac のアイコンは .icns (PyInstaller等)"),
        (r"<MouseWheel>", "マウスホイール: Mac は event.delta の単位が異なる/横スクロールの扱い確認"),
        (r"/select\s*,", "explorer /select: Finder では open -R <path> が相当"),
        (r"\bwinsound\.", "winsound の未変換呼び出しが残っています"),
        (r"\bos\.getlogin\b|\bos\.startfile\b", "要確認 (Windows前提の呼び出し)"),
    ],
    M2W: [
        (r"\b(?:AppKit|Foundation|Quartz|objc|Cocoa|CoreFoundation|PyObjCTools)\b", "pyobjc系API: Windows では別実装が必要"),
        (r"\bosascript\b", "osascript(AppleScript): Windows では PowerShell / pywin32 等で代替"),
        (r"/Applications/|/Library/|/Users/|/opt/homebrew|/usr/local/bin|/Volumes/|/tmp\b", "Mac固有パス: Windows のパスへ書き換えが必要"),
        (r"\b(?:pbcopy|pbpaste)\b", "pbcopy/pbpaste: tkinter の clipboard_* や pyperclip で代替"),
        (r"""["']open["']""", "open コマンド(引数付き)が残っています: os.startfile / explorer で代替"),
        (r"\bafplay\b", "afplay の未変換呼び出し (winsound は WAV のみ対応)"),
        (r"\.app\b", ".app: Windows では .exe (PyInstaller 等)"),
        (r"os\.fork\b|set_start_method\(\s*[\"']fork", "fork: Windows では使えません (spawn)"),
        (r"~/Library", "~/Library: Windows では APPDATA / LOCALAPPDATA"),
        (r"\bsay\b\s*[\"']|[\"']say[\"']", "say コマンド: Windows では pyttsx3 等で代替"),
    ],
}


def add_utf8_open(text):
    """encoding 未指定の open() に encoding='utf-8' を付与 (Windows既定cp932での文字化け防止)"""
    rx = re.compile(r"(?<![\w.])open\(")
    edits, skip_until = [], 0
    for m in rx.finditer(text):
        if m.start() < skip_until:
            continue
        i, depth, q, args, last = m.end(), 1, None, [], m.end()
        while i < len(text) and depth > 0:
            c = text[i]
            if q:
                if c == "\\":
                    i += 2
                    continue
                if c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif c == "," and depth == 1:
                args.append(text[last:i]); last = i + 1
            i += 1
        if depth:
            continue
        args.append(text[last:i - 1])
        inner = text[m.end():i - 1]
        if not inner.strip() or "encoding" in inner or "**" in inner:
            continue
        if len(args) >= 2:
            a2 = args[1].strip()
            if not a2.startswith(("'", '"')) and not re.match(r"\w+\s*=", a2):
                continue  # mode が変数 → バイナリか不明
        if any("b" in md for md in re.findall(r"""["']([rwxabt+U]+)["']""", inner)):
            continue
        ins = i - 1
        stripped = text[:ins].rstrip()
        sep = " " if stripped.endswith(",") else ", "
        edits.append((len(stripped) if stripped.endswith(",") else ins, f'{sep}encoding="utf-8"' if sep == ", " else ' encoding="utf-8"'))
        skip_until = i
    for pos, s in sorted(edits, reverse=True):
        text = text[:pos] + s + text[pos:]
    return text, len(edits)


def add_header(text, direction):
    lines = text.split("\n")
    if direction == W2M and not (lines and lines[0].startswith("#!")):
        lines.insert(0, "#!/usr/bin/env python3")
    idx = 1 if lines and lines[0].startswith("#!") else 0
    if idx < len(lines) and re.match(r"#.*coding[:=]", lines[idx]):
        idx += 1
    lines.insert(idx, f"# {TAG} {DIR_LABEL[direction]} へ自動変換 (要動作確認。詳細は 変換レポート.txt)")
    return "\n".join(lines)


def scan_warnings(text, direction):
    out = []
    for n, line in enumerate(text.split("\n"), 1):
        if TAG in line and "pass" in line:
            continue
        for rx, msg in WARN[direction]:
            if re.search(rx, line):
                out.append(f"L{n}: {msg}\n      > {line.strip()[:90]}")
    return out


def convert_py(text, direction):
    changes = []
    for r in RULES[direction]:
        text, n = r.rx.subn(r.repl, text)
        if n:
            changes.append(f"{r.desc} ×{n}")
            for mod in r.imports:
                text = ensure_import(text, mod)
    if direction == M2W:
        text, n = add_utf8_open(text)
        if n:
            changes.append(f"open() に encoding='utf-8' を付与 ×{n} (Windows既定cp932の文字化け防止)")
    text = add_header(text, direction)
    return text, changes, scan_warnings(text, direction)


def convert_req(text, direction):
    out, changes = [], []
    for l in text.splitlines():
        s = l.strip()
        if s and not s.startswith(("#", "-")):
            name = re.split(r"[<>=!~;\[\s@]", s, maxsplit=1)[0].lower().replace("_", "-")
            bad = (direction == W2M and name in WIN_REQ) or \
                  (direction == M2W and (name.startswith("pyobjc") or name in MAC_REQ))
            if bad:
                os_name = "Windows" if direction == W2M else "Mac"
                out.append(f"# {TAG} {os_name}専用のため無効化: {s}")
                changes.append(f"{s} を無効化")
                continue
        out.append(l)
    warns = [f"{c} → 代替ライブラリの検討が必要" for c in changes]
    return "\n".join(out) + "\n", changes, warns


# ---- 起動スクリプト ----------------------------------------------------
def _bat_line(s):
    """バッチ1行(構文外)をシェル向けに変換"""
    s = re.sub(r"call\s+(\.?venv)\\Scripts\\activate(?:\.bat)?", r"source \1/bin/activate", s, flags=re.I)
    s = re.sub(r"(?<![\w./\\-])(?:py\s+-3|py|pythonw?(?:\.exe)?)(?=\s)", "python3", s, flags=re.I)
    s = re.sub(r"(?<!-m )(?<![\w.-])pip(?=\s+(?:install|uninstall|list|freeze|show)\b)", "python3 -m pip", s)
    s = re.sub(r'^set\s+"?([A-Za-z_]\w*)=(.*?)"?$', r'\1="\2"', s, flags=re.I)
    s = re.sub(r"%~?([1-9])", r'"$\1"', s)
    s = s.replace("%*", '"$@"')
    s = re.sub(r"%([A-Za-z_]\w*)%", r"${\1}", s)
    s = re.sub(r'^start\s+(?:"[^"]*"\s+)?', "open ", s, flags=re.I)
    s = re.sub(r"^cls\b", "clear", s, flags=re.I)
    s = re.sub(r"^del\s+(?:/\w\s+)*", "rm -f ", s, flags=re.I)
    s = re.sub(r"^copy\s+", "cp ", s, flags=re.I)
    s = re.sub(r"^move\s+", "mv ", s, flags=re.I)
    s = re.sub(r"2?>\s*nul", lambda m: m.group(0).replace("nul", "/dev/null"), s, flags=re.I)
    s = s.replace("\\", "/").replace("%~dp0", "./")
    m = re.match(r"echo\.?(?:\s+(.*))?$", s, re.I)
    if m and not re.search(r"\s[12]?>", s) and "|" not in s:
        body = m.group(1) or ""
        if body in ("", "."):
            return "echo"
        if "${" in body:
            return 'echo "' + body.replace("\\", "\\\\").replace('"', '\\"') + '"'
        return "echo '" + body.replace("'", "'\\''") + "'"
    return s


def bat_to_command(text):
    out, warns, depth = ["#!/bin/bash"], [], 0

    def emit(line, d=None):
        out.append("    " * (depth if d is None else d) + line)

    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip().lstrip("@")
        low = s.lower()
        if not s:
            out.append(""); continue
        if low in ("echo off", "endlocal") or low.startswith(("setlocal", "chcp", "title ")):
            continue
        if low == "rem" or low.startswith(("rem ", "::")):
            emit("# " + re.sub(r"^(?i:rem|::)\s*", "", s)); continue
        if re.match(r'cd\s+/d\s+"?%~dp0', low):
            emit('cd "$(dirname "$0")"'); continue
        if low == "pause":
            emit('read -n 1 -s -r -p "何かキーを押すと閉じます..."'); emit("echo"); continue
        m = re.match(r"exit(?:\s+/b)?\s*(\d*)$", low)
        if m:
            emit(f"exit {m.group(1)}".strip()); continue
        # --- ブロック構文 ---
        m = re.match(r"if\s+errorlevel\s+(\d+)\s*\($", low)
        if m:
            emit("if [ $? -ge %s ]; then" % m.group(1)); depth += 1; continue
        m = re.match(r"if\s+errorlevel\s+(\d+)\s+(.+)$", s, re.I)
        if m:
            emit("if [ $? -ge %s ]; then %s; fi" % (m.group(1), _bat_line(m.group(2)))); continue
        m = re.match(r'if\s+(/i\s+)?(not\s+)?"?%(\w+)%"?\s*==\s*"?([^"\s()]*)"?\s*\($', s, re.I)
        if m:
            ci, neg, var, val = bool(m.group(1)), m.group(2), m.group(3), m.group(4)
            lhs = f'"${{{var}}}"'
            if ci:
                lhs = f'"$(printf %s "${{{var}}}" | tr \'[:lower:]\' \'[:upper:]\')"'
                val = val.upper()
            emit(f'if [ {lhs} {"!=" if neg else "="} "{val}" ]; then'); depth += 1; continue
        m = re.match(r'if\s+(not\s+)?exist\s+("[^"]+"|\S+?)\s*\($', s, re.I)
        if m:
            emit(f'if [ {"! " if m.group(1) else ""}-e {_bat_line(m.group(2))} ]; then'); depth += 1; continue
        if re.match(r"\)\s*else\s*\($", low) or low == "else (":
            depth = max(depth - 1, 0); emit("else"); depth += 1; continue
        if low == ")":
            depth = max(depth - 1, 0); emit("fi"); continue
        s = _bat_line(s)
        if re.match(r"(if|goto|call\s+:|for)\b", s, re.I) or s[:1] == ":":
            emit(f"# {TAG} 要手動確認: {s}")
            warns.append(f"L{n}: 制御構文は自動変換できません > {s[:80]}")
            continue
        m = re.match(r"set\s+/p\s+(\w+)=(.*)$", s, re.I)
        if m:
            prompt = m.group(2).strip().strip('"').replace('"', '\\"')
            emit(f'read -r -p "{prompt}" {m.group(1)}'); continue
        emit(s)
    if depth:
        warns.append("括弧ブロックの対応が取れませんでした。手動で確認してください")
    return "\n".join(out) + "\n", warns


def command_to_bat(text):
    out, warns = ["@echo off", "chcp 65001 >nul"], []
    has_pause = False
    for n, raw in enumerate(text.splitlines(), 1):
        s = raw.strip()
        if s.startswith("#!") or not s and not out[-1:] == [""]:
            if not s:
                out.append("")
            continue
        if s.startswith("#"):
            out.append("rem " + s.lstrip("# ")); continue
        if re.match(r"(set\s+-|chmod\b)", s):
            continue
        if re.match(r"cd\s+.*dirname.*\$0", s):
            out.append('cd /d "%~dp0"'); continue
        if re.match(r"read\b", s):
            out.append("pause"); has_pause = True; continue
        if s == "echo":
            out.append("echo."); continue
        m = re.match(r"exit\s*(\d*)$", s)
        if m:
            out.append(f"exit /b {m.group(1)}".strip()); continue
        s = re.sub(r"(?:source|\.)\s+(\.?venv)/bin/activate", r"call \1\\Scripts\\activate.bat", s)
        s = re.sub(r"\bpython3\b", "python", s)
        s = re.sub(r"\bpip3\b", "pip", s)
        s = re.sub(r"^open\s+(.+)$", r'start "" \1', s)
        s = re.sub(r"^clear$", "cls", s)
        s = re.sub(r"^rm\s+(?:-\w+\s+)*", "del ", s)
        s = re.sub(r"^cp\s+", "copy ", s)
        s = re.sub(r"^mv\s+", "move ", s)
        s = s.replace("/dev/null", "nul").replace('"$@"', "%*").replace("$@", "%*")
        s = re.sub(r'"?\$([1-9])"?', r"%\1", s)
        s = re.sub(r'^([A-Za-z_]\w*)=(.*)$', lambda m: f'set "{m.group(1)}={m.group(2).strip(chr(34) + chr(39))}"', s)
        s = re.sub(r"\$\{?([A-Za-z_]\w*)\}?", r"%\1%", s)
        if re.match(r"(if|then|else|elif|fi|for|while|do|done|case|esac|function)\b", s) or "[[" in s:
            out.append(f"rem {TAG} 要手動確認: {s}")
            warns.append(f"L{n}: 制御構文は自動変換できません > {s[:80]}")
            continue
        out.append(s)
    if not has_pause and out[-1].strip() != "pause":
        out.append("pause")
    return "\n".join(out) + "\n", warns


# ----------------------------------------------------------------------
# 3. ファイル/フォルダ処理
# ----------------------------------------------------------------------
def iter_files(root: Path, out_dir: Path):
    out_r = out_dir.resolve()
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS and (Path(dp) / d).resolve() != out_r]
        for f in fn:
            if f not in SKIP_FILES:
                yield Path(dp) / f


def default_out(src: Path, direction):
    suf = "_mac" if direction == W2M else "_win"
    return src.with_name(src.name + suf) if src.is_dir() else src.with_name(src.stem + suf + src.suffix)


def new_name_for(p: Path, direction):
    k = file_kind(p)
    if direction == W2M and k == "bat":
        return p.with_suffix(".command")
    if direction == M2W and k == "sh":
        return p.with_suffix(".bat")
    return p


def write_out(dst: Path, kind, direction, text):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if kind == "bat" and direction == M2W:
        dst.write_bytes(text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8"))
    else:
        dst.write_text(text, encoding="utf-8", newline="\n")
    if direction == W2M and dst.suffix == ".command":
        dst.chmod(dst.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def run(src, forced=None, out=None, dry=False):
    src = Path(src).expanduser().resolve()
    if not src.exists():
        raise FileNotFoundError(f"見つかりません: {src}")
    probe_out = Path(out).resolve() if out else default_out(src, forced or W2M)
    paths = list(iter_files(src, probe_out)) if src.is_dir() else [src]
    texts = []
    for p in paths:
        k = file_kind(p)
        if k != "other":
            texts.append((p, k, read_text(p)[0]))
    ws, ms, wev, mev = detect(texts)
    direction, reason = decide(ws, ms, forced)
    rep = [f"=== {APP_NAME} 変換レポート ===", f"入力: {src}", f"方向: {DIR_LABEL[direction]}  ({reason})",
           f"検知スコア  Windows向け={ws}  Mac向け={ms}"]
    if wev:
        rep.append("  Windows根拠: " + ", ".join(f"{k}×{v}" for k, v in wev.items()))
    if mev:
        rep.append("  Mac根拠    : " + ", ".join(f"{k}×{v}" for k, v in mev.items()))
    if dry:
        rep.append("\n(判定のみ: ファイルは書き出していません)")
        return "\n".join(rep), None, direction

    out_path = Path(out).resolve() if out else default_out(src, direction)
    if src.is_dir():
        if out_path.exists():
            shutil.rmtree(out_path)
        out_path.mkdir(parents=True)
        targets = [(p, out_path / new_name_for(p, direction).relative_to(src)) for p in iter_files(src, out_path)]
    else:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        targets = [(src, new_name_for(out_path, direction))]
    manual, n_conv = [], 0
    rep.append("\n--- 変換内容 ---")
    for p, dst in targets:
        k = file_kind(p)
        rel = p.name if not src.is_dir() else str(p.relative_to(src))
        text = changes = warns = None
        if k == "py":
            text, changes, warns = convert_py(read_text(p)[0], direction)
        elif k == "req":
            text, changes, warns = convert_req(read_text(p)[0], direction)
        elif k == "bat" and direction == W2M:
            text, warns = bat_to_command(read_text(p)[0]); changes = [f"{p.suffix} → .command に変換"]
        elif k == "sh" and direction == M2W:
            text, warns = command_to_bat(read_text(p)[0]); changes = [f"{p.suffix} → .bat に変換"]
        if text is None:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, dst)
            continue
        write_out(dst, k, direction, text)
        n_conv += 1
        rep.append(f"[{rel}] → {dst.name}")
        rep += [f"   ・{c}" for c in changes] or ["   ・(自動置換なし)"]
        for w in warns:
            manual.append(f"[{rel}] {w}")
    rep.append(f"\n変換ファイル数: {n_conv}")
    rep.append("\n--- 手動対応が必要・要確認の箇所 ---")
    rep += manual or ["(なし)"]
    rep.append("\n※ 自動変換は定型パターンの置換です。必ず実機で動作確認してください。")
    report = "\n".join(rep)
    rp = (out_path if src.is_dir() else out_path.parent) / "変換レポート.txt"
    rp.write_text(report, encoding="utf-8")
    return report, out_path, direction


# ----------------------------------------------------------------------
# 4. GUI / CLI
# ----------------------------------------------------------------------
APP_NAME = "OS Porter"


def launch_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext
    font = "Hiragino Sans" if platform.system() == "Darwin" else "Meiryo UI"
    root = tk.Tk()
    root.title("OS Porter - Windows ⇄ Mac 変換")
    root.geometry("760x560")
    path_var, dir_var = tk.StringVar(), tk.StringVar(value="auto")
    top = tk.Frame(root); top.pack(fill="x", padx=10, pady=8)
    tk.Entry(top, textvariable=path_var, font=(font, 11)).pack(side="left", fill="x", expand=True)
    tk.Button(top, text="ファイル", command=lambda: path_var.set(filedialog.askopenfilename() or path_var.get())).pack(side="left", padx=3)
    tk.Button(top, text="フォルダ", command=lambda: path_var.set(filedialog.askdirectory() or path_var.get())).pack(side="left")
    mid = tk.Frame(root); mid.pack(fill="x", padx=10)
    for label, val in (("自動検知", "auto"), ("Windows → Mac", W2M), ("Mac → Windows", M2W)):
        tk.Radiobutton(mid, text=label, variable=dir_var, value=val, font=(font, 11)).pack(side="left", padx=6)
    log = scrolledtext.ScrolledText(root, font=(font, 10)); log.pack(fill="both", expand=True, padx=10, pady=8)

    def go(dry):
        p = path_var.get().strip().strip('"')
        if not p:
            messagebox.showwarning(APP_NAME, "ファイルまたはフォルダを選択してください"); return
        try:
            rep, outp, _ = run(p, None if dir_var.get() == "auto" else dir_var.get(), dry=dry)
        except Exception as e:  # noqa
            rep, outp = f"エラー: {e}", None
        log.delete("1.0", "end"); log.insert("end", rep)
        if outp:
            log.insert("end", f"\n\n出力先: {outp}")

    bt = tk.Frame(root); bt.pack(pady=(0, 10))
    tk.Button(bt, text="判定のみ", width=14, command=lambda: go(True)).pack(side="left", padx=6)
    tk.Button(bt, text="変換実行", width=14, command=lambda: go(False)).pack(side="left", padx=6)
    root.mainloop()


def main():
    ap = argparse.ArgumentParser(description="Python ソフトを Windows ⇄ Mac で自動変換 (方向は自動検知)")
    ap.add_argument("path", nargs="?", help="変換するファイル or フォルダ (省略で GUI)")
    ap.add_argument("-d", "--direction", choices=["auto", W2M, M2W], default="auto")
    ap.add_argument("-o", "--out", help="出力先")
    ap.add_argument("--dry-run", action="store_true", help="判定のみ")
    a = ap.parse_args()
    if not a.path:
        try:
            launch_gui()
        except Exception as e:  # noqa
            print(f"GUI を起動できませんでした ({e})。パスを引数に指定してください。")
            ap.print_help()
        return
    try:
        rep, outp, _ = run(a.path, None if a.direction == "auto" else a.direction, a.out, a.dry_run)
    except Exception as e:  # noqa
        print(f"エラー: {e}")
        sys.exit(1)
    print(rep)
    if outp:
        print(f"\n出力先: {outp}")


if __name__ == "__main__":
    main()
