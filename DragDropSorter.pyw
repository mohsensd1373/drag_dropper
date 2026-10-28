# -*- coding: utf-8 -*-
"""
DragDropSorter
یک برنامه کوچک سینی سیستم (System Tray) برای ویندوز.
"""

import os
import sys
import json
import shutil
import threading
import queue
import errno
import time
import datetime
import ctypes
from ctypes import wintypes
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import winreg  # فقط روی ویندوز (برای قابلیت اجرای خودکار)
except Exception:  # noqa: BLE001
    winreg = None

APP_NAME = "DragDropSorter"
POLL_MS = 40  # فاصله‌ی حلقه‌ی پایش نخ UI (میلی‌ثانیه)


def resource_path(name):
    """مسیر فایل همراه (چه حالت اسکریپت، چه exe ساخته‌شده با PyInstaller)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        p = os.path.join(meipass, name)
        if os.path.exists(p):
            return p
    if getattr(sys, "frozen", False):
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, name)


ICON_PATH = resource_path("DragDropSorter.ico")


def config_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


CONFIG_PATH = os.path.join(config_dir(), "config.json")

DEFAULT_CONFIG = {
    # هر دسته: {"name": str, "path": str, "mode": "ask"|"copy"|"move"}
    "categories": [],
    # کلید میانبر پیش‌فرض: Ctrl + Shift + D
    "hotkey": {"ctrl": True, "shift": True, "alt": False, "win": False, "key": "D"},
    # آخرین مقصدهای استفاده‌شده: [{"name":..., "path":...}]
    "recent": [],
    # آیکن شناور کوچک روی صفحه
    "dock": {"visible": True, "x": None, "y": None},
    # وضوح بالا در مانیتورهای High-DPI
    "dpi_aware": True,
    # بعد از انتقال، باکس به‌طور خودکار بسته شود؟
    "close_after": True,
    # باکس در کدام گوشه باز شود: br/bl/tr/tl/center
    "box_corner": "br",
}


def load_config():
    cfg = {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f) or {}
    except Exception:
        cfg = {}
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(cfg, dict):
        if isinstance(cfg.get("categories"), list):
            merged["categories"] = cfg["categories"]
        if isinstance(cfg.get("hotkey"), dict):
            merged["hotkey"].update(cfg["hotkey"])
        if isinstance(cfg.get("recent"), list):
            merged["recent"] = [r for r in cfg["recent"] if isinstance(r, dict)][:5]
        if isinstance(cfg.get("dock"), dict):
            merged["dock"].update(cfg["dock"])
        if isinstance(cfg.get("dpi_aware"), bool):
            merged["dpi_aware"] = cfg["dpi_aware"]
        if isinstance(cfg.get("close_after"), bool):
            merged["close_after"] = cfg["close_after"]
        if cfg.get("box_corner") in ("br", "bl", "tr", "tl", "center"):
            merged["box_corner"] = cfg["box_corner"]
    return merged


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


MODE_LABELS = {"ask": "پرسیده شود", "copy": "کپی", "move": "انتقال"}
LABEL_TO_MODE = {v: k for k, v in MODE_LABELS.items()}

# گوشه‌ی باز شدن باکس روی صفحه
CORNER_LABELS = {
    "br": "پایین-راست",
    "bl": "پایین-چپ",
    "tr": "بالا-راست",
    "tl": "بالا-چپ",
    "center": "وسط صفحه",
}
LABEL_TO_CORNER = {v: k for k, v in CORNER_LABELS.items()}

# زیرپوشه‌ی خودکار (اختیاری، برای هر دسته جدا؛ پیش‌فرض: بدون زیرپوشه)
SUB_LABELS = {
    "none": "بدون زیرپوشه",
    "ym": "سال-ماه  (مثلاً 2026-10)",
    "y": "سال  (مثلاً 2026)",
    "ymd": "سال-ماه-روز  (مثلاً 2026-10-22)",
    "type": "بر اساس نوع فایل  (Images, Videos, ...)",
}
LABEL_TO_SUB = {v: k for k, v in SUB_LABELS.items()}

TYPE_FOLDERS = {
    "Images": "jpg jpeg png gif webp bmp tif tiff svg heic heif ico raw psd",
    "Videos": "mp4 mkv avi mov wmv flv webm m4v mpg mpeg",
    "Audio": "mp3 wav flac aac ogg m4a wma",
    "Documents": "pdf doc docx xls xlsx ppt pptx txt rtf odt ods csv md epub",
    "Archives": "zip rar 7z tar gz bz2 xz iso",
    "Programs": "exe msi bat cmd ps1 apk",
}
EXT_TO_TYPE = {}
for _folder, _exts in TYPE_FOLDERS.items():
    for _e in _exts.split():
        EXT_TO_TYPE[_e] = _folder


def subfolder_for(src, sub, now):
    """نام زیرپوشه‌ی خودکار برای یک مورد (خالی = بدون زیرپوشه)."""
    if not sub or sub == "none":
        return ""
    if sub == "y":
        return now.strftime("%Y")
    if sub == "ym":
        return now.strftime("%Y-%m")
    if sub == "ymd":
        return now.strftime("%Y-%m-%d")
    if sub == "type":
        if os.path.isdir(src):
            return ""  # فولدرها همان‌طور که هستند
        ext = os.path.splitext(src)[1].lower().lstrip(".")
        return EXT_TO_TYPE.get(ext, "Other")
    return ""


def sanitize_categories(lst):
    """ساختار دسته‌ها را (مثلاً از فایل ورودی) اعتبارسنجی و پاک‌سازی می‌کند."""
    out = []
    if not isinstance(lst, list):
        return out

    def leaf(d):
        if not isinstance(d, dict):
            return None
        name = str(d.get("name", "")).strip()
        path = str(d.get("path", "")).strip()
        if not name or not path:
            return None
        mode = d.get("mode", "ask")
        if mode not in MODE_LABELS:
            mode = "ask"
        r = {"name": name, "path": path, "mode": mode}
        if d.get("sub") in SUB_LABELS and d.get("sub") != "none":
            r["sub"] = d["sub"]
        if d.get("fav"):
            r["fav"] = True
        return r

    for d in lst:
        if not isinstance(d, dict):
            continue
        if d.get("children") is not None and not d.get("path"):
            name = str(d.get("name", "")).strip()
            if not name:
                continue
            kids = []
            for x in d.get("children") or []:
                k = leaf(x)
                if k:
                    kids.append(k)
            out.append({"name": name, "path": "", "children": kids})
        else:
            l = leaf(d)
            if l:
                out.append(l)
    return out


# تاریخچه‌ی عملیات (برای بازگردانی)
HISTORY_PATH = os.path.join(config_dir(), "history.json")
HISTORY_MAX = 30


def load_history():
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [b for b in data if isinstance(b, dict) and "id" in b][-HISTORY_MAX:]
    except Exception:
        pass
    return []


def save_history(hist):
    try:
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(hist[-HISTORY_MAX:], f, ensure_ascii=False)
    except Exception:
        pass

# ===========================================================================
# لایه‌ی Windows API (ctypes) — آیکن سینی سیستم، درگ‌ودراپ، کلید میانبر
# ===========================================================================

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# --- ثابت‌های پیام‌ها ---
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_DROPFILES = 0x0233
WM_HOTKEY = 0x0312
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
TRAY_CALLBACK = WM_USER + 1
WM_COPYDATA = 0x004A

# عنوان یکتای پنجره‌ی مخفی برای پیدا کردن نمونه‌ی در حال اجرا (single instance / IPC)
IPC_WINDOW_TITLE = "DragDropSorter::IPC::MainWindow::v1"

GWLP_WNDPROC = -4

# --- Notify Icon ---
NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
IDI_APPLICATION = 32512

# --- منوی راست‌کلیک ---
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

# --- کلید میانبر ---
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
HOTKEY_ID = 1

# --- هوک سطح پایین صفحه‌کلید (مستقل از زبان/چیدمان کیبورد) ---
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
MAPVK_VK_TO_VSC = 0

# کدهای مجازی مودیفایرها
VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12  # Alt
VK_LWIN = 0x5B
VK_RWIN = 0x5C

# --- بارگذاری آیکن ---
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
LR_SHARED = 0x8000

# --- دستورهای منوی سینی ---
ID_OPEN = 1001
ID_SETTINGS = 1002
ID_EXIT = 1003
ID_DOCK = 1004
ID_HISTORY = 1005
MF_CHECKED = 0x0008

LONG_PTR = ctypes.c_ssize_t
WNDPROCTYPE = ctypes.WINFUNCTYPE(
    LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _SetWindowLong = user32.SetWindowLongPtrW
else:
    _SetWindowLong = user32.SetWindowLongW
_SetWindowLong.restype = LONG_PTR
_SetWindowLong.argtypes = [wintypes.HWND, ctypes.c_int, WNDPROCTYPE]

CallWindowProc = user32.CallWindowProcW
CallWindowProc.restype = LONG_PTR
CallWindowProc.argtypes = [
    LONG_PTR, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]

user32.GetParent.restype = wintypes.HWND
user32.GetParent.argtypes = [wintypes.HWND]

# --- پیدا کردن و پیام فرستادن به نمونه‌ی در حال اجرا ---
user32.FindWindowW.restype = wintypes.HWND
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.SendMessageW.restype = LONG_PTR
user32.SendMessageW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
]


class COPYDATASTRUCT(ctypes.Structure):
    _fields_ = [
        ("dwData", ctypes.c_void_p),
        ("cbData", wintypes.DWORD),
        ("lpData", ctypes.c_void_p),
    ]

user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]

shell32.DragAcceptFiles.restype = None
shell32.DragAcceptFiles.argtypes = [wintypes.HWND, wintypes.BOOL]
shell32.DragQueryFileW.restype = wintypes.UINT
shell32.DragQueryFileW.argtypes = [
    wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT
]
shell32.DragFinish.restype = None
shell32.DragFinish.argtypes = [wintypes.HANDLE]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16),
        ("hBalloonIcon", wintypes.HICON),
    ]


shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)]


class SHFILEOPSTRUCTW(ctypes.Structure):
    _pack_ = 1 if ctypes.sizeof(ctypes.c_void_p) == 4 else 8
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", ctypes.c_void_p),
        ("pTo", ctypes.c_void_p),
        ("fFlags", ctypes.c_ushort),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", ctypes.c_void_p),
    ]


shell32.SHFileOperationW.restype = ctypes.c_int
shell32.SHFileOperationW.argtypes = [ctypes.POINTER(SHFILEOPSTRUCTW)]
FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400

# --- بارگذاری آیکن از فایل .ico ---
user32.LoadImageW.restype = wintypes.HANDLE
user32.LoadImageW.argtypes = [
    wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT,
    ctypes.c_int, ctypes.c_int, wintypes.UINT,
]

# --- هوک صفحه‌کلید ---
HOOKPROC = ctypes.WINFUNCTYPE(
    LONG_PTR, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(wintypes.ULONG)),
    ]


user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [
    ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
]
user32.CallNextHookEx.restype = LONG_PTR
user32.CallNextHookEx.argtypes = [
    wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.MapVirtualKeyW.restype = wintypes.UINT
user32.MapVirtualKeyW.argtypes = [wintypes.UINT, wintypes.UINT]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


def load_hicon():
    """آیکن برنامه را از فایل .ico می‌خواند؛ در صورت شکست آیکن پیش‌فرض ویندوز."""
    hicon = 0
    if os.path.exists(ICON_PATH):
        hicon = user32.LoadImageW(
            None, ICON_PATH, IMAGE_ICON, 0, 0,
            LR_LOADFROMFILE | LR_DEFAULTSIZE | LR_SHARED,
        )
    if not hicon:
        hicon = user32.LoadIconW(None, ctypes.c_void_p(IDI_APPLICATION))
    return hicon


def get_hwnd(widget):
    """HWND پنجره‌ی بالایی (top-level) یک ویجت Tk را برمی‌گرداند."""
    widget.update_idletasks()
    return user32.GetParent(widget.winfo_id())


def vk_from_key(key):
    """تبدیل یک حرف یا F1..F24 به کد مجازی کلید (VK)."""
    key = (key or "").strip().upper()
    if len(key) == 1 and ("A" <= key <= "Z" or "0" <= key <= "9"):
        return ord(key)
    if key.startswith("F") and key[1:].isdigit():
        n = int(key[1:])
        if 1 <= n <= 24:
            return 0x70 + (n - 1)
    return 0


# ---------------------------------------------------------------------------
# تک‌نمونه (single instance) + ارسال مسیرهای رهاشده روی آیکن به نمونه‌ی در حال اجرا
# ---------------------------------------------------------------------------


def find_running_instance():
    """HWND پنجره‌ی مخفی نمونه‌ی در حال اجرا را (اگر باشد) برمی‌گرداند."""
    try:
        hwnd = user32.FindWindowW(None, IPC_WINDOW_TITLE)
        return hwnd if hwnd else 0
    except Exception:
        return 0


def send_paths_to_instance(hwnd, paths):
    """مسیرهای رهاشده را با WM_COPYDATA به نمونه‌ی در حال اجرا می‌فرستد."""
    data = "\n".join(paths)
    buf = ctypes.create_unicode_buffer(data)  # UTF-16 با پایان‌بند null
    cbytes = (len(data) + 1) * ctypes.sizeof(ctypes.c_wchar)
    cds = COPYDATASTRUCT()
    cds.dwData = 1
    cds.cbData = cbytes
    cds.lpData = ctypes.cast(buf, ctypes.c_void_p)
    try:
        user32.SendMessageW(
            hwnd, WM_COPYDATA, 0, ctypes.cast(ctypes.byref(cds), ctypes.c_void_p).value
        )
        return True
    except Exception:
        return False


# ===========================================================================
# اجرای خودکار هنگام روشن شدن ویندوز (Registry Run)
# ===========================================================================

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    """دستور اجرای برنامه برای ثبت در Startup."""
    if getattr(sys, "frozen", False):
        return '"{}"'.format(sys.executable)
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return '"{}" "{}"'.format(exe, os.path.abspath(__file__))


def is_startup_enabled():
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            val, _ = winreg.QueryValueEx(k, APP_NAME)
            return bool(val)
    except OSError:
        return False


def set_startup(enable):
    if winreg is None:
        return
    if enable:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, startup_command())
    else:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
            ) as k:
                winreg.DeleteValue(k, APP_NAME)
        except OSError:
            pass

# ===========================================================================
# عملیات کپی / انتقال فایل و فولدر
# ===========================================================================


def unique_dest(dest_dir, name):
    """اگر نام در مقصد تکراری باشد، یک نام یکتا می‌سازد."""
    target = os.path.join(dest_dir, name)
    if not os.path.exists(target):
        return target
    base, ext = os.path.splitext(name)
    i = 1
    while True:
        cand = os.path.join(dest_dir, "{} ({}){}".format(base, i, ext))
        if not os.path.exists(cand):
            return cand
        i += 1


class Cancelled(Exception):
    """کاربر عملیات را متوقف کرد."""


class Progress:
    """وضعیت پیشرفت (نخ کارگر می‌نویسد، نخ UI فقط می‌خواند)."""

    def __init__(self, total_items):
        self.cancel = threading.Event()
        self.items_total = total_items
        self.items_done = 0
        self.cur_size = 0
        self.cur_done = 0
        self.current = ""

    def fraction(self):
        if not self.items_total:
            return 0.0
        part = (self.cur_done / float(self.cur_size)) if self.cur_size else 0.0
        return min(1.0, (self.items_done + min(part, 1.0)) / float(self.items_total))

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()


def path_size(p):
    try:
        if os.path.isfile(p):
            return os.path.getsize(p)
    except OSError:
        return 0
    total = 0
    for root, _dirs, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def copy_file(src, dst, prog):
    """کپی تکه‌تکه با گزارش پیشرفت و امکان توقف؛ در صورت خطا فایل ناقص پاک می‌شود."""
    try:
        with open(src, "rb") as fi, open(dst, "wb") as fo:
            while True:
                prog.check()
                buf = fi.read(1024 * 1024)
                if not buf:
                    break
                fo.write(buf)
                prog.cur_done += len(buf)
    except Exception:
        try:
            if os.path.exists(dst):
                os.remove(dst)
        except OSError:
            pass
        raise
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def copy_tree(src, dst, prog):
    os.makedirs(dst)
    with os.scandir(src) as it:
        entries = list(it)
    for entry in entries:
        prog.check()
        s = entry.path
        d = os.path.join(dst, entry.name)
        if entry.is_dir(follow_symlinks=False):
            copy_tree(s, d, prog)
        else:
            copy_file(s, d, prog)
    try:
        shutil.copystat(src, dst)
    except OSError:
        pass


def _norm(p):
    return os.path.normcase(os.path.abspath(p))


def transfer_one(src, dest_dir, mode, prog):
    """یک مورد را کپی/منتقل می‌کند و مسیر نهایی را برمی‌گرداند."""
    src = os.path.normpath(src)
    if not os.path.lexists(src):
        raise OSError("مبدأ پیدا نشد (حذف یا جابه‌جا شده)")
    name = os.path.basename(src.rstrip("\\/")) or src
    if os.path.isdir(src):
        a, b = _norm(src), _norm(dest_dir)
        if b == a or b.startswith(a + os.sep):
            raise OSError("مقصد داخل خودِ همین فولدر است")
    if mode == "move" and _norm(os.path.dirname(src)) == _norm(dest_dir):
        raise OSError("از قبل در همین مسیر است")

    target = unique_dest(dest_dir, name)
    prog.current = name
    prog.cur_done = 0
    prog.cur_size = 0

    if mode == "move":
        try:
            os.rename(src, target)  # روی همان درایو: آنی
            return target
        except OSError as exc:
            cross = (getattr(exc, "winerror", None) == 17) or (exc.errno == errno.EXDEV)
            if not cross:
                raise
            # درایو دیگر → کپی + حذف مبدأ

    prog.cur_size = path_size(src)
    try:
        if os.path.isdir(src):
            copy_tree(src, target, prog)
        else:
            copy_file(src, target, prog)
    except Exception:
        # پاکسازی کپی ناقص (مبدأ دست‌نخورده می‌ماند)
        if os.path.isdir(target):
            shutil.rmtree(target, ignore_errors=True)
        elif os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
        raise

    if mode == "move":
        try:
            if os.path.isdir(src) and not os.path.islink(src):
                shutil.rmtree(src)
            else:
                os.remove(src)
        except Exception as exc:  # noqa: BLE001
            raise OSError("کپی شد ولی حذف مبدأ ممکن نشد: {}".format(exc))
    return target


def transfer_items(paths, dest_dir, mode, sub, prog):
    """هر مسیر را کپی/منتقل می‌کند.
    خروجی: (تعداد موفق، لیست خطا، رکوردهای بازگردانی، آیا متوقف شد)"""
    ok = 0
    errors = []
    records = []
    cancelled = False
    now = datetime.datetime.now()
    for src in paths:
        try:
            prog.check()
            d = dest_dir
            sf = subfolder_for(src, sub, now)
            if sf:
                d = os.path.join(dest_dir, sf)
            os.makedirs(d, exist_ok=True)
            target = transfer_one(src, d, mode, prog)
            records.append({"src": os.path.normpath(src), "dst": target})
            ok += 1
        except Cancelled:
            cancelled = True
            break
        except Exception as exc:  # noqa: BLE001
            errors.append("{}: {}".format(os.path.basename(str(src).rstrip("\\/")) or src, exc))
        finally:
            prog.items_done += 1
            prog.cur_done = 0
            prog.cur_size = 0
    return ok, errors, records, cancelled


def recycle(path):
    """فایل/فولدر را به سطل زباله می‌فرستد (قابل بازیابی)."""
    path = os.path.abspath(path)
    buf = ctypes.create_unicode_buffer(path + "\0")  # دو null پایانی
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buf, ctypes.c_void_p).value
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    res = shell32.SHFileOperationW(ctypes.byref(op))
    if res != 0 or op.fAnyOperationsAborted:
        raise OSError("انتقال به سطل زباله ناموفق بود (کد {})".format(res))


def undo_items(items, mode):
    """بازگردانی: انتقال → برگشت به جای اول؛ کپی → سطل زباله.
    خروجی: (موفق، خطاها، مواردی که هنوز مانده‌اند)"""
    ok = 0
    errors = []
    remaining = []
    for it in items:
        src, dst = it.get("src", ""), it.get("dst", "")
        label = os.path.basename(dst.rstrip("\\/")) or dst
        try:
            if not os.path.lexists(dst):
                errors.append("{}: در مقصد پیدا نشد (جابه‌جا یا حذف شده)".format(label))
                continue  # دیگر قابل بازگردانی نیست
            if mode == "move":
                back = os.path.dirname(src)
                os.makedirs(back, exist_ok=True)
                target = unique_dest(back, os.path.basename(src))
                shutil.move(dst, target)
            else:
                recycle(dst)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            errors.append("{}: {}".format(label, exc))
            remaining.append(it)
    return ok, errors, remaining


user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.SystemParametersInfoW.restype = wintypes.BOOL
user32.SystemParametersInfoW.argtypes = [
    wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT
]
SPI_GETWORKAREA = 0x0030


def enable_dpi_awareness():
    """قبل از ساخت اولین پنجره صدا زده شود تا متن‌ها در مانیتور High-DPI تار نشوند."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


