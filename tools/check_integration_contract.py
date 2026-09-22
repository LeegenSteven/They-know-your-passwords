#!/usr/bin/env python3
"""Static cross-component contract checks; never reads password datasets."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def main() -> int:
    action = read("keepassxc-develop/src/browser/BrowserAction.cpp")
    service = read("keepassxc-develop/src/riskassess/RiskAssessmentService.cpp")
    extension = read("keepassxc-browser/keepassxc-browser/background/keepass.js")
    event = read("keepassxc-browser/keepassxc-browser/background/event.js")
    popup = read("keepassxc-browser/keepassxc-browser/popups/popup.html")
    installer = read("keepassxc-develop/src/browser/NativeMessageInstaller.cpp")
    manifest = json.loads(read("keepassxc-browser/dist/manifest_chromium.json"))

    for protocol_action in ("assess-password", "recommend-password"):
        assert protocol_action in action
        assert protocol_action in extension
    assert "STALE_INPUT_REVISION" in read("keepassxc-develop/src/browser/BrowserService.cpp")
    assert "STALE_VAULT_REVISION" in service
    assert 'result["level"] = "UNKNOWN"' in service
    assert 'result["calibrated"] = false' in service
    assert "EXACT_REUSE" in service
    assert "sender?.url?.startsWith(extensionOrigin)" in event
    assert "risk-confirm-success" in popup
    assert "我确认网站已成功修改口令" in popup
    assert "pending.inputRevision !== inputRevision" in extension
    assert "args: [ riskInputRevision ]" in read(
        "keepassxc-browser/keepassxc-browser/popups/popup.js"
    )

    extension_id = "ijlckofhohjbbifcfhpiglkmfndaaeol"
    assert manifest["name"] == "They Know Your Passwords"
    assert manifest.get("key")
    assert extension_id in installer
    assert "allowed_origins" not in json.dumps(manifest).lower()

    # The pending candidate is tab-memory state. It must not be written via
    # browser.storage by the risk workflow.
    risk_block = extension[extension.index("keepass.stageRiskCandidate"):extension.index("keepass.associate")]
    assert "browser.storage" not in risk_block
    print("integration-contract=PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
