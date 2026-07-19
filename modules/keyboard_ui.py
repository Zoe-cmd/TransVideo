# -*- coding: utf-8 -*-
"""键盘交互 UI 模块 —— 上下键选择菜单

功能：
  - 启用 Windows VT100 颜色支持
  - 提供上下键 + Enter 的交互式选择菜单
  - 支持数字键快速跳转
  - 当前项高亮显示
  - 底部显示快捷键帮助
  - 跨平台支持（Windows msvcrt / Unix termios）
  - 无法获取键盘输入时自动回退到数字输入

使用方法：
  from modules.keyboard_ui import keyboard_select

  idx = keyboard_select("请选择操作", [
      "🌐 翻译抖音视频",
      "▶️  翻译 YouTube",
      "📁 翻译本地视频",
  ], default=0)
  if idx is None:
      print("用户取消")
  else:
      print(f"选择了: {idx}")
"""

import sys
import os


# ==================== ANSI 颜色 ====================

class Color:
    """ANSI 颜色代码"""
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    # 前景色
    BLACK = '\033[30m'
    RED = '\033[91m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    MAGENTA = '\033[95m'
    CYAN = '\033[96m'
    WHITE = '\033[97m'
    # 背景色（用于高亮选中项）
    BG_CYAN = '\033[46m'
    BG_BLUE = '\033[44m'
    BG_MAGENTA = '\033[45m'
    BG_GREEN = '\033[42m'
    BG_YELLOW = '\033[43m'
    # 组合样式
    HEADER = '\033[95m\033[1m'
    SELECTED = '\033[96m\033[1m'  # 选中项颜色（青色加粗）
    SELECTED_BG = '\033[44m\033[97m'  # 选中项背景（蓝底白字）
    HINT = '\033[2m'  # 提示文字（暗淡）


# ==================== Windows VT100 启用 ====================

_VT100_ENABLED = False


def enable_vt100():
    """在 Windows 上启用 VT100 虚拟终端处理（让 ANSI 颜色生效）

    Windows 10 1511+ 支持 ANSI 转义序列，但需要显式启用。
    在 Unix 系统上此函数无操作。

    返回：True 表示已启用（或本就支持）
    """
    global _VT100_ENABLED
    if _VT100_ENABLED:
        return True
    if not sys.platform.startswith("win"):
        _VT100_ENABLED = True
        return True

    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # 获取标准输出句柄
        STD_OUTPUT_HANDLE = -11
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            return False

        # 获取当前控制台模式
        mode = wintypes.DWORD()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False

        # 启用 VT100 处理（0x0004）和 ANSI 颜色
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        ENABLE_PROCESSED_OUTPUT = 0x0001
        new_mode = mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING | ENABLE_PROCESSED_OUTPUT

        if not kernel32.SetConsoleMode(handle, new_mode):
            return False

        _VT100_ENABLED = True
        return True
    except Exception:
        return False


# ==================== 键盘输入读取 ====================

def _read_key_windows():
    """Windows: 使用 msvcrt 读取单次按键

    返回：键名（字符串）
      - 'up' / 'down' / 'left' / 'right'  方向键
      - 'enter' / 'escape' / 'space' / 'tab'
      - 'home' / 'end' / 'pageup' / 'pagedown'
      - 'backspace' / 'delete'
      - 单字符（小写字母、数字等）
      - 'unknown' 无法识别
    """
    try:
        import msvcrt
    except ImportError:
        return None

    ch = msvcrt.getch()
    if ch in (b'\x00', b'\xe0'):
        # 特殊键前缀，读取第二个字节
        ch2 = msvcrt.getch()
        key_map = {
            72: 'up',      # ↑
            80: 'down',    # ↓
            75: 'left',    # ←
            77: 'right',   # →
            71: 'home',    # Home
            79: 'end',     # End
            73: 'pageup',  # Page Up
            81: 'pagedown',# Page Down
            82: 'insert',  # Insert
            83: 'delete',  # Delete
            13: 'enter',
        }
        return key_map.get(ch2[0], 'unknown')

    # 普通键
    if ch == b'\r':  # Enter (CR)
        return 'enter'
    if ch == b'\n':  # Enter (LF)
        return 'enter'
    if ch == b'\x1b':  # Esc
        return 'escape'
    if ch == b'\x08':  # Backspace
        return 'backspace'
    if ch == b'\t':  # Tab
        return 'tab'
    if ch == b' ':  # Space
        return 'space'

    # 尝试解码为字符
    try:
        return ch.decode('ascii', errors='ignore').lower()
    except Exception:
        return 'unknown'


def _read_key_unix():
    """Unix: 使用 termios + tty 读取单次按键

    返回：键名（字符串），格式同 _read_key_windows
    """
    try:
        import termios
        import tty
    except ImportError:
        return None

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x1b':
            # 可能是特殊键，读取后续字节
            # ESC 序列格式：\x1b[A (上) / \x1b[B (下) / \x1b[C (右) / \x1b[D (左)
            # 也可能是单独的 Esc 键
            import select
            # 短暂等待看是否有后续字节
            if select.select([sys.stdin], [], [], 0.05)[0]:
                ch2 = sys.stdin.read(1)
                if ch2 == '[':
                    ch3 = sys.stdin.read(1)
                    key_map = {
                        'A': 'up',
                        'B': 'down',
                        'C': 'right',
                        'D': 'left',
                        'H': 'home',
                        'F': 'end',
                    }
                    return key_map.get(ch3, 'unknown')
                return 'unknown'
            return 'escape'
        if ch == '\r' or ch == '\n':
            return 'enter'
        if ch == '\x7f':  # Backspace (Unix)
            return 'backspace'
        if ch == '\t':
            return 'tab'
        if ch == ' ':
            return 'space'
        return ch.lower()
    except Exception:
        return 'unknown'
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass


def read_key():
    """读取单次按键，返回键名

    自动检测平台并调用对应的读取函数。
    返回 None 表示无法读取键盘输入（应回退到数字输入）。
    """
    if sys.platform.startswith("win"):
        return _read_key_windows()
    else:
        return _read_key_unix()


def can_read_key():
    """检测当前环境是否支持键盘单字符读取

    用于决定是否启用上下键选择菜单。
    """
    # 检查是否在交互式终端中
    if not sys.stdin.isatty():
        return False
    if not sys.stdout.isatty():
        return False

    # 检查所需模块
    if sys.platform.startswith("win"):
        try:
            import msvcrt  # noqa: F401
            return True
        except ImportError:
            return False
    else:
        try:
            import termios  # noqa: F401
            import tty  # noqa: F401
            return True
        except ImportError:
            return False


# ==================== 交互式选择菜单 ====================

def keyboard_select(prompt, options, default=0, help_text=None):
    """交互式键盘选择菜单

    参数：
      prompt: 标题提示文字
      options: 选项列表（字符串）
      default: 默认选中的索引
      help_text: 自定义帮助文字（None 用默认）

    返回：
      选择的索引（int），或 None（用户按 Esc/q 取消）
    """
    if not options:
        return None

    # 启用 VT100
    enable_vt100()

    if not can_read_key():
        # 无法读取键盘，回退到数字输入
        return _fallback_input(prompt, options, default)

    current = default if 0 <= default < len(options) else 0
    total = len(options)

    # 默认帮助文字
    if help_text is None:
        help_text = (
            f"{Color.DIM}↑↓{Color.RESET} 选择  "
            f"{Color.DIM}Enter{Color.RESET} 确认  "
            f"{Color.DIM}数字键{Color.RESET} 快速跳转  "
            f"{Color.DIM}Esc/q{Color.RESET} 取消"
        )

    # 隐藏光标避免闪烁
    sys.stdout.write('\033[?25l')
    sys.stdout.flush()

    try:
        while True:
            # 绘制菜单
            _draw_menu(prompt, options, current, help_text)

            # 读取按键
            key = read_key()

            if key is None:
                # 无法读取，回退
                _clear_menu(total, len(help_text))
                return _fallback_input(prompt, options, current)

            if key in ('up', 'k'):  # k 是 vim 风格的上
                current = (current - 1) % total
            elif key in ('down', 'j'):  # j 是 vim 风格的下
                current = (current + 1) % total
            elif key in ('home', 'g'):
                current = 0
            elif key in ('end', 'G'):
                current = total - 1
            elif key in ('pageup',):
                current = max(0, current - 5)
            elif key in ('pagedown',):
                current = min(total - 1, current + 5)
            elif key in ('enter',):
                _clear_menu(total, len(help_text))
                return current
            elif key in ('escape', 'q'):
                _clear_menu(total, len(help_text))
                return None
            elif key and key.isdigit():
                # 数字键快速跳转
                num = int(key)
                if 1 <= num <= total:
                    current = num - 1
            # 其他键忽略

            # 清除旧菜单（移动光标到菜单开始位置）
            _clear_menu(total, len(help_text))
    finally:
        # 恢复光标
        sys.stdout.write('\033[?25h')
        sys.stdout.flush()


def _draw_menu(prompt, options, current, help_text):
    """绘制菜单

    输出格式：
      \n
      <prompt>
        <图标> <序号> <选项文字>  ← 当前项有高亮背景
        ...
      <help_text>
    """
    out = sys.stdout
    out.write('\n')

    # 标题
    out.write(f'{Color.HEADER}{prompt}{Color.RESET}\n')

    # 选项
    for i, opt in enumerate(options):
        if i == current:
            # 选中项：蓝底白字 + 指示符
            out.write(f'  {Color.SELECTED_BG} ▶ {i+1:>2}. {opt} {Color.RESET}\n')
        else:
            # 未选中项：暗淡显示
            out.write(f'    {Color.DIM}{i+1:>2}.{Color.RESET} {opt}\n')

    # 帮助文字
    out.write(f'\n  {help_text}\n')
    out.flush()


def _clear_menu(num_options, num_help_lines):
    """清除已绘制的菜单

    计算行数并向上移动光标清除。
    菜单结构：
      1 行空行
      1 行标题
      num_options 行选项
      1 行空行
      1 行帮助文字
    """
    total_lines = 1 + 1 + num_options + 1 + 1
    # 移动光标到菜单开始位置并清除
    sys.stdout.write(f'\033[{total_lines}A')  # 向上移动
    sys.stdout.write('\033[J')  # 清除从光标到屏幕末尾
    sys.stdout.flush()


def _fallback_input(prompt, options, default):
    """回退方案：数字输入选择

    当无法读取键盘单字符时使用。
    输入 q 或 0 取消（返回 None）。
    """
    print(f'\n{Color.HEADER}{prompt}{Color.RESET}')
    for i, opt in enumerate(options):
        marker = f'{Color.GREEN}►{Color.RESET}' if i == default else ' '
        print(f'  {marker} {Color.DIM}{i+1}.{Color.RESET} {opt}')
    while True:
        s = input(f'\n{Color.DIM}选择 (1-{len(options)}, q=取消, 默认 {default+1}): {Color.RESET}').strip()
        if not s:
            return default
        if s.lower() in ('q', 'quit', 'cancel', '0'):
            return None  # 取消
        try:
            idx = int(s) - 1
            if 0 <= idx < len(options):
                return idx
        except ValueError:
            pass
        print(f'{Color.RED}无效输入{Color.RESET}')


# ==================== 便捷封装 ====================

def keyboard_confirm(prompt, default_yes=True):
    """交互式确认对话框

    参数：
      prompt: 提示文字
      default_yes: 默认是否为"是"

    返回：True/False
    """
    if not can_read_key():
        # 回退到普通输入
        hint = f'{Color.GREEN}Y/n{Color.RESET}' if default_yes else f'{Color.YELLOW}y/N{Color.RESET}'
        s = input(f'{prompt} [{hint}]: ').strip().lower()
        if not s:
            return default_yes
        return s in ('y', 'yes', '是')

    options = ['是 (Y)', '否 (N)']
    default = 0 if default_yes else 1
    idx = keyboard_select(prompt, options, default=default)
    if idx is None:
        return default_yes
    return idx == 0


def keyboard_pause(msg='按任意键继续...'):
    """暂停等待任意键"""
    if not can_read_key():
        input(f'\n{Color.DIM}{msg}{Color.RESET}')
        return
    print(f'\n{Color.DIM}{msg}{Color.RESET}')
    read_key()