# ===========================================================================
# کلاس اصلی برنامه
# ===========================================================================


class DragDropSorter:
    def __init__(self):
        self.cfg = load_config()
        if self.cfg.get("dpi_aware", True):
            enable_dpi_awareness()
        self._history = load_history()
        self._history_win = None
        self._progress = None
        self._xfer_verb = ""
        self._last_status = ""
        self._last_batch_id = None
        self._no_search_trace = False
        self._positioning = False
        self._last_box_size = None
        self._pos_job = None
        self._close_job = None
        self.pending = []  # مسیرهای درگ‌شده که منتظر پردازش‌اند
        self.box_visible = False
        self._tray_added = False
        # هوک کیبورد (کلید میانبر مستقل از زبان)
        self._hook = None
        self._hook_proc = None
        self._hk_down = False
        self.hotkey_scan = 0
        self.hotkey_mods = {}
        # پرچم ایمن برای کلید میانبر (هوک فقط این را ست می‌کند، UI جدا پردازش می‌کند)
        self._hotkey_pending = False
        # مسیرهای درگ‌شده که منتظر پردازش روی نخ UI هستند (پرچم ایمن مثل hotkey)
        self._drop_pending = None
        # درخواست نمایش باکس از نمونه‌ی دیگر (بدون فایل)
        self._show_pending = False
        # صف پردازش پس‌زمینه برای کپی/انتقال
        self._xfer_queue = queue.Queue()
        self._xfer_running = False
        # وضعیت درخت مقصدها (آکاردئونی: فقط یک گروه باز می‌ماند)
        self.tree_groups = {}          # iid گروه -> نام گروه
        self._open_group_iid = None    # گروهی که الان باز است
        self._last_open_group = None   # نام آخرین گروهِ بازشده (بین دراپ‌ها حفظ می‌شود)

        # --- ریشه‌ی مخفی (فقط برای آیکن سینی و کلید میانبر) ---
        self.root = tk.Tk()
        # عنوان یکتا تا نمونه‌ی جدید بتواند این پنجره را پیدا کند
        self.root.title(IPC_WINDOW_TITLE)
        self.root.withdraw()
        try:
            if os.path.exists(ICON_PATH):
                self.root.iconbitmap(ICON_PATH)
        except Exception:
            pass
        # جلوگیری از بسته شدن ناگهانی
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        # مقیاس رابط (High-DPI) و استایل درخت‌ها
        try:
            self.ui_scale = min(3.0, max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0))
        except Exception:
            self.ui_scale = 1.0
        self._setup_styles()

        self._build_box()
        self._build_dock()

        # --- دریافت HWNDها و جایگزینی WndProc ---
        self.root_hwnd = get_hwnd(self.root)
        self.box_hwnd = get_hwnd(self.box)
        self.dock_hwnd = get_hwnd(self.dock)

        # نگه‌داشتن ارجاع به تابع‌ها تا garbage collect نشوند
        self._root_proc = WNDPROCTYPE(self._root_wndproc)
        self._box_proc = WNDPROCTYPE(self._box_wndproc)
        self._root_old = _SetWindowLong(self.root_hwnd, GWLP_WNDPROC, self._root_proc)
        self._box_old = _SetWindowLong(self.box_hwnd, GWLP_WNDPROC, self._box_proc)
        self._dock_proc = WNDPROCTYPE(self._dock_wndproc)
        self._dock_old = _SetWindowLong(self.dock_hwnd, GWLP_WNDPROC, self._dock_proc)

        # پذیرش درگ‌ودراپ روی باکس
        shell32.DragAcceptFiles(self.box_hwnd, True)
        shell32.DragAcceptFiles(self.dock_hwnd, True)

        self._add_tray_icon()
        self._install_hook()
        self.register_hotkey()

    # ------------------------------------------------------------------
    # استایل (ارتفاع ردیف درخت متناسب با DPI + رفع باگ رنگ تگ‌ها در Tk 8.6.9)
    # ------------------------------------------------------------------
    def _setup_styles(self):
        try:
            style = ttk.Style(self.root)
            style.configure("Treeview", rowheight=int(22 * self.ui_scale))

            def fixed_map(option):
                return [e for e in style.map("Treeview", query_opt=option)
                        if e[:2] != ("!disabled", "!selected")]

            style.map("Treeview", foreground=fixed_map("foreground"),
                      background=fixed_map("background"))
        except Exception:
            pass

    # ------------------------------------------------------------------
    # آیکن شناور کوچک (Dock): فایل را رویش بکشید تا باکس باز شود
    # ------------------------------------------------------------------
    def _build_dock(self):
        size = int(52 * self.ui_scale)
        self._dock_size = size
        d = tk.Toplevel(self.root)
        d.overrideredirect(True)
        d.attributes("-topmost", True)
        try:
            d.attributes("-alpha", 0.9)
        except Exception:
            pass
        d.configure(bg="#1565c0")
        lbl = tk.Label(
            d, text="📥", font=("Segoe UI Emoji", 15),
            bg="#e3f2fd", fg="#0d47a1", cursor="hand2",
        )
        lbl.pack(fill="both", expand=True, padx=2, pady=2)
        self.dock = d
        self.dock_label = lbl

        dc = self.cfg.get("dock", {})
        sw = d.winfo_screenwidth()
        sh = d.winfo_screenheight()
        x, y = dc.get("x"), dc.get("y")
        try:
            x = int(x)
            y = int(y)
        except (TypeError, ValueError):
            x, y = sw - size - 6, sh // 2
        x = min(max(0, x), max(0, sw - size))
        y = min(max(0, y), max(0, sh - size))
        d.geometry("{0}x{0}+{1}+{2}".format(size, x, y))

        self._dock_start = None
        self._dock_moved = False
        lbl.bind("<ButtonPress-1>", self._dock_press)
        lbl.bind("<B1-Motion>", self._dock_motion)
        lbl.bind("<ButtonRelease-1>", self._dock_release)
        lbl.bind("<ButtonRelease-3>", lambda e: self._show_tray_menu())
        lbl.bind("<Enter>", lambda e: lbl.config(bg="#bbdefb"))
        lbl.bind("<Leave>", lambda e: lbl.config(bg="#e3f2fd"))
        if not dc.get("visible", True):
            d.withdraw()

    def _dock_press(self, e):
        self._dock_start = (e.x_root, e.y_root, self.dock.winfo_x(), self.dock.winfo_y())
        self._dock_moved = False

    def _dock_motion(self, e):
        if not self._dock_start:
            return
        sx, sy, wx, wy = self._dock_start
        dx, dy = e.x_root - sx, e.y_root - sy
        if not self._dock_moved and abs(dx) + abs(dy) < 6:
            return
        self._dock_moved = True
        sw = self.dock.winfo_screenwidth()
        sh = self.dock.winfo_screenheight()
        nx = min(max(0, wx + dx), max(0, sw - self._dock_size))
        ny = min(max(0, wy + dy), max(0, sh - self._dock_size))
        self.dock.geometry("+{}+{}".format(nx, ny))

    def _dock_release(self, e):
        moved = self._dock_moved
        self._dock_start = None
        self._dock_moved = False
        if moved:
            dc = self.cfg.setdefault("dock", {})
            dc["x"] = self.dock.winfo_x()
            dc["y"] = self.dock.winfo_y()
            try:
                save_config(self.cfg)
            except Exception:
                pass
        else:
            self.toggle_box()

    def dock_is_visible(self):
        return bool(self.cfg.get("dock", {}).get("visible", True))

    def toggle_dock(self):
        dc = self.cfg.setdefault("dock", {})
        vis = not dc.get("visible", True)
        dc["visible"] = vis
        try:
            save_config(self.cfg)
        except Exception:
            pass
        if vis:
            self.dock.deiconify()
            self.dock.attributes("-topmost", True)
            self.dock.lift()
        else:
            self.dock.withdraw()

    # ------------------------------------------------------------------
    # ساخت باکس درگ‌ودراپ
    # ------------------------------------------------------------------
    def _build_box(self):
        self.box = tk.Toplevel(self.root)
        self.box.title(APP_NAME)
        self.box.resizable(False, False)
        self.box.attributes("-topmost", True)
        self.box.configure(bg="#f4f4f4")
        try:
            if os.path.exists(ICON_PATH):
                self.box.iconbitmap(ICON_PATH)
        except Exception:
            pass
        self.box.protocol("WM_DELETE_WINDOW", self.hide_box)
        self.box.bind("<Configure>", self._on_box_configure)
        self.box.withdraw()

        pad = {"padx": 10, "pady": 6}

        # ناحیه‌ی درگ‌ودراپ
        self.drop_zone = tk.Label(
            self.box,
            text="📁  فایل یا فولدر را اینجا بکشید و رها کنید",
            bg="#ffffff",
            fg="#333333",
            relief="groove",
            bd=2,
            width=40,
            height=4,
            font=("Tahoma", 10),
        )
        self.drop_zone.pack(fill="x", **pad)

        # دکمه‌ی بازگردانی سریع (فقط بعد از یک انتقال/کپی نمایش داده می‌شود)
        self.undo_btn = tk.Button(
            self.box, text="↩ بازگردانی", bg="#fff3e0",
            font=("Tahoma", 9), command=self._undo_last,
        )

        # ناحیه‌ی انتخاب (بعد از دراپ نمایان می‌شود)
        self.choose_frame = tk.Frame(self.box, bg="#f4f4f4")

        self.info_label = tk.Label(
            self.choose_frame, text="", bg="#f4f4f4", font=("Tahoma", 9, "bold")
        )
        self.info_label.pack(anchor="e", padx=10)

        tk.Label(
            self.choose_frame, text="مقصد را انتخاب کنید:",
            bg="#f4f4f4", font=("Tahoma", 9)
        ).pack(anchor="e", padx=10)

        # کادر جستجو در مقصدها
        srow = tk.Frame(self.choose_frame, bg="#f4f4f4")
        srow.pack(fill="x", padx=10, pady=(2, 4))
        tk.Label(srow, text="🔍", bg="#f4f4f4").pack(side="right")
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(srow, textvariable=self.search_var, justify="right")
        self.search_entry.pack(side="right", fill="x", expand=True, padx=(0, 4))
        self.search_var.trace_add("write", self._on_search_change)
        self.search_entry.bind("<Return>", self._on_search_enter)
        self.search_entry.bind("<Down>", self._on_search_enter)

        # تفکیک خودکار بر اساس نوع فایل (Images/Videos/...) داخل مقصد انتخاب‌شده
        self.auto_type_var = tk.BooleanVar(value=False)
        self.auto_type_chk = tk.Checkbutton(
            self.choose_frame,
            text="📦 تفکیک خودکار بر اساس نوع فایل (Images, Videos, Documents, ...)",
            variable=self.auto_type_var, bg="#f4f4f4", fg="#0d47a1",
            font=("Tahoma", 8), anchor="e", justify="right",
            activebackground="#f4f4f4", selectcolor="#ffffff",
        )
        self.auto_type_chk.pack(anchor="e", padx=10, pady=(0, 2))

        tree_wrap = tk.Frame(self.choose_frame, bg="#f4f4f4")
        tree_wrap.pack(fill="x", padx=10)
        tscroll = tk.Scrollbar(tree_wrap)
        tscroll.pack(side="left", fill="y")
        self.cat_tree = ttk.Treeview(
            tree_wrap, show="tree", height=7, selectmode="browse",
            yscrollcommand=tscroll.set,
        )
        self.cat_tree.pack(side="right", fill="x", expand=True)
        tscroll.config(command=self.cat_tree.yview)
        # iid درخت -> دیکشنری برگ (مقصد واقعی). گروه‌ها در این مپ نیستند.
        self.tree_leaf = {}
        self.cat_tree.bind("<<TreeviewSelect>>", lambda e: self._update_action_buttons())
        self.cat_tree.bind("<Double-Button-1>", self._on_tree_double)
        # آکاردئونی: با باز شدن یک گروه، بقیه بسته می‌شوند
        self.cat_tree.bind("<Return>", self._on_tree_double)
        self.cat_tree.bind("<<TreeviewOpen>>", self._on_tree_open_close)
        self.cat_tree.bind("<<TreeviewClose>>", self._on_tree_open_close)

        # دکمه‌های عملیات: کپی / انتقال / کنسل
        btns = tk.Frame(self.choose_frame, bg="#f4f4f4")
        btns.pack(fill="x", padx=10, pady=8)
        self.copy_btn = tk.Button(
            btns, text="📋 کپی", width=10,
            command=lambda: self._do_transfer("copy"),
        )
        self.copy_btn.pack(side="right", padx=4)
        self.move_btn = tk.Button(
            btns, text="✂ انتقال", width=10,
            command=lambda: self._do_transfer("move"),
        )
        self.move_btn.pack(side="right", padx=4)
        self.cancel_btn = tk.Button(
            btns, text="✕ کنسل", width=8, command=self._on_cancel_click
        )
        self.cancel_btn.pack(side="left", padx=4)

        # نوار وضعیت (هنگام انتقال طولانی)
        self.status_label = tk.Label(
            self.choose_frame, text="", bg="#f4f4f4", fg="#1565c0",
            font=("Tahoma", 9, "bold"),
        )
        self.progress = ttk.Progressbar(self.choose_frame, mode="indeterminate")

    # ------------------------------------------------------------------
    # نمایش / پنهان کردن باکس
    # ------------------------------------------------------------------
    def _work_area(self):
        """ناحیه‌ی قابل‌استفاده‌ی صفحه (بدون تسک‌بار)."""
        try:
            r = wintypes.RECT()
            if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
                return r.left, r.top, r.right, r.bottom
        except Exception:
            pass
        return 0, 0, self.box.winfo_screenwidth(), self.box.winfo_screenheight()

    def _outer_size(self):
        """اندازه‌ی واقعی پنجره‌ی باکس (با نوار عنوان و حاشیه)."""
        try:
            r = wintypes.RECT()
            if user32.GetWindowRect(self.box_hwnd, ctypes.byref(r)):
                w, h = r.right - r.left, r.bottom - r.top
                if w > 50 and h > 50:
                    return w, h
        except Exception:
            pass
        sc = getattr(self, "ui_scale", 1.0)
        return (self.box.winfo_reqwidth() + int(16 * sc),
                self.box.winfo_reqheight() + int(40 * sc))

    def _position_box(self):
        """باکس را همیشه در گوشه‌ی انتخاب‌شده قرار می‌دهد و هرگز از صفحه بیرون نمی‌زند."""
        if self._positioning:
            return
        self._positioning = True
        try:
            sc = getattr(self, "ui_scale", 1.0)
            margin = int(10 * sc)
            l, t, r, b = self._work_area()
            # اگر لیست مقصدها بلند است و جا ندارد، ردیف‌های درخت را کم کن
            rows = 7
            self.cat_tree.config(height=rows)
            self.box.update_idletasks()
            w, h = self._outer_size()
            avail_h = (b - t) - 2 * margin
            if h > avail_h and self.choose_frame.winfo_manager():
                rh = int(22 * sc)
                need = -(-(h - avail_h) // rh)
                rows = max(3, 7 - need)
                self.cat_tree.config(height=rows)
                self.box.update_idletasks()
                w, h = self._outer_size()
            corner = self.cfg.get("box_corner", "br")
            if corner == "bl":
                x, y = l + margin, b - h - margin
            elif corner == "tr":
                x, y = r - w - margin, t + margin
            elif corner == "tl":
                x, y = l + margin, t + margin
            elif corner == "center":
                x = l + ((r - l) - w) // 2
                y = t + ((b - t) - h) // 2
            else:  # br (پیش‌فرض: پایین-راست)
                x, y = r - w - margin, b - h - margin
            x = max(l, min(x, r - w - margin))
            y = max(t, min(y, b - h - margin))
            self._last_box_size = (w, h)
            self.box.geometry("+{}+{}".format(x, y))
        finally:
            self._positioning = False

    def _on_box_configure(self, event):
        """هر وقت اندازه‌ی باکس عوض شد (بزرگ/کوچک شدن)، دوباره سر جایش برود."""
        if event.widget is not self.box or self._positioning:
            return
        try:
            if self.box.state() == "withdrawn":
                return
        except Exception:
            return
        if self._pos_job is not None:
            try:
                self.box.after_cancel(self._pos_job)
            except Exception:
                pass

        def later():
            self._pos_job = None
            try:
                w, h = self._outer_size()
                if self._last_box_size != (w, h):  # فقط هنگام تغییر اندازه
                    self._position_box()
            except Exception:
                pass

        self._pos_job = self.box.after(30, later)

    def _cancel_close_job(self):
        """زمان‌بندی بسته‌شدن خودکار باکس را (اگر در جریان باشد) لغو می‌کند."""
        if self._close_job is not None:
            try:
                self.box.after_cancel(self._close_job)
            except Exception:
                pass
            self._close_job = None

    def _auto_close_box(self):
        self._close_job = None
        if not self._xfer_running and not self.pending:
            self.hide_box()

    def show_box(self):
        self._cancel_close_job()
        self._cancel_choose()
        self.box.deiconify()
        self.box.lift()
        self.box.attributes("-topmost", True)
        self._position_box()
        self.box_visible = True

    def hide_box(self):
        self._cancel_close_job()
        self.box.withdraw()
        self.box_visible = False

    def toggle_box(self):
        if self.box_visible and self.box.state() != "withdrawn":
            self.hide_box()
        else:
            self.show_box()

    def _cancel_choose(self):
        self.pending = []
        self.choose_frame.pack_forget()
        for iid in self.cat_tree.get_children(""):
            self.cat_tree.delete(iid)
        self.tree_leaf = {}
        self.tree_groups = {}
        self._open_group_iid = None
        self.undo_btn.pack_forget()
        self._clear_search()
        self.drop_zone.config(
            text="📁  فایل یا فولدر را اینجا بکشید و رها کنید",
            bg="#ffffff",
        )
        self._position_box()

    def _cancel_and_hide(self):
        self._cancel_choose()
        self.hide_box()

    # ------------------------------------------------------------------
    # پردازش دراپ
    # ------------------------------------------------------------------
    def _query_drop(self, hdrop):
        paths = []
        count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
        for i in range(count):
            length = shell32.DragQueryFileW(hdrop, i, None, 0)
            buf = ctypes.create_unicode_buffer(length + 1)
            shell32.DragQueryFileW(hdrop, i, buf, length + 1)
            if buf.value:
                paths.append(buf.value)
        shell32.DragFinish(hdrop)
        return paths

    def _all_leaves(self):
        out = []
        for c in self.cfg.get("categories", []):
            if c.get("children") is not None and not c.get("path"):
                out.extend(c.get("children") or [])
            else:
                out.append(c)
        return out

    def _clear_search(self):
        self._no_search_trace = True
        try:
            self.search_var.set("")
        finally:
            self._no_search_trace = False

    def _on_search_change(self, *args):
        if self._no_search_trace:
            return
        if self.pending:
            self._populate_tree()

    def _visible_leaf_iids(self):
        out = []

        def walk(parent):
            for iid in self.cat_tree.get_children(parent):
                if iid in self.tree_leaf:
                    out.append(iid)
                else:
                    walk(iid)

        walk("")
        return out

    def _on_search_enter(self, event=None):
        leaves = self._visible_leaf_iids()
        if leaves:
            self.cat_tree.selection_set(leaves[0])
            self.cat_tree.focus(leaves[0])
            self.cat_tree.see(leaves[0])
            self.cat_tree.focus_set()
        return "break"

    def _populate_tree(self):
        """درخت دسته‌ها را از روی پیکربندی می‌سازد.

        - فقط «آخرین مقصد استفاده‌شده» در صدر لیست می‌آید (نه یک بخش همیشه‌باز).
        - همه‌ی گروه‌ها بسته‌اند، مگر آخرین گروهی که کاربر باز کرده بود.
        - اگر در کادر جستجو چیزی نوشته شود، فقط موارد مطابق نمایش داده می‌شوند.
        """
        for iid in self.cat_tree.get_children(""):
            self.cat_tree.delete(iid)
        self.tree_leaf = {}
        self.tree_groups = {}
        self._open_group_iid = None
        want_open = self._last_open_group
        q = self.search_var.get().strip().casefold()
        filtering = bool(q)

        def add_leaf(parent_iid, item):
            label = "📂 {}   — [{}]".format(
                item.get("name", "?"), MODE_LABELS.get(item.get("mode", "ask"), "?")
            )
            iid = self.cat_tree.insert(parent_iid, "end", text=label)
            self.tree_leaf[iid] = item

        def match(item):
            return q in str(item.get("name", "")).casefold()

        # --- فقط آخرین مقصد استفاده‌شده در صدر (بدون جستجو) ---
        if not filtering:
            recent = self.cfg.get("recent", [])
            if recent:
                r0 = recent[0]
                for lf in self._all_leaves():
                    if (lf.get("name") == r0.get("name")
                            and lf.get("path") == r0.get("path")):
                        rid = self.cat_tree.insert(
                            "", "end",
                            text="🕘 {}   — [{}]".format(
                                lf.get("name", "?"),
                                MODE_LABELS.get(lf.get("mode", "ask"), "?"),
                            ),
                        )
                        self.tree_leaf[rid] = lf
                        break

        for c in self.cfg.get("categories", []):
            if c.get("children") is not None and not c.get("path"):
                children = c.get("children") or []
                if not children:
                    continue  # گروه خالی در باکس نمایش داده نمی‌شود
                if filtering:
                    group_hit = q in str(c.get("name", "")).casefold()
                    kids = children if group_hit else [k for k in children if match(k)]
                    if not kids:
                        continue
                    gid = self.cat_tree.insert(
                        "", "end", text="📁 {}".format(c.get("name", "?")), open=True
                    )
                    for ch in kids:
                        add_leaf(gid, ch)
                    continue
                is_open = (
                    want_open is not None
                    and c.get("name") == want_open
                    and self._open_group_iid is None
                )
                gid = self.cat_tree.insert(
                    "", "end", text="📁 {}".format(c.get("name", "?")), open=is_open
                )
                self.tree_groups[gid] = c.get("name")
                if is_open:
                    self._open_group_iid = gid
                for ch in children:
                    add_leaf(gid, ch)
            else:  # دسته‌ی ساده (سطح بالا)
                if not filtering or match(c):
                    add_leaf("", c)

        self._update_action_buttons()

    def _on_tree_open_close(self, event=None):
        # رویداد Open قبل از باز شدن واقعی گروه می‌آید؛ پس بعد از idle بررسی می‌کنیم.
        try:
            self.cat_tree.after_idle(self._sync_open_groups)
        except Exception:
            pass

    def _sync_open_groups(self):
        if self.search_var.get().strip():
            return  # هنگام جستجو همه‌ی گروه‌های مطابق باز نمایش داده می‌شوند
        try:
            opened = [g for g in self.tree_groups if self.cat_tree.item(g, "open")]
            keep = None
            if opened:
                fresh = [g for g in opened if g != self._open_group_iid]
                keep = fresh[-1] if fresh else opened[0]
                for g in opened:
                    if g != keep:
                        self.cat_tree.item(g, open=False)
            self._open_group_iid = keep
            self._last_open_group = self.tree_groups.get(keep) if keep else None
            # اگر مورد انتخاب‌شده داخل گروهِ بسته‌شده بود، انتخاب را بردار
            sel = self.cat_tree.selection()
            if sel:
                parent = self.cat_tree.parent(sel[0])
                if parent in self.tree_groups and parent != keep:
                    self.cat_tree.selection_remove(sel[0])
            self._update_action_buttons()
            self._position_box()
        except Exception:
            pass

    def _selected_leaf(self):
        sel = self.cat_tree.selection()
        if not sel:
            return None
        return self.tree_leaf.get(sel[0])

    def _update_action_buttons(self):
        leaf = self._selected_leaf()
        if not leaf:
            # گروه یا چیزی انتخاب نشده
            self.copy_btn.config(state="disabled")
            self.move_btn.config(state="disabled")
            return
        mode = leaf.get("mode", "ask")
        # حالت ثابت دسته دکمه‌ی نامتناسب را غیرفعال می‌کند
        self.copy_btn.config(state=("normal" if mode in ("ask", "copy") else "disabled"))
        self.move_btn.config(state=("normal" if mode in ("ask", "move") else "disabled"))

    def _on_tree_double(self, event):
        leaf = self._selected_leaf()
        if not leaf:
            return
        mode = leaf.get("mode", "ask")
        if mode == "move":
            self._do_transfer("move")
        else:
            self._do_transfer("copy")

    def on_drop(self, paths):
        if not paths:
            return
        self._cancel_close_job()
        # مهم: اول باکس را نشان بده (show_box وضعیت را ریست می‌کند)، بعد فایل‌ها را ثبت کن
        if not self.box_visible or self.box.state() == "withdrawn":
            self.show_box()
        self.pending = paths
        self.undo_btn.pack_forget()
        self._clear_search()
        self.drop_zone.config(
            text="✅  {} مورد انتخاب شد".format(len(paths)), bg="#eaffea"
        )
        self.info_label.config(text="{} مورد آماده‌ی انتقال/کپی".format(len(paths)))
        self._populate_tree()
        self.choose_frame.pack(fill="x", pady=(0, 6))
        # همیشه هنگام دراپ تازه، باکس سر جای درستش بازیابی می‌شود
        self._position_box()
        self.box.lift()
        try:
            self.box.focus_force()
            self.search_entry.focus_set()
        except Exception:
            pass

    def _do_transfer(self, mode):
        if self._xfer_running:
            return  # جلوگیری از اجرای دوباره
        leaf = self._selected_leaf()
        if not leaf:
            messagebox.showwarning(APP_NAME, "لطفاً یک مقصد (دسته‌ی دارای مسیر) را انتخاب کنید.", parent=self.box)
            return
        if not self.pending:
            messagebox.showwarning(APP_NAME, "هیچ فایلی برای انتقال وجود ندارد.", parent=self.box)
            return
        # احترام به حالت ثابت دسته (اگر فقط کپی یا فقط انتقال باشد)
        leaf_mode = leaf.get("mode", "ask")
        if leaf_mode in ("copy", "move"):
            mode = leaf_mode
        dest = leaf.get("path", "")
        if not dest:
            messagebox.showerror(APP_NAME, "مسیر این دسته تعیین نشده است.", parent=self.box)
            return

        verb = "منتقل" if mode == "move" else "کپی"
        paths = list(self.pending)
        sub = leaf.get("sub", "none")
        # اگر تیک «تفکیک خودکار بر اساس نوع» زده شده باشد، آن را اولویت می‌دهیم
        try:
            if self.auto_type_var.get():
                sub = "type"
        except Exception:
            pass
        leaf_name = leaf.get("name", "")
        prog = Progress(len(paths))
        self._progress = prog
        self._xfer_verb = verb
        self._last_status = ""

        # --- اجرای پس‌زمینه تا رابط کاربری فریز نشود ---
        self._xfer_running = True
        self._cancel_close_job()
        self.copy_btn.config(state="disabled")
        self.move_btn.config(state="disabled")
        self.cancel_btn.config(state="normal", text="⏹ توقف")
        self.status_label.config(text="در حال آماده‌سازی...")
        self.status_label.pack(anchor="e", padx=10)
        self.progress.config(mode="determinate", maximum=1000, value=0)
        self.progress.pack(fill="x", padx=10, pady=(0, 6))
        self._position_box()

        def worker():
            try:
                res = transfer_items(paths, dest, mode, sub, prog)
            except Exception as exc:  # noqa: BLE001
                res = (0, [str(exc)], [], False)
            self._xfer_queue.put(("xfer",) + res + (verb, mode, dest, leaf_name))

        threading.Thread(target=worker, daemon=True).start()

    def _on_cancel_click(self):
        if self._xfer_running:
            if self._progress is not None:
                self._progress.cancel.set()
                self.cancel_btn.config(state="disabled", text="در حال توقف...")
            return
        self._cancel_and_hide()

    def _on_transfer_done(self, ok, errors, records, cancelled, verb, mode, dest, leaf_name):
        self._xfer_running = False
        self._progress = None
        try:
            self.progress.pack_forget()
            self.status_label.pack_forget()
            self.cancel_btn.config(state="normal", text="✕ کنسل")
        except Exception:
            pass
        batch_id = None
        if records:
            batch_id = self._add_history(mode, dest, leaf_name, records)
            self._touch_recent(leaf_name, dest)
        self.pending = []
        self.choose_frame.pack_forget()
        head = "⏹ متوقف شد — {} مورد {} شد" if cancelled else "✅  {} مورد {} شد"
        self.drop_zone.config(
            text=head.format(ok, verb) + "\nفایل‌های بعدی را بکشید و رها کنید",
            bg="#eaffea" if not cancelled else "#fff8e1",
        )
        if batch_id is not None:
            self._last_batch_id = batch_id
            self.undo_btn.config(text="↩ بازگردانی ({} مورد)".format(len(records)), state="normal")
            self.undo_btn.pack(fill="x", padx=10, pady=(0, 6), after=self.drop_zone)
        else:
            self.undo_btn.pack_forget()
        self._position_box()
        self._refresh_history_win()
        if errors:
            # هنگام خطا باکس باز می‌ماند تا کاربر پیام را ببیند
            msg = "{} مورد {} شد.\n\nخطاها:\n".format(ok, verb) + "\n".join(errors[:10])
            messagebox.showwarning(APP_NAME, msg, parent=self.box)
        elif self.cfg.get("close_after", True) and not cancelled:
            # بعد از انتقال موفق، باکس به‌طور خودکار بسته می‌شود (طبق تنظیمات)
            self._cancel_close_job()
            self._close_job = self.box.after(1500, self._auto_close_box)

    # ---- پرکاربردها (اخیراً استفاده‌شده) ----
    def _touch_recent(self, name, path):
        rec = [r for r in self.cfg.get("recent", [])
               if not (r.get("name") == name and r.get("path") == path)]
        rec.insert(0, {"name": name, "path": path})
        self.cfg["recent"] = rec[:5]
        try:
            save_config(self.cfg)
        except Exception:
            pass

    # ---- تاریخچه و بازگردانی ----
    def _add_history(self, mode, dest, leaf_name, records):
        bid = int(time.time() * 1000)
        self._history.append({
            "id": bid,
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "mode": mode,
            "dest": dest,
            "name": leaf_name,
            "items": records,
            "undone": False,
        })
        self._history = self._history[-HISTORY_MAX:]
        save_history(self._history)
        return bid

    def _find_batch(self, batch_id):
        for b in self._history:
            if b.get("id") == batch_id:
                return b
        return None

    def _undo_last(self):
        self._undo_batch(self._last_batch_id, parent=self.box)

    def _undo_batch(self, batch_id, parent=None):
        if self._xfer_running:
            return
        parent = parent or self.box
        batch = self._find_batch(batch_id)
        if not batch or batch.get("undone") or not batch.get("items"):
            messagebox.showinfo(APP_NAME, "چیزی برای بازگردانی وجود ندارد.", parent=parent)
            return
        items = list(batch["items"])
        mode = batch.get("mode", "copy")
        if mode == "copy":
            if not messagebox.askyesno(
                APP_NAME,
                "{} مورد کپی‌شده به سطل زباله منتقل شود؟\n(قابل بازیابی از سطل زباله)".format(len(items)),
                parent=parent,
            ):
                return
        self._xfer_running = True
        self._cancel_close_job()
        try:
            self.undo_btn.config(state="disabled")
            if not self.pending:
                self.drop_zone.config(text="⏳  در حال بازگردانی...", bg="#fff8e1")
        except Exception:
            pass

        def worker():
            try:
                res = undo_items(items, mode)
            except Exception as exc:  # noqa: BLE001
                res = (0, [str(exc)], items)
            self._xfer_queue.put(("undo", batch_id) + res)

        threading.Thread(target=worker, daemon=True).start()

    def _on_undo_done(self, batch_id, ok, errors, remaining):
        self._xfer_running = False
        batch = self._find_batch(batch_id)
        if batch is not None:
            batch["items"] = remaining
            if not remaining:
                batch["undone"] = True
        save_history(self._history)
        self.undo_btn.pack_forget()
        if self.pending:
            self.drop_zone.config(
                text="✅  {} مورد انتخاب شد".format(len(self.pending)), bg="#eaffea"
            )
        else:
            self.drop_zone.config(
                text="↩  {} مورد بازگردانی شد\nفایل‌های بعدی را بکشید و رها کنید".format(ok),
                bg="#e3f2fd",
            )
        self._position_box()
        self._refresh_history_win()
        if errors:
            parent = self.box
            if self._history_win is not None:
                try:
                    if self._history_win.win.winfo_exists():
                        parent = self._history_win.win
                except Exception:
                    pass
            messagebox.showwarning(
                APP_NAME, "نتیجه‌ی بازگردانی:\n\n" + "\n".join(errors[:10]), parent=parent
            )

    def _refresh_history_win(self):
        if self._history_win is not None:
            try:
                if self._history_win.win.winfo_exists():
                    self._history_win.refresh()
            except Exception:
                pass

    def open_history(self):
        if self._history_win is not None:
            try:
                if self._history_win.win.winfo_exists():
                    self._history_win.win.lift()
                    return
            except Exception:
                pass
        self._history_win = HistoryWindow(self)


    # ------------------------------------------------------------------
    # WndProc‌ها
    # ------------------------------------------------------------------
    def _root_wndproc(self, hwnd, msg, wparam, lparam):
        if msg == TRAY_CALLBACK:
            event = lparam & 0xFFFF
            if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                self.toggle_box()
            elif event == WM_RBUTTONUP:
                self._show_tray_menu()
            return 0
        if msg == WM_HOTKEY and wparam == HOTKEY_ID:
            self.toggle_box()
            return 0
        if msg == WM_COPYDATA:
            # مسیرهای رهاشده روی آیکن/شورت‌کات از نمونه‌ی دیگر آمدند.
            # فقط داده را می‌خوانیم و طبق الگوی ایمن در پرچم می‌گذاریم.
            try:
                cds = COPYDATASTRUCT.from_address(lparam)
                if cds.lpData and cds.cbData:
                    count = cds.cbData // ctypes.sizeof(ctypes.c_wchar)
                    text = ctypes.wstring_at(cds.lpData, count).rstrip("\x00")
                    paths = [p for p in text.split("\n") if p]
                else:
                    paths = []
                if paths:
                    self._drop_pending = paths
                else:
                    self._show_pending = True
            except Exception:
                self._show_pending = True
            return 1
        return CallWindowProc(self._root_old, hwnd, msg, wparam, lparam)

    def _box_wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            # مهم: فقط مسیرها را استخراج می‌کنیم و در پرچم می‌گذاریم.
            # هیچ تابع Tkinter اینجا صدا نمی‌شود (جلوگیری از reentrancy).
            # پردازش واقعی در _poll (هر ۴۰ میلی‌ثانیه) انجام می‌شود.
            try:
                self._drop_pending = self._query_drop(wparam)
            except Exception:
                self._drop_pending = None
            return 0
        return CallWindowProc(self._box_old, hwnd, msg, wparam, lparam)

    def _dock_wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            try:
                self._drop_pending = self._query_drop(wparam)
            except Exception:
                self._drop_pending = None
            return 0
        return CallWindowProc(self._dock_old, hwnd, msg, wparam, lparam)


    # ------------------------------------------------------------------
    # آیکن سینی سیستم
    # ------------------------------------------------------------------
    def _add_tray_icon(self):
        hicon = load_hicon()
        self._nid = NOTIFYICONDATA()
        self._nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        self._nid.hWnd = self.root_hwnd
        self._nid.uID = 1
        self._nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self._nid.uCallbackMessage = TRAY_CALLBACK
        self._nid.hIcon = hicon
        self._nid.szTip = "{} — کلیک کنید تا باکس باز شود".format(APP_NAME)
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self._nid))
        self._tray_added = True

    def _remove_tray_icon(self):
        if self._tray_added:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            self._tray_added = False

    def _show_tray_menu(self):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN, "باز کردن باکس")
        user32.AppendMenuW(
            hmenu, MF_STRING | (MF_CHECKED if self.dock_is_visible() else 0),
            ID_DOCK, "نمایش آیکن شناور",
        )
        user32.AppendMenuW(hmenu, MF_STRING, ID_HISTORY, "تاریخچه و بازگردانی")
        user32.AppendMenuW(hmenu, MF_STRING, ID_SETTINGS, "تنظیمات")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "خروج")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.root_hwnd)
        cmd = user32.TrackPopupMenu(
            hmenu, TPM_RETURNCMD | TPM_RIGHTBUTTON, pt.x, pt.y, 0, self.root_hwnd, None
        )
        user32.DestroyMenu(hmenu)
        if cmd == ID_OPEN:
            self.show_box()
        elif cmd == ID_DOCK:
            self.toggle_dock()
        elif cmd == ID_HISTORY:
            self.open_history()
        elif cmd == ID_SETTINGS:
            self.open_settings()
        elif cmd == ID_EXIT:
            self.quit()

    # ------------------------------------------------------------------
    # کلید میانبر
    # ------------------------------------------------------------------
    def register_hotkey(self):
        """کلید میانبر را بر اساس کد اسکن (scan code) ثبت می‌کند."""
        hk = self.cfg.get("hotkey", {})
        vk = vk_from_key(hk.get("key", ""))
        has_mod = bool(
            hk.get("ctrl") or hk.get("alt") or hk.get("shift") or hk.get("win")
        )
        if vk and has_mod:
            self.hotkey_scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
        else:
            self.hotkey_scan = 0
        self.hotkey_mods = {
            "ctrl": bool(hk.get("ctrl")),
            "alt": bool(hk.get("alt")),
            "shift": bool(hk.get("shift")),
            "win": bool(hk.get("win")),
        }
        self._hk_down = False
        return bool(self.hotkey_scan)

    def _install_hook(self):
        self._hook_proc = HOOKPROC(self._ll_keyboard)
        hmod = kernel32.GetModuleHandleW(None)
        self._hook = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._hook_proc, hmod, 0
        )

    @staticmethod
    def _is_down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)

    def _matches_hotkey(self, scan):
        if not self.hotkey_scan or scan != self.hotkey_scan:
            return False
        m = self.hotkey_mods
        if m.get("ctrl") and not self._is_down(VK_CONTROL):
            return False
        if m.get("shift") and not self._is_down(VK_SHIFT):
            return False
        if m.get("alt") and not self._is_down(VK_MENU):
            return False
        if m.get("win") and not (self._is_down(VK_LWIN) or self._is_down(VK_RWIN)):
            return False
        return True

    def _ll_keyboard(self, nCode, wParam, lParam):
        if nCode == 0:
            try:
                kb = ctypes.cast(
                    lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)
                ).contents
                if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                    if self._matches_hotkey(kb.scanCode):
                        if not self._hk_down:
                            self._hk_down = True
                            self._hotkey_pending = True
                        return 1  # کلید را مصرف کن تا به برنامه‌ی فعال نرسد
                elif wParam in (WM_KEYUP, WM_SYSKEYUP):
                    if self.hotkey_scan and kb.scanCode == self.hotkey_scan:
                        was_down = self._hk_down
                        self._hk_down = False
                        if was_down:
                            return 1
            except Exception:
                pass
        return user32.CallNextHookEx(None, nCode, wParam, lParam)

    # ------------------------------------------------------------------
    # تنظیمات
    # ------------------------------------------------------------------
    def open_settings(self):
        SettingsWindow(self)

    def apply_config(self, cfg):
        # این‌ها را خود برنامه به‌روز می‌کند
        cfg["recent"] = self.cfg.get("recent", [])
        cfg["dock"] = self.cfg.get("dock", cfg.get("dock", {}))
        self.cfg = cfg
        save_config(cfg)
        self.register_hotkey()

    # ------------------------------------------------------------------
    def quit(self):
        try:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
                self._hook = None
        except Exception:
            pass
        self._remove_tray_icon()
        try:
            self.root.destroy()
        except Exception:
            pass


    # ------------------------------------------------------------------
    # حلقه‌ی پایش (هر ۴۰ میلی‌ثانیه) — تمام کارهای UI اینجا انجام می‌شود
    # تا از فراخوانی Tkinter از داخل کال‌بک‌های نیتیو جلوگیری شود.
    # ------------------------------------------------------------------
    def _poll(self):
        # ۱) کلید میانبر
        if self._hotkey_pending:
            self._hotkey_pending = False
            try:
                self.toggle_box()
            except Exception:
                pass

        # ۲) درخواست نمایش از نمونه‌ی دیگر (بدون فایل)
        if self._show_pending:
            self._show_pending = False
            try:
                self.show_box()
            except Exception:
                pass

        # ۳) فایل‌های درگ‌شده (از WM_DROPFILES یا آیکن/شورت‌کات)
        drop = self._drop_pending
        if drop is not None:
            self._drop_pending = None
            if drop:
                try:
                    self.on_drop(drop)
                except Exception:
                    pass

        # ۴) نتایج پردازش پس‌زمینه (کپی/انتقال/بازگردانی)
        try:
            while True:
                item = self._xfer_queue.get_nowait()
                kind = item[0]
                if kind == "xfer":
                    (_, ok, errors, records, cancelled, verb, mode, dest,
                     leaf_name) = item
                    self._on_transfer_done(
                        ok, errors, records, cancelled, verb, mode, dest,
                        leaf_name,
                    )
                elif kind == "undo":
                    _, batch_id, ok, errors, remaining = item
                    self._on_undo_done(batch_id, ok, errors, remaining)
                elif kind == "progress":
                    pass
        except queue.Empty:
            pass

        # ۵) به‌روزرسانی نوار پیشرفت (اگر پردازشی در جریان است)
        if self._xfer_running and self._progress is not None:
            try:
                frac = self._progress.fraction()
                self.progress.config(value=int(frac * 1000))
                done = self._progress.items_done
                total = self._progress.items_total
                verb = self._xfer_verb
                status = "در حال {}... ({}/{})".format(verb, done, total)
                if status != self._last_status:
                    self._last_status = status
                    self.status_label.config(text=status)
            except Exception:
                pass

        self.root.after(POLL_MS, self._poll)

    def queue_initial_files(self, paths):
        """فایل‌هایی که هنگام اجرا روی آیکن/شورت‌کات رها شدند."""
        paths = [p for p in paths if p and os.path.exists(p)]
        if paths:
            self._drop_pending = paths
        else:
            self._show_pending = True

    def run(self):
        self.root.after(POLL_MS, self._poll)
        self.root.mainloop()



