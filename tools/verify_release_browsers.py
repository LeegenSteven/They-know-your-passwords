"""Isolated actual-browser release acceptance, with exact Native-host restore.

Only fictional test secrets exist in child memory/stdin. Public reports contain
booleans, counts, model/executable identities and elapsed times. No personal
vault/profile is opened or personal process terminated.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import winreg

import psutil


FIXED_ID = 'ijlckofhohjbbifcfhpiglkmfndaaeol'
KEYS = ['Software\\' + value + '\\NativeMessagingHosts\\org.keepassxc.keepassxc_browser'
        for value in ('Google\\Chrome', 'Microsoft\\Edge')]


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def snapshot_registry():
    result = []
    for path in KEYS:
        row = {'path': path, 'existed': False, 'hasDefault': False}
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                row['existed'] = True
                try:
                    row['value'], row['kind'] = winreg.QueryValueEx(key, '')
                    row['hasDefault'] = True
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            pass
        result.append(row)
    return result


def restore_registry(before):
    for row in before:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, row['path'], 0, winreg.KEY_SET_VALUE) as key:
            if row['hasDefault']:
                winreg.SetValueEx(key, '', 0, row['kind'], row['value'])
            else:
                try:
                    winreg.DeleteValue(key, '')
                except FileNotFoundError:
                    pass
        if not row['existed']:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, row['path']) as key:
                children, values, _ = winreg.QueryInfoKey(key)
            if children == 0 and values == 0:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, row['path'])
    return snapshot_registry() == before


def safe_child_cleanup(run_root, target):
    run_root = run_root.resolve()
    target = target.resolve()
    if target == run_root or run_root not in target.parents:
        raise RuntimeError('cleanup-outside-this-run')
    if target.is_dir():
        shutil.rmtree(target)
    elif target.exists():
        target.unlink()


def inspect_test_processes(pid, package):
    summary = {'packagedPythonSeen': False, 'packagedNativeProxySeen': False,
               'algorithmListeningSockets': 0}
    try:
        desktop = psutil.Process(pid)
        if Path(desktop.exe()).resolve() != package / 'TheyKnowYourPasswords.exe':
            return summary
        for child in desktop.children(recursive=True):
            try:
                exe = Path(child.exe()).resolve()
                if exe == package / 'runtime/python.exe':
                    summary['packagedPythonSeen'] = True
                    summary['algorithmListeningSockets'] += sum(
                        conn.status == psutil.CONN_LISTEN for conn in child.connections(kind='inet'))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        for child in psutil.process_iter(['name', 'exe']):
            if (child.info['name'] or '').casefold() == 'they-know-your-passwords-proxy.exe' and child.info['exe']:
                if Path(child.info['exe']).resolve() == package / 'they-know-your-passwords-proxy.exe':
                    summary['packagedNativeProxySeen'] = True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--repo', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--node', default='node')
    parser.add_argument('--version', default='v0.2.3')
    parser.add_argument('--extension-version', default='1.10.5')
    parser.add_argument('--fixture', help='Path to a freshly built desktop tests/riskfixture.exe')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    package = Path(args.package).resolve()
    repo = Path(args.repo).resolve()
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    script = repo / 'browser-extension/tests/real-browser-acceptance.cjs'
    manifest = json.loads((package / 'extension-chromium/manifest.json').read_text(encoding='utf-8'))
    if manifest['manifest_version'] != 3 or manifest['version'] != args.extension_version:
        raise RuntimeError('unexpected-release-extension')
    tracked = ['background/client.js', 'background/keepass.js', 'background/tabs.js',
               'background/background_service.js', 'content/they-know-your-passwords-browser.js']
    source_identity = []
    for relative in tracked:
        bundled = package / 'extension-chromium' / relative
        source = repo / 'browser-extension/extension' / relative
        same = bundled.read_bytes().replace(b'\r\n', b'\n') == source.read_bytes().replace(b'\r\n', b'\n')
        source_identity.append({'file': relative, 'sha256': sha(bundled), 'matchesSource': same})
        if not same:
            raise RuntimeError('release-script-source-mismatch')
    before = snapshot_registry()
    # The snapshot contains only original local manifest paths/types. It stays
    # private in this work directory and is not part of the public report.
    (output / 'registration-before.private.json').write_text(json.dumps(before, indent=2), encoding='utf-8')
    native = output / 'native-messaging'
    native.mkdir()
    native_manifest = native / 'org.keepassxc.keepassxc_browser.json'
    native_manifest.write_text(json.dumps({
        'name': 'org.keepassxc.keepassxc_browser', 'description': 'Isolated release acceptance',
        'path': str(package / 'they-know-your-passwords-proxy.exe'), 'type': 'stdio',
        'allowed_origins': ['chrome-extension://' + FIXED_ID + '/']
    }, indent=2), encoding='utf-8')
    report = {'schema': 1, 'version': args.version, 'complete': False,
              'registrationRestored': False, 'trace': False, 'screenshots': False,
              'sourceIdentity': source_identity, 'browsers': {}}
    try:
        for key_path in KEYS:
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, '', 0, winreg.REG_SZ, str(native_manifest))
        for browser in ('chrome', 'edge'):
            env = os.environ.copy()
            env['TKYP_TEST_PACKAGE'] = str(package)
            env['TKYP_TEST_REPORTS'] = os.path.relpath(output, repo)
            if args.fixture:
                env['TKYP_TEST_FIXTURE'] = str(Path(args.fixture).resolve())
            env.pop('DEBUG', None)
            env.pop('PWDEBUG', None)
            started = time.monotonic()
            child = subprocess.Popen([args.node, str(script), browser], cwd=repo, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     encoding='utf-8', creationflags=subprocess.CREATE_NO_WINDOW)
            desktop_pid = None
            process_identity = {'packagedPythonSeen': False, 'packagedNativeProxySeen': False,
                                'algorithmListeningSockets': 0}
            for line in child.stdout:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if row.get('check') == 'release-desktop-running':
                    desktop_pid = row.get('desktopPid')
                if desktop_pid and row.get('check') in ('actual-native-generic-assessment',
                                                       'change-rechecks-independent-pard'):
                    inspected = inspect_test_processes(desktop_pid, package)
                    for field in ('packagedPythonSeen', 'packagedNativeProxySeen'):
                        process_identity[field] |= inspected[field]
                    process_identity['algorithmListeningSockets'] += inspected['algorithmListeningSockets']
                # Only the harness's explicitly selected status fields leave memory.
                allowed = ('browser', 'check', 'passed', 'status', 'errorCode', 'failedStage', 'type', 'code',
                           'elapsedMs', 'historyUsed', 'generated', 'rejected', 'budgetMs')
                print(json.dumps({key: row[key] for key in allowed if key in row}), flush=True)
            code = child.wait(timeout=30)
            browser_report = json.loads((output / browser / 'report.json').read_text(encoding='utf-8'))
            browser_report['processIdentity'] = process_identity
            browser_report['elapsedSeconds'] = round(time.monotonic() - started, 2)
            browser_report['childExitCode'] = code
            browser_report['complete'] = (browser_report['complete'] and code == 0
                and process_identity['packagedPythonSeen'] and process_identity['packagedNativeProxySeen']
                and process_identity['algorithmListeningSockets'] == 0)
            report['browsers'][browser] = browser_report
            # Profiles, random-key encrypted fixture vault and private config are
            # useful only during this run; retain only sanitized JSON evidence.
            for target in (output / browser).iterdir():
                if target.name != 'report.json':
                    safe_child_cleanup(output, target)
            browser_report['isolatedStateCleaned'] = True
            (output / browser / 'report.json').write_text(json.dumps(browser_report, indent=2), encoding='utf-8')
            if not browser_report['complete']:
                raise RuntimeError('real-browser-acceptance-failed-' + browser)
        report['complete'] = True
    finally:
        report['registrationRestored'] = restore_registry(before)
        if not report['registrationRestored']:
            report['complete'] = False
        (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'summary': 'real-release-acceptance', 'complete': report['complete'],
                          'registrationRestored': report['registrationRestored'],
                          'browsers': {name: {'complete': value['complete'],
                              'checks': len(value['checks']),
                              'passed': sum(bool(row['passed']) for row in value['checks'])}
                              for name, value in report['browsers'].items()}}), flush=True)


if __name__ == '__main__':
    main()
