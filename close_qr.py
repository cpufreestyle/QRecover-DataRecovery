import ctypes
from ctypes import wintypes
user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
found = []
def cb(hwnd, lp):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    if 'QRecover' in buf.value:
        found.append(hwnd)
    return True
user32.EnumWindows(WNDENUMPROC(cb), 0)
for h in found:
    user32.PostMessageW(h, 0x0010, 0, 0)
print(f'Closed {len(found)}')