def _center_on(win, parent=None):
    try:
        win.update_idletasks()
        w = win.winfo_width()
        h = win.winfo_height()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        x = (sw - w) // 2
        y = (sh - h) // 3
        win.geometry("+{}+{}".format(max(0, x), max(0, y)))
    except Exception:
        pass


def hotkey_text(hk):
    parts = []
    if hk.get("ctrl"):
        parts.append("Ctrl")
    if hk.get("alt"):
        parts.append("Alt")
    if hk.get("shift"):
        parts.append("Shift")
    if hk.get("win"):
        parts.append("Win")
    key = str(hk.get("key", "")).strip()
    if key:
        parts.append(key.upper())
    return " + ".join(parts) if parts else "(تعیین‌نشده)"


class LeafDialog:
    """دیالوگ افزودن/ویرایش یک مقصد (برگ)."""

    def __init__(self, parent, title, item=None):
        self.result = None
        item = item or {}
        self.win = tk.Toplevel(parent)
        self.win.title(title)
        self.win.transient(parent)
        self.win.resizable(False, False)
        self.win.configure(bg="#f4f4f4")
        try:
            self.win.grab_set()
        except Exception:
            pass

        frm = tk.Frame(self.win, bg="#f4f4f4")
        frm.pack(fill="both", expand=True, padx=12, pady=10)

        tk.Label(frm, text="نام مقصد:", bg="#f4f4f4",
                 font=("Tahoma", 9)).grid(row=0, column=1, sticky="e", pady=4)
        self.v_name = tk.StringVar(value=item.get("name", ""))
        tk.Entry(frm, textvariable=self.v_name, width=34,
                 justify="right").grid(row=0, column=0, sticky="we", pady=4)

        tk.Label(frm, text="مسیر پوشه:", bg="#f4f4f4",
                 font=("Tahoma", 9)).grid(row=1, column=1, sticky="e", pady=4)
        prow = tk.Frame(frm, bg="#f4f4f4")
        prow.grid(row=1, column=0, sticky="we", pady=4)
        self.v_path = tk.StringVar(value=item.get("path", ""))
        tk.Entry(prow, textvariable=self.v_path, width=26,
                 justify="left").pack(side="right", fill="x", expand=True)
        tk.Button(prow, text="انتخاب...",
                  command=self._browse).pack(side="left", padx=(0, 4))

        tk.Label(frm, text="حالت:", bg="#f4f4f4",
                 font=("Tahoma", 9)).grid(row=2, column=1, sticky="e", pady=4)
        self.v_mode = tk.StringVar(
            value=MODE_LABELS.get(item.get("mode", "ask"), MODE_LABELS["ask"]))
        ttk.Combobox(frm, textvariable=self.v_mode, state="readonly",
                     values=list(MODE_LABELS.values()), width=31).grid(
            row=2, column=0, sticky="we", pady=4)

        tk.Label(frm, text="زیرپوشه‌ی خودکار:", bg="#f4f4f4",
                 font=("Tahoma", 9)).grid(row=3, column=1, sticky="e", pady=4)
        self.v_sub = tk.StringVar(
            value=SUB_LABELS.get(item.get("sub", "none"), SUB_LABELS["none"]))
        ttk.Combobox(frm, textvariable=self.v_sub, state="readonly",
                     values=list(SUB_LABELS.values()), width=31).grid(
            row=3, column=0, sticky="we", pady=4)

        frm.columnconfigure(0, weight=1)

        btns = tk.Frame(self.win, bg="#f4f4f4")
        btns.pack(fill="x", padx=12, pady=(0, 10))
        tk.Button(btns, text="ذخیره", width=10,
                  command=self._ok).pack(side="right", padx=4)
        tk.Button(btns, text="انصراف", width=10,
                  command=self.win.destroy).pack(side="right", padx=4)

        _center_on(self.win, parent)
        self.win.bind("<Return>", lambda e: self._ok())
        self.win.bind("<Escape>", lambda e: self.win.destroy())
        parent.wait_window(self.win)

    def _browse(self):
        d = filedialog.askdirectory(parent=self.win, title="انتخاب پوشه‌ی مقصد")
        if d:
            self.v_path.set(os.path.normpath(d))
            if not self.v_name.get().strip():
                self.v_name.set(os.path.basename(d.rstrip("/\\")) or d)

    def _ok(self):
        name = self.v_name.get().strip()
        path = self.v_path.get().strip()
        if not name:
            messagebox.showwarning(APP_NAME, "نام مقصد را وارد کنید.", parent=self.win)
            return
        if not path:
            messagebox.showwarning(APP_NAME, "مسیر پوشه را انتخاب کنید.", parent=self.win)
            return
        r = {
            "name": name,
            "path": os.path.normpath(path),
            "mode": LABEL_TO_MODE.get(self.v_mode.get(), "ask"),
        }
        sub = LABEL_TO_SUB.get(self.v_sub.get(), "none")
        if sub != "none":
            r["sub"] = sub
        self.result = r
        self.win.destroy()



