'use strict';

let reloadCount = 0;
let riskInputRevision = 0;

HTMLElement.prototype.show = function() {
    this.style.display = 'block';
};

HTMLElement.prototype.hide = function() {
    this.style.display = 'none';
};

function statusResponse(r) {
    $('#initial-state').hide();
    $('#error-encountered').hide();
    $('#need-reconfigure').hide();
    $('#not-configured').hide();
    $('#configured-and-associated').hide();
    $('#configured-not-associated').hide();
    $('#lock-database-button').hide();
    $('#risk-panel').hide();
    $('#getting-started-guide').hide();
    $('#database-not-opened').hide();

    if (!r.keePassXCAvailable) {
        $('#error-message').textContent = r.error;
        $('#error-encountered').show();

        if (r.showGettingStartedGuideAlert) {
            $('#getting-started-guide').show();
        }

        if (r.showTroubleshootingGuideAlert && reloadCount >= 2) {
            $('#troubleshooting-guide').show();
        } else {
            $('#troubleshooting-guide').hide();
        }
    } else if (r.keePassXCAvailable && r.databaseClosed) {
        $('#database-error-message').textContent = r.error;
        $('#database-not-opened').show();
    } else if (!r.configured) {
        $('#not-configured').show();
    } else if (r.encryptionKeyUnrecognized) {
        $('#need-reconfigure').show();
        $('#need-reconfigure-message').textContent = r.error;
    } else if (!r.associated) {
        $('#need-reconfigure').show();
        $('#need-reconfigure-message').textContent = r.error;
    } else if (r.error) {
        $('#error-encountered').show();
        $('#error-message').textContent = r.error;
    } else {
        $('#configured-and-associated').show();
        $('#associated-identifier').textContent = r.identifier;
        $('#lock-database-button').show();
        $('#risk-panel').show();

        if (r.usernameFieldDetected) {
            $('#username-field-detected').show();
        }

        if (r.iframeDetected) {
            $('#iframe-detected').show();
        }

        reloadCount = 0;
    }
}

const sendMessageToTab = async function(message) {
    const tab = await getCurrentTab();
    if (!tab) {
        return false; // Only the background devtools or a popup are opened
    }

    await browser.tabs.sendMessage(tab.id, {
        action: message
    });

    return true;
};

