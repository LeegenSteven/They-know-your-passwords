#!/usr/bin/env python3
"""Static cross-component contract checks; never reads password datasets."""

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_NAME = "They know your passwords"
PACKAGE_NAME = "they-know-your-passwords"


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    tracked_paths = subprocess.check_output(
        ["git", "ls-files"], cwd=ROOT, text=True, encoding="utf-8"
    ).splitlines()
    assert all("keepassxc" not in path.lower() for path in tracked_paths)
    assert (ROOT / "desktop-app").is_dir()
    assert (ROOT / "browser-extension" / "extension").is_dir()

    action = read("desktop-app/src/browser/BrowserAction.cpp")
    service = read("desktop-app/src/riskassess/RiskAssessmentService.cpp")
    extension = read("browser-extension/extension/background/keepass.js")
    event = read("browser-extension/extension/background/event.js")
    popup = read("browser-extension/extension/popups/popup.html")
    installer = read("desktop-app/src/browser/NativeMessageInstaller.cpp")
    manifest_paths = (
        "browser-extension/dist/manifest_chromium.json",
        "browser-extension/dist/manifest_firefox.json",
        "browser-extension/extension/manifest.json",
    )
    manifests = [json.loads(read(path)) for path in manifest_paths]
    chromium_manifest = manifests[0]

    for protocol_action in ("assess-password", "recommend-password"):
        assert protocol_action in action
        assert protocol_action in extension
    assert "STALE_INPUT_REVISION" in read("desktop-app/src/browser/BrowserService.cpp")
    assert "STALE_VAULT_REVISION" in service
    assert 'result["level"] = "UNKNOWN"' in service
    assert 'result["calibrated"] = false' in service
    assert "EXACT_REUSE" in service
    assert "sender?.url?.startsWith(extensionOrigin)" in event
    assert "risk-confirm-success" in popup
    assert "我确认网站已成功修改口令" in popup
    assert "pending.inputRevision !== inputRevision" in extension
    assert "args: [ riskInputRevision ]" in read(
        "browser-extension/extension/popups/popup.js"
    )

    extension_id = "ijlckofhohjbbifcfhpiglkmfndaaeol"
    assert all(item["name"] == PRODUCT_NAME for item in manifests)
    for manifest in manifests:
        action_key = "action" if "action" in manifest else "browser_action"
        assert manifest[action_key]["default_title"] == PRODUCT_NAME
    assert chromium_manifest.get("key")
    assert extension_id in installer
    assert "allowed_origins" not in json.dumps(chromium_manifest).lower()

    # The pending candidate is tab-memory state. It must not be written via
    # browser.storage by the risk workflow.
    risk_block = extension[extension.index("keepass.stageRiskCandidate"):extension.index("keepass.associate")]
    assert "browser.storage" not in risk_block

    package = json.loads(read("browser-extension/package.json"))
    package_lock = json.loads(read("browser-extension/package-lock.json"))
    assert package["name"] == PACKAGE_NAME
    assert package_lock["name"] == PACKAGE_NAME
    assert package_lock["packages"][""]["name"] == PACKAGE_NAME
    assert f"{PACKAGE_NAME}_${{version}}_${{browser}}.zip" in read("browser-extension/build.js")

    extension_root = ROOT / "browser-extension" / "extension"
    for path in extension_root.rglob("*"):
        if path.suffix.lower() not in {".css", ".html", ".js", ".json"}:
            continue
        content = path.read_text(encoding="utf-8")
        assert "They Know Your Passwords" not in content, path
        assert "KeePassXC-Browser" not in content, path
    print("integration-contract=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