class SettingsWindow:
    """پنجره‌ی تنظیمات: مقصدها، رفتار باکس، کلید میانبر و گزینه‌ها."""

    def __init__(self, app):
        self.app = app
        self.cfg = json.loads(json.dumps(app.cfg))  # کپی برای ویرایش
        self.item_map = {}  # iid -> {"kind":..., "item":...}
        self.hotkey = dict(self.cfg.get("hotkey", {}))
        self._capturing = False

        self.win = tk.Toplevel(app.root)
        self.win.title("{} — تنظیمات".format(APP_NAME))
        self.win.configure(bg="#f4f4f4")
        self.win.resizable(False, False)
        try:
            if os.path.exists(ICON_PATH):
                self.win.iconbitmap(ICON_PATH)
        except Exception:
            pass
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass

        root = tk.Frame(self.win, bg="#f4f4f4")
        root.pack(fill="both", expand=True, padx=12, pady=10)

        # ---- بخش مقصدها ----
        catf = tk.LabelFrame(root, text="مقصدها و گروه‌ها", bg="#f4f4f4",
                             font=("Tahoma", 9, "bold"))
        catf.pack(fill="both", expand=True)
        inner = tk.Frame(catf, bg="#f4f4f4")
        inner.pack(fill="both", expand=True, padx=8, pady=8)

        tbox = tk.Frame(inner, bg="#f4f4f4")
        tbox.pack(side="right", fill="both", expand=True)
        sb = tk.Scrollbar(tbox)
        sb.pack(side="left", fill="y")
        self.tree = ttk.Treeview(tbox, show="tree", height=11,
                                 selectmode="browse", yscrollcommand=sb.set)
        self.tree.pack(side="right", fill="both", expand=True)
        sb.config(command=self.tree.yview)
        self.tree.bind("<Double-Button-1>", lambda e: self._edit())

        bcol = tk.Frame(inner, bg="#f4f4f4")
        bcol.pack(side="left", fill="y", padx=(8, 0))
        for txt, cmd in (
            ("➕ مقصد", self._add_leaf),
            ("📁 گروه", self._add_group),
            ("✏ ویرایش", self._edit),
            ("🗑 حذف", self._delete),
            ("⬆ بالا", lambda: self._move(-1)),
            ("⬇ پایین", lambda: self._move(1)),
            ("⇤ از گروه خارج", self._unparent),
        ):
            tk.Button(bcol, text=txt, width=12,
                      command=cmd).pack(fill="x", pady=2)
        tk.Label(bcol, text="برای انتقال به یک گروه،\nمقصد را روی گروه بکشید\nو رها کنید.", bg="#f4f4f4",
                 fg="#666", font=("Tahoma", 7),
                 justify="right").pack(fill="x", pady=(8, 0))

        # درگ‌ودراپ داخل درخت (انتقال به گروه/جابه‌جایی)
        self.tree.bind("<ButtonPress-1>", self._drag_start)
        self.tree.bind("<B1-Motion>", self._drag_motion)
        self.tree.bind("<ButtonRelease-1>", self._drag_drop)
        self._drag_iid = None

        self._reload_tree()
        self._build_rest(root)
        _center_on(self.win, app.root)
        self.win.bind("<Escape>", lambda e: self._on_escape())

    def _build_rest(self, root):
        # ---- رفتار باکس ----
        boxf = tk.LabelFrame(root, text="رفتار باکس", bg="#f4f4f4",
                             font=("Tahoma", 9, "bold"))
        boxf.pack(fill="x", pady=(10, 0))
        bi = tk.Frame(boxf, bg="#f4f4f4")
        bi.pack(fill="x", padx=8, pady=8)

        self.v_close_after = tk.BooleanVar(
            value=bool(self.cfg.get("close_after", True)))
        tk.Checkbutton(
            bi, text="پس از انتقال/کپی، باکس خودکار بسته شود",
            variable=self.v_close_after, bg="#f4f4f4", anchor="e",
            font=("Tahoma", 9), activebackground="#f4f4f4",
            selectcolor="#ffffff", justify="right",
        ).pack(anchor="e")

        crow = tk.Frame(bi, bg="#f4f4f4")
        crow.pack(fill="x", pady=(6, 0))
        tk.Label(crow, text="باکس در کدام گوشه‌ی صفحه باز شود:",
                 bg="#f4f4f4", font=("Tahoma", 9)).pack(side="right")
        cur_corner = self.cfg.get("box_corner", "br")
        self.v_corner = tk.StringVar(
            value=CORNER_LABELS.get(cur_corner, CORNER_LABELS["br"]))
        ttk.Combobox(crow, textvariable=self.v_corner, state="readonly",
                     values=list(CORNER_LABELS.values()), width=16).pack(
            side="right", padx=(0, 6))
        tk.Label(
            bi,
            text="باکس همیشه هنگام درگ‌ودراپ تازه به این گوشه بازمی‌گردد.",
            bg="#f4f4f4", fg="#666", font=("Tahoma", 7),
            anchor="e", justify="right").pack(anchor="e", pady=(2, 0))

        # ---- کلید میانبر ----
        hkf = tk.LabelFrame(root, text="کلید میانبر (نمایش/پنهان کردن باکس)",
                            bg="#f4f4f4", font=("Tahoma", 9, "bold"))
        hkf.pack(fill="x", pady=(10, 0))
        hi = tk.Frame(hkf, bg="#f4f4f4")
        hi.pack(fill="x", padx=8, pady=8)
        self.hk_label = tk.Label(hi, text=hotkey_text(self.hotkey),
                                 bg="#ffffff", relief="groove", bd=1,
                                 width=22, font=("Tahoma", 10, "bold"))
        self.hk_label.pack(side="right", padx=(0, 6), ipady=3)
        self.hk_btn = tk.Button(hi, text="ثبت کلید جدید",
                                command=self._capture_hotkey)
        self.hk_btn.pack(side="right")
        tk.Label(hi, text="(حداقل یک کلید ترکیبی Ctrl/Alt/Shift لازم است)",
                 bg="#f4f4f4", fg="#666", font=("Tahoma", 7)).pack(
            side="right", padx=6)

        # ---- گزینه‌ها ----
        optf = tk.LabelFrame(root, text="گزینه‌ها", bg="#f4f4f4",
                             font=("Tahoma", 9, "bold"))
        optf.pack(fill="x", pady=(10, 0))
        oi = tk.Frame(optf, bg="#f4f4f4")
        oi.pack(fill="x", padx=8, pady=8)
        self.v_dock = tk.BooleanVar(
            value=bool(self.cfg.get("dock", {}).get("visible", True)))
        tk.Checkbutton(oi, text="نمایش آیکن شناور روی صفحه",
                       variable=self.v_dock, bg="#f4f4f4", anchor="e",
                       font=("Tahoma", 9), activebackground="#f4f4f4",
                       selectcolor="#ffffff").pack(anchor="e")
        self.v_dpi = tk.BooleanVar(value=bool(self.cfg.get("dpi_aware", True)))
        tk.Checkbutton(oi, text="سازگاری با نمایشگرهای High-DPI (نیازمند اجرای مجدد)",
                       variable=self.v_dpi, bg="#f4f4f4", anchor="e",
                       font=("Tahoma", 9), activebackground="#f4f4f4",
                       selectcolor="#ffffff").pack(anchor="e")
        self.v_startup = tk.BooleanVar(value=is_startup_enabled())
        tk.Checkbutton(oi, text="اجرای خودکار هنگام روشن شدن ویندوز",
                       variable=self.v_startup, bg="#f4f4f4", anchor="e",
                       font=("Tahoma", 9), activebackground="#f4f4f4",
                       selectcolor="#ffffff").pack(anchor="e")

        # ---- دکمه‌ها ----
        btns = tk.Frame(root, bg="#f4f4f4")
        btns.pack(fill="x", pady=(12, 0))
        tk.Button(btns, text="💾 ذخیره", width=12,
                  command=self._save).pack(side="right", padx=4)
        tk.Button(btns, text="انصراف", width=12,
                  command=self.win.destroy).pack(side="right", padx=4)


    # ---- ساخت/بازسازی درخت ویرایش ----
    def _reload_tree(self, select=None):
        for iid in self.tree.get_children(""):
            self.tree.delete(iid)
        self.item_map = {}
        for c in self.cfg.get("categories", []):
            if c.get("children") is not None and not c.get("path"):
                gid = self.tree.insert(
                    "", "end", text="📁 {}".format(c.get("name", "?")), open=True)
                self.item_map[gid] = {"kind": "group", "item": c}
                for ch in c.get("children") or []:
                    self._insert_leaf(gid, ch)
            else:
                self._insert_leaf("", c)
        if select and self.tree.exists(select):
            self.tree.selection_set(select)
            self.tree.see(select)

    def _insert_leaf(self, parent, item):
        label = "📂 {}   [{}]".format(
            item.get("name", "?"),
            MODE_LABELS.get(item.get("mode", "ask"), "?"))
        if item.get("sub") and item.get("sub") != "none":
            label += "  · زیرپوشه"
        iid = self.tree.insert(parent, "end", text=label)
        self.item_map[iid] = {"kind": "leaf", "item": item}
        return iid

    def _sel(self):
        s = self.tree.selection()
        return s[0] if s else None

    def _add_leaf(self):
        dlg = LeafDialog(self.win, "افزودن مقصد")
        if not dlg.result:
            return
        sel = self._sel()
        parent = ""
        if sel and self.item_map.get(sel, {}).get("kind") == "group":
            parent = sel
        elif sel:
            p = self.tree.parent(sel)
            if p:
                parent = p
        iid = self._insert_leaf(parent, dlg.result)
        self.tree.selection_set(iid)
        self.tree.see(iid)

    def _add_group(self):
        name = self._ask_name("نام گروه جدید:")
        if not name:
            return
        gid = self.tree.insert("", "end", text="📁 {}".format(name), open=True)
        self.item_map[gid] = {"kind": "group",
                              "item": {"name": name, "path": "", "children": []}}
        self.tree.selection_set(gid)
        self.tree.see(gid)

    def _ask_name(self, prompt, initial=""):
        dlg = tk.Toplevel(self.win)
        dlg.title(APP_NAME)
        dlg.configure(bg="#f4f4f4")
        dlg.resizable(False, False)
        dlg.transient(self.win)
        try:
            dlg.grab_set()
        except Exception:
            pass
        tk.Label(dlg, text=prompt, bg="#f4f4f4",
                 font=("Tahoma", 9)).pack(padx=14, pady=(12, 4), anchor="e")
        var = tk.StringVar(value=initial)
        ent = tk.Entry(dlg, textvariable=var, width=30, justify="right")
        ent.pack(padx=14, pady=4)
        ent.focus_set()
        out = {"v": None}

        def ok():
            out["v"] = var.get().strip()
            dlg.destroy()
        bf = tk.Frame(dlg, bg="#f4f4f4")
        bf.pack(fill="x", padx=14, pady=(4, 12))
        tk.Button(bf, text="تایید", width=9,
                  command=ok).pack(side="right", padx=4)
        tk.Button(bf, text="انصراف", width=9,
                  command=dlg.destroy).pack(side="right", padx=4)
        dlg.bind("<Return>", lambda e: ok())
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        _center_on(dlg, self.win)
        self.win.wait_window(dlg)
        return out["v"]

    def _edit(self):
        sel = self._sel()
        if not sel:
            return
        info = self.item_map.get(sel)
        if not info:
            return
        if info["kind"] == "group":
            name = self._ask_name("نام گروه:", info["item"].get("name", ""))
            if name:
                info["item"]["name"] = name
                self.tree.item(sel, text="📁 {}".format(name))
        else:
            dlg = LeafDialog(self.win, "ویرایش مقصد", info["item"])
            if dlg.result:
                info["item"] = dlg.result
                self.item_map[sel] = {"kind": "leaf", "item": dlg.result}
                lbl = "📂 {}   [{}]".format(
                    dlg.result.get("name", "?"),
                    MODE_LABELS.get(dlg.result.get("mode", "ask"), "?"))
                if dlg.result.get("sub"):
                    lbl += "  · زیرپوشه"
                self.tree.item(sel, text=lbl)

    def _delete(self):
        sel = self._sel()
        if not sel:
            return
        info = self.item_map.get(sel, {})
        name = info.get("item", {}).get("name", "")
        if info.get("kind") == "group" and self.tree.get_children(sel):
            if not messagebox.askyesno(
                    APP_NAME,
                    "گروه «{}» و همه‌ی مقصدهای داخلش حذف شوند؟".format(name),
                    parent=self.win):
                return
        self.tree.delete(sel)
        self.item_map.pop(sel, None)

    def _move(self, delta):
        sel = self._sel()
        if not sel:
            return
        parent = self.tree.parent(sel)
        idx = self.tree.index(sel)
        self.tree.move(sel, parent, max(0, idx + delta))

    def _unparent(self):
        sel = self._sel()
        if not sel:
            return
        if self.item_map.get(sel, {}).get("kind") != "leaf":
            return
        parent = self.tree.parent(sel)
        if not parent:
            return
        self.tree.move(sel, "", "end")


    # ---- درگ‌ودراپ داخل درخت ----
    def _drag_start(self, event):
        self._drag_iid = self.tree.identify_row(event.y)

    def _drag_motion(self, event):
        if self._drag_iid:
            try:
                self.tree.config(cursor="hand2")
            except Exception:
                pass

    def _drag_drop(self, event):
        try:
            self.tree.config(cursor="")
        except Exception:
            pass
        src = self._drag_iid
        self._drag_iid = None
        if not src or not self.tree.exists(src):
            return
        if self.item_map.get(src, {}).get("kind") != "leaf":
            return  # فقط مقصدها را می‌شود جابه‌جا کرد
        target = self.tree.identify_row(event.y)
        if not target or target == src:
            return
        tinfo = self.item_map.get(target, {})
        if tinfo.get("kind") == "group":
            self.tree.move(src, target, "end")
            self.tree.item(target, open=True)
        else:
            parent = self.tree.parent(target)
            idx = self.tree.index(target)
            self.tree.move(src, parent, idx)

    # ---- ثبت کلید میانبر ----
    def _capture_hotkey(self):
        if self._capturing:
            return
        self._capturing = True
        self.hk_btn.config(text="کلید را فشار دهید...", state="disabled")
        self.hk_label.config(text="منتظر...", bg="#fff8e1")
        self._cap_bind = self.win.bind_all("<KeyPress>", self._on_capture_key)

    def _on_capture_key(self, event):
        ks = event.keysym
        # کلیدهای ترکیبی تنها را نادیده بگیر (منتظر کلید اصلی بمان)
        if ks in ("Shift_L", "Shift_R", "Control_L", "Control_R",
                  "Alt_L", "Alt_R", "Win_L", "Win_R", "Super_L", "Super_R"):
            return "break"
        state = event.state
        ctrl = bool(state & 0x0004)
        shift = bool(state & 0x0001)
        alt = bool(state & 0x20000) or bool(state & 0x0008)
        key = ks
        if len(ks) == 1:
            key = ks.upper()
        hk = {"ctrl": ctrl, "shift": shift, "alt": alt, "win": False, "key": key}
        if not (ctrl or alt or shift):
            self.hk_label.config(
                text="حداقل Ctrl/Alt/Shift لازم است", bg="#ffebee")
            return "break"
        self.hotkey = hk
        self._end_capture()
        self.hk_label.config(text=hotkey_text(hk), bg="#ffffff")
        return "break"

    def _end_capture(self):
        self._capturing = False
        try:
            self.win.unbind_all("<KeyPress>")
        except Exception:
            pass
        try:
            self.hk_btn.config(text="ثبت کلید جدید", state="normal")
        except Exception:
            pass

    def _on_escape(self):
        if self._capturing:
            self._end_capture()
            self.hk_label.config(text=hotkey_text(self.hotkey), bg="#ffffff")
            return
        self.win.destroy()

    # ---- جمع‌آوری و ذخیره ----
    def _collect_categories(self):
        cats = []
        for iid in self.tree.get_children(""):
            info = self.item_map.get(iid, {})
            if info.get("kind") == "group":
                kids = []
                for c in self.tree.get_children(iid):
                    ci = self.item_map.get(c, {})
                    if ci.get("kind") == "leaf":
                        kids.append(ci["item"])
                g = dict(info["item"])
                g["path"] = ""
                g["children"] = kids
                cats.append(g)
            elif info.get("kind") == "leaf":
                cats.append(info["item"])
        return sanitize_categories(cats)

    def _save(self):
        if self._capturing:
            self._end_capture()
        cfg = self.app.cfg
        cfg["categories"] = self._collect_categories()
        cfg["hotkey"] = self.hotkey
        cfg["close_after"] = bool(self.v_close_after.get())
        cfg["box_corner"] = LABEL_TO_CORNER.get(self.v_corner.get(), "br")
        dock = dict(cfg.get("dock", {}))
        dock["visible"] = bool(self.v_dock.get())
        cfg["dock"] = dock
        cfg["dpi_aware"] = bool(self.v_dpi.get())
        self.app.apply_config(cfg)
        # اجرای خودکار هنگام روشن شدن
        try:
            set_startup(bool(self.v_startup.get()))
        except Exception:
            pass
        # اعمال فوری وضعیت آیکن شناور
        try:
            if dock["visible"] and not self.app.dock_is_visible():
                self.app.toggle_dock()
            elif not dock["visible"] and self.app.dock_is_visible():
                self.app.toggle_dock()
        except Exception:
            pass
        self.win.destroy()
        messagebox.showinfo(
            APP_NAME,
            "تنظیمات ذخیره شد.\nاگر گزینه‌ی High-DPI را تغییر دادید، برنامه را دوباره اجرا کنید.",
            parent=self.app.box if self.app.box_visible else None)