(async () => {
    await initColorTheme();

    $('#connect-button').addEventListener('click', async () => {
        await browser.runtime.sendMessage({
            action: 'associate'
        });

        // This does not work with Firefox because of https://bugzilla.mozilla.org/show_bug.cgi?id=1665380
        await sendMessageToTab('retrieve_credentials_forced');
        close();
    });

    $('#reconnect-button').addEventListener('click', async () => {
        await browser.runtime.sendMessage({
            action: 'associate'
        });
        close();
    });

    $('#reload-status-button').addEventListener('click', async () => {
        statusResponse(await browser.runtime.sendMessage({
            action: 'reconnect'
        }));

        // Shows the Troubleshooting Guide alert every third time Reload button is pressed when popup is open
        if (reloadCount > 2) {
            reloadCount = 0;
        }
        reloadCount++;
    });

    $('#reopen-database-button').addEventListener('click', async () => {
        statusResponse(await browser.runtime.sendMessage({
            action: 'get_status',
            args: [ false, true ] // Set forcePopup to true
        }));
        window.close();
    });

    $('#redetect-fields-button').addEventListener('click', async () => {
        const res = await sendMessageToTab('redetect_fields');
        if (!res) {
            return;
        }

        statusResponse(await browser.runtime.sendMessage({
            action: 'get_status'
        }));
    });

    $('#lock-database-button').addEventListener('click', async () => {
        statusResponse(await browser.runtime.sendMessage({
            action: 'lock_database'
        }));
    });

    $('#username-only-button').addEventListener('click', async () => {
        await sendMessageToTab('add_username_only_option');
        await sendMessageToTab('redetect_fields');
        $('#username-field-detected').hide();
    });

    $('#allow-iframe-button').addEventListener('click', async () => {
        await sendMessageToTab('add_allow_iframes_option');
        await sendMessageToTab('redetect_fields');
        $('#iframe-detected').hide();
    });

    const candidateInput = $('#risk-candidate');
    const resultElement = $('#risk-result');
    const confirmCheckbox = $('#risk-confirm-success');
    const saveButton = $('#risk-save-button');

    const showRiskResult = (response) => {
        resultElement.classList.remove('risk-high', 'risk-unknown');
        if (!response || response.status !== 'OK') {
            resultElement.classList.add('risk-unknown');
            resultElement.textContent = `暂无法评估：${response?.error_code ?? 'UNAVAILABLE'}`;
            return;
        }
        const level = response.level ?? 'UNKNOWN';
        resultElement.classList.add(level === 'HIGH' ? 'risk-high' : 'risk-unknown');
        if (level === 'HIGH' && response.reason === 'EXACT_REUSE') {
            resultElement.textContent = '高风险：与口令库中的历史口令完全相同。';
        } else if (!response.calibrated) {
            resultElement.textContent = '模型已返回原生指标；当前阈值未标定，暂不判定风险等级。';
        } else {
            resultElement.textContent = `风险等级：${level}`;
        }
    };

    const invalidateRiskInput = () => {
        riskInputRevision++;
        resultElement.textContent = '输入已变化，请重新评估。';
        confirmCheckbox.checked = false;
        saveButton.disabled = true;
    };
    candidateInput.addEventListener('input', invalidateRiskInput);
    $('#risk-context').addEventListener('change', invalidateRiskInput);

    $('#risk-assess-button').addEventListener('click', async () => {
        const revision = riskInputRevision;
        resultElement.textContent = '正在评估…';
        const response = await browser.runtime.sendMessage({
            action: 'assess_password',
            args: [ candidateInput.value, $('#risk-context').value, revision ]
        });
        if (revision !== riskInputRevision || response?.inputRevision !== revision) {
            return;
        }
        showRiskResult(response);
    });

    $('#risk-recommend-button').addEventListener('click', async () => {
        const revision = ++riskInputRevision;
        resultElement.textContent = '正在生成并复检…';
        confirmCheckbox.checked = false;
        saveButton.disabled = true;
        const response = await browser.runtime.sendMessage({
            action: 'recommend_password',
            args: [ $('#risk-context').value, revision ]
        });
        if (revision !== riskInputRevision || response?.inputRevision !== revision) {
            return;
        }
        if (response?.candidate) {
            candidateInput.value = response.candidate;
            const staged = await browser.runtime.sendMessage({
                action: 'risk_stage_candidate',
                args: [ response.candidate, revision ]
            });
            if (staged?.status !== 'OK') {
                showRiskResult(staged);
                return;
            }
            resultElement.classList.add('risk-unknown');
            resultElement.textContent = response.resultStatus === 'QUALIFIED'
                ? '候选口令已通过复检并填入页面。网站修改成功后再确认保存。'
                : '候选口令已填入页面；阈值未标定，请人工复核。网站修改成功后再确认保存。';
            confirmCheckbox.checked = false;
            saveButton.disabled = true;
        } else {
            showRiskResult(response);
        }
    });

    confirmCheckbox.addEventListener('change', () => {
        saveButton.disabled = !confirmCheckbox.checked;
    });

    saveButton.addEventListener('click', async () => {
        if (!confirmCheckbox.checked) {
            return;
        }
        const response = await browser.runtime.sendMessage({
            action: 'risk_confirm_save',
            args: [ riskInputRevision ]
        });
        if (response?.status === 'OK') {
            resultElement.textContent = 'KeePassXC 条目已更新。';
            candidateInput.value = '';
            confirmCheckbox.checked = false;
            saveButton.disabled = true;
        } else {
            resultElement.textContent = `未保存：${response?.error_code ?? 'UNAVAILABLE'}`;
        }
    });

    const pending = await browser.runtime.sendMessage({ action: 'risk_get_pending' });
    if (pending?.status === 'OK') {
        candidateInput.value = pending.candidate;
        riskInputRevision = pending.inputRevision;
        resultElement.textContent = '存在待确认候选。仅在网站已成功修改后确认保存。';
    }

    $('#getting-started-alert-close-button').addEventListener('click', async () => {
        await browser.runtime.sendMessage({
            action: 'hide_getting_started_guide_alert'
        });
    });

    $('#troubleshooting-guide-alert-close-button').addEventListener('click', async () => {
        await browser.runtime.sendMessage({
            action: 'hide_troubleshooting_guide_alert'
        });
    });

    statusResponse(await browser.runtime.sendMessage({
        action: 'get_status'
    }).catch((err) => {
        logError('Could not get status: ' + err);
    }));
})();
