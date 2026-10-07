"""Exercise the shipped CMD entry points with an empty, isolated desktop.

No vault, password, screenshot, window text or process memory is read. Cleanup
is bound to the PID in this run's unique Qt lock and uses a normal WM_CLOSE.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--setup', action='store_true', help='Also exercise registration and the first-use entry point')
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('Windows is required')
    package = Path(args.package).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = output / ('empty-settings-' + uuid.uuid4().hex)
    settings.mkdir()
    (settings / 'settings.ini').write_text(
        '[General]\nAutoSaveAfterEveryChange=false\n[GUI]\nLanguage=zh_CN\n'
        'MinimizeOnClose=false\n[Browser]\nEnabled=true\nUpdateBinaryPath=false\n', encoding='utf-8')
    (settings / 'local.ini').write_text('[Browser]\nRiskAssessmentEnabled=true\n', encoding='utf-8')
    env = os.environ.copy()
    env['USERNAME'] = 'tkyp-launch-test-' + uuid.uuid4().hex
    lock = Path(env['TEMP']) / ('keepassxc-' + env['USERNAME'] + '.lock')
    report = {'complete': False, 'vaults': 0, 'screenshots': False, 'checks': []}
    user = ctypes.WinDLL('user32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsIconic.argtypes = [wintypes.HWND]
    user.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user.GetWindow.restype = wintypes.HWND
    user.GetLayeredWindowAttributes.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
                                              ctypes.POINTER(wintypes.BYTE), ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    user.MonitorFromRect.argtypes = [ctypes.POINTER(wintypes.RECT), wintypes.DWORD]
    user.MonitorFromRect.restype = wintypes.HANDLE
    user.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    test_pid = None
    registry_before = []
    if args.setup:
        import winreg
        for browser in ('Google\\Chrome', 'Microsoft\\Edge'):
            key_path = 'Software\\' + browser + '\\NativeMessagingHosts\\org.keepassxc.keepassxc_browser'
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    value, kind = winreg.QueryValueEx(key, '')
            except FileNotFoundError:
                value, kind = None, winreg.REG_SZ
            registry_before.append((key_path, value, kind))

    def record(name, passed, **extra):
        row = {'check': name, 'passed': bool(passed), **extra}
        report['checks'].append(row)
        print(json.dumps(row), flush=True)
        if not passed:
            raise RuntimeError(name)

    def bind_pid():
        # This file name contains a fresh UUID, so it cannot identify a personal instance.
        pid = int(lock.read_text(encoding='utf-8').splitlines()[0])
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise RuntimeError('test-process-not-running')
        try:
            value = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(value))
            if not kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
                raise RuntimeError('cannot-verify-test-process')
            if Path(value.value) != package / 'TheyKnowYourPasswords.exe':
                raise RuntimeError('unexpected-test-process')
        finally:
            kernel.CloseHandle(handle)
        return pid

    def windows(pid):
        found = []
        @callback_type
        def visit(hwnd, state):
            owner = wintypes.DWORD()
            user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                name = ctypes.create_unicode_buffer(256)
                user.GetClassNameW(hwnd, name, len(name))
                if name.value.endswith('QWindowIcon') and not user.GetWindow(hwnd, 4):
                    alpha = wintypes.BYTE(255)
                    color, flags = wintypes.DWORD(), wintypes.DWORD()
                    layered = user.GetLayeredWindowAttributes(hwnd, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags))
                    opaque = not layered or not (flags.value & 2) or alpha.value == 255
                    found.append((hwnd, bool(user.IsWindowVisible(hwnd)), bool(user.IsIconic(hwnd)), opaque))
            return True
        user.EnumWindows(visit, 0)
        return found

    def shown(pid):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if any(visible and not minimized and opaque and on_screen(hwnd)
                   for hwnd, visible, minimized, opaque in windows(pid)):
                return True
            time.sleep(0.1)
        return False

    def on_screen(hwnd):
        rect = wintypes.RECT()
        user.GetWindowRect(hwnd, ctypes.byref(rect))
        return bool(user.MonitorFromRect(ctypes.byref(rect), 0))

    def affinity(pid):
        for hwnd, *_ in windows(pid):
            value = wintypes.DWORD(0xffffffff)
            if user.GetWindowDisplayAffinity(hwnd, ctypes.byref(value)):
                return value.value
        return None

    def command(name, extra=()):
        # All arguments are local, generated paths; credentials never enter the command line.
        return [env['COMSPEC'], '/d', '/c', 'call', str(package / name),
                '-ConfigDirectory', str(settings), *extra]

    def launch(name):
        start = time.monotonic()
        result = subprocess.run(command(name), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW, timeout=25)
        record(name + '-exit-success', result.returncode == 0,
               elapsed_ms=round((time.monotonic() - start) * 1000))
        pid = bind_pid()
        displayed = shown(pid)
        if not displayed:
            for hwnd, visible, minimized, opaque in windows(pid):
                rect = wintypes.RECT()
                user.GetWindowRect(hwnd, ctypes.byref(rect))
                print(json.dumps({'diagnostic': 'display-state-only', 'visible': visible, 'minimized': minimized,
                                  'opaque': opaque, 'on_screen': on_screen(hwnd),
                                  'rect': [rect.left, rect.top, rect.right, rect.bottom]}), flush=True)
        record(name + '-visible-opaque-main-window', displayed)
        return pid

    try:
        test_pid = launch('Setup.cmd' if args.setup else 'Launch.cmd')
        record('local-startup-blocks-capture-by-default', affinity(test_pid) == 0x11)
        if args.setup:
            expected = str(package / 'native-messaging' / 'org.keepassxc.keepassxc_browser.json')
            for index, (key_path, _, _) in enumerate(registry_before):
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                    value, _ = winreg.QueryValueEx(key, '')
                record(('chrome' if index == 0 else 'edge') + '-setup-registers-current-package', value == expected)
            record('chinese-setup-alias-identical', (package / 'Setup.cmd').read_bytes() == (package / '首次配置.cmd').read_bytes())
        first_pid = test_pid
        test_pid = launch('Launch.cmd')
        record('repeat-uses-same-instance', test_pid == first_pid)
        hwnd = windows(test_pid)[0][0]
        user.ShowWindow(hwnd, 6)  # Minimize this empty test window only.
        test_pid = launch('启动软件.cmd')
        record('minimized-instance-restored', shown(test_pid))
        hwnd = windows(test_pid)[0][0]
        user.SetWindowPos(hwnd, None, 30000, 30000, 800, 600, 0x14)
        record('offscreen-window-reproduced', not on_screen(hwnd))
        test_pid = launch('Launch.cmd')
        record('offscreen-window-restored', shown(test_pid))
        missing = output / 'intentionally-missing.exe'
        failure = subprocess.Popen(command('Launch.cmd', ['-KeePassExecutable', str(missing)]),
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(2)
        waiting = failure.poll() is None
        stdout, _ = failure.communicate(b'\r\n', timeout=5)
        record('missing-file-nonzero-exit', failure.returncode != 0)
        record('failure-keeps-error-visible', waiting and b'Launch failed.' in stdout)
        record('failure-does-not-close-existing-test-desktop', bind_pid() == test_pid and shown(test_pid))

        # Recreate the original SW_HIDE bug, then upgrade without killing the app.
        for hwnd, *_ in windows(test_pid):
            user.PostMessageW(hwnd, 0x0010, 0, 0)
        deadline = time.monotonic() + 3
        while lock.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        record('normal-test-instance-closed', not lock.exists())
        test_pid = None
        legacy_env = env.copy()
        windows_root = os.environ['SystemRoot']
        legacy_env.update(PATH=str(package) + ';' + windows_root + '\\System32;' + windows_root,
                          QT_PLUGIN_PATH=str(package), QT_QPA_PLATFORM_PLUGIN_PATH=str(package / 'platforms'))
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = 0
        legacy = subprocess.Popen([str(package / 'TheyKnowYourPasswords.exe'), '--config', str(settings / 'settings.ini'),
            '--localconfig', str(settings / 'local.ini')], env=legacy_env, startupinfo=startup,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        test_pid = legacy.pid
        deadline = time.monotonic() + 3
        while not windows(test_pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        record('legacy-hidden-instance-reproduced', bool(windows(test_pid)) and
               not any(visible for _, visible, *_ in windows(test_pid)) and bind_pid() == legacy.pid)
        test_pid = launch('Launch.cmd')
        record('legacy-instance-recovered-without-restart', test_pid == legacy.pid and shown(test_pid))
        protected_pid = test_pid
        test_pid = launch('Launch-Remote.cmd')
        record('remote-request-normally-restarts-protected-instance', test_pid != protected_pid)
        record('remote-window-allows-capture-without-taking-screenshots', affinity(test_pid) == 0)
        remote_pid = test_pid
        test_pid = launch('远程启动.cmd')
        record('remote-repeat-reuses-instance', test_pid == remote_pid and affinity(test_pid) == 0)
        if args.setup:
            test_pid = launch('Setup-Remote.cmd')
            record('remote-setup-reuses-capture-enabled-instance', test_pid == remote_pid and affinity(test_pid) == 0)
        record('remote-setup-alias-identical', (package / 'Setup-Remote.cmd').read_bytes() == (package / '远程首次配置.cmd').read_bytes())
        for hwnd, *_ in windows(test_pid):
            user.PostMessageW(hwnd, 0x0010, 0, 0)
        deadline = time.monotonic() + 3
        while lock.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        record('remote-test-instance-closed-normally', not lock.exists())
        test_pid = launch('Launch.cmd')
        record('local-startup-restores-capture-protection', affinity(test_pid) == 0x11)
        report['complete'] = True
    finally:
        if test_pid is None and lock.exists():
            test_pid = bind_pid()
        if test_pid is not None and lock.exists() and bind_pid() == test_pid:
            for hwnd, *_ in windows(test_pid):
                user.PostMessageW(hwnd, 0x0010, 0, 0)
            deadline = time.monotonic() + 3
            while lock.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            report['test_instance_closed'] = not lock.exists()
        for key_path, value, kind in registry_before:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                if value is None:
                    winreg.DeleteValue(key, '')
                else:
                    winreg.SetValueEx(key, '', 0, kind, value)
        if registry_before:
            report['registration_restored'] = True
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