class HistoryWindow:
    """تاریخچه‌ی عملیات و دکمه‌ی بازگردانی برای هر دسته."""

    def __init__(self, app):
        self.app = app
        self.win = tk.Toplevel(app.root)
        self.win.title("{} — تاریخچه و بازگردانی".format(APP_NAME))
        self.win.configure(bg="#f4f4f4")
        self.win.geometry("560x420")
        try:
            if os.path.exists(ICON_PATH):
                self.win.iconbitmap(ICON_PATH)
        except Exception:
            pass
        try:
            self.win.attributes("-topmost", True)
        except Exception:
            pass

        top = tk.Frame(self.win, bg="#f4f4f4")
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="عملیات اخیر (جدیدترین در بالا):",
                 bg="#f4f4f4", font=("Tahoma", 9, "bold")).pack(side="right")

        body = tk.Frame(self.win, bg="#f4f4f4")
        body.pack(fill="both", expand=True, padx=10, pady=4)
        self.canvas = tk.Canvas(body, bg="#ffffff", highlightthickness=1,
                                highlightbackground="#ddd")
        vs = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="left", fill="y")
        self.canvas.pack(side="right", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg="#ffffff")
        self._win_id = self.canvas.create_window((0, 0), window=self.inner,
                                                  anchor="nw")
        self.inner.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self._win_id, width=e.width))
        self.canvas.bind_all("<MouseWheel>", self._wheel)

        bottom = tk.Frame(self.win, bg="#f4f4f4")
        bottom.pack(fill="x", padx=10, pady=(4, 10))
        tk.Button(bottom, text="بستن", width=10,
                  command=self._close).pack(side="right")

        self.win.protocol("WM_DELETE_WINDOW", self._close)
        _center_on(self.win, app.root)
        self.refresh()

    def _wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass

    def _close(self):
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        self.app._history_win = None
        self.win.destroy()

    def refresh(self):
        for w in self.inner.winfo_children():
            w.destroy()
        hist = list(reversed(self.app._history))
        if not hist:
            tk.Label(self.inner, text="هنوز عملیاتی انجام نشده است.",
                     bg="#ffffff", fg="#888",
                     font=("Tahoma", 10)).pack(pady=30)
            return
        for b in hist:
            self._row(b)

    def _row(self, batch):
        items = batch.get("items", [])
        done = batch.get("undone") or not items
        card = tk.Frame(self.inner, bg="#fafafa", bd=1, relief="solid")
        card.pack(fill="x", padx=6, pady=4)
        verb = "انتقال" if batch.get("mode") == "move" else "کپی"
        head = "{}  ·  {} مورد  ·  به «{}»".format(
            verb, len(items), batch.get("name", "?"))
        tk.Label(card, text=head, bg="#fafafa", anchor="e",
                 font=("Tahoma", 9, "bold"), justify="right").pack(
            fill="x", padx=8, pady=(6, 0))
        tk.Label(card, text="{}  ·  {}".format(
            batch.get("time", ""), batch.get("dest", "")),
            bg="#fafafa", fg="#666", anchor="e", font=("Tahoma", 8),
            justify="right", wraplength=480).pack(fill="x", padx=8)
        rowb = tk.Frame(card, bg="#fafafa")
        rowb.pack(fill="x", padx=8, pady=(2, 6))
        if done:
            tk.Label(rowb, text="✓ بازگردانده شده / خالی",
                     bg="#fafafa", fg="#2e7d32",
                     font=("Tahoma", 8)).pack(side="right")
        else:
            bid = batch.get("id")
            tk.Button(rowb, text="↩ بازگردانی‌ی این دسته",
                      bg="#fff3e0",
                      command=lambda i=bid: self._undo(i)).pack(side="right")

    def _undo(self, batch_id):
        self.app._undo_batch(batch_id, parent=self.win)


def main():
    # تک‌نمونه: اگر نمونه‌ی دیگری در حال اجراست، مسیرها را به آن بفرست و خارج شو
    args = [a for a in sys.argv[1:] if a]
    existing = find_running_instance()
    if existing:
        try:
            send_paths_to_instance(existing, args)
        except Exception:
            pass
        return
    app = DragDropSorter()
    if args:
        app.queue_initial_files(args)
    app.run()


if __name__ == "__main__":
    main()
