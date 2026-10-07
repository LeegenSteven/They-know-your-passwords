'use strict';

let reloadCount = 0;
let riskInputRevision = Date.now();

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
    let hasCandidate = false;
    const updateSave = () => {
        saveButton.disabled = !hasCandidate || !confirmCheckbox.checked || !$('#risk-acknowledge').checked;
    };
    const options = () => ({
        account: $('#risk-account').value, entryUuid: $('#risk-account-select').value,
        constraints: {
            minLength: Number($('#risk-min').value), maxLength: Number($('#risk-max').value),
            length: Number($('#risk-length').value), lower: $('#risk-lower').checked,
            upper: $('#risk-upper').checked, digits: $('#risk-digits').checked,
            symbols: $('#risk-symbols').checked, forbidden: $('#risk-forbidden').value,
            budgetMs: Number($('#risk-budget').value)
        }
    });
    const showRiskResult = (response) => {
        const messages = [];
        const trawling = response?.trawling ?? response;
        const psm = trawling?.psm;
        if (trawling?.status === 'OK' && psm) {
            messages.push(psm.band
                ? 'PSM 第 ' + psm.band + '/6 档，估计区间、待验证'
                : '参考表截断；估计猜测次数下界 ≥ ' + Number(psm.lower_bound).toExponential(3) + '，待验证');
            messages.push('RankGuess 估计猜测次数：' + Number(trawling.native?.guess_number).toExponential(3));
        } else {
            messages.push('通用强度未知：' + (trawling?.error_code ?? trawling?.status ?? 'UNAVAILABLE'));
        }
        const reuse = response?.reuse;
        if (reuse) {
            const native = reuse.native;
            messages.push(reuse.status !== 'OK' ? 'PARD 重用未知：' + (reuse.error_code ?? reuse.status)
                : native?.exact_match ? 'PARD：精确复用，高风险'
                : native?.best_rank ? 'PARD：搜索命中，排名 ' + native.best_rank + '；风险待验证'
                : 'PARD：搜索未命中，风险未知');
            if (native) {
                messages.push('搜索预算：beam=' + native.beam_width + '，top-k=' + native.top_k
                    + '；历史使用 ' + (reuse.historyUsed ?? native.usable_sources));
            }
        }
        if (response?.error_code && !messages.includes(response.error_code)) {
            messages.push('状态：' + response.error_code);
        }
        if (response?.candidateToken) {
            messages.push('候选已准备。网站操作成功后，勾选两项确认再保存。');
        }
        resultElement.style.whiteSpace = 'pre-line';
        resultElement.textContent = messages.join('\n');
    };
    const call = async (action, args = []) => {
        try { return await browser.runtime.sendMessage({ action, args }); }
        catch (_err) { return { status: 'UNAVAILABLE', error_code: 'REQUEST_FAILED' }; }
    };
    const invalidate = () => {
        riskInputRevision++;
        hasCandidate = false;
        confirmCheckbox.checked = false;
        $('#risk-acknowledge').checked = false;
        updateSave();
        resultElement.textContent = '输入或目标已变化，请重新评估。';
        call('risk_cancel_candidate', [ riskInputRevision ]);
    };
    for (const id of ['risk-candidate', 'risk-context', 'risk-account', 'risk-account-select',
        'risk-min', 'risk-max', 'risk-length', 'risk-forbidden', 'risk-lower', 'risk-upper',
        'risk-digits', 'risk-symbols', 'risk-budget']) {
        $( '#' + id ).addEventListener(id === 'risk-candidate' || id === 'risk-account' ? 'input' : 'change', invalidate);
    }
    $('#risk-account-select').addEventListener('change', async () => {
        await call('page_set_login_id', $('#risk-account-select').value);
    });
    const fill = async () => {
        const index = $('#risk-target').value;
        const result = await call('risk_stage_candidate',
            [ candidateInput.value, riskInputRevision, index === '' ? undefined : Number(index) ]);
        if (result.status !== 'OK') {
            resultElement.textContent += '\n填充未完成：' + result.error_code + '。请在“目标输入框”选择新口令框。';
        }
    };
    $('#risk-fill-button').addEventListener('click', fill);
    const evaluate = async (action) => {
        invalidate();
        const revision = riskInputRevision;
        resultElement.textContent = action === 'recommend_password' ? '正在生成与复检（冷启动最多 180 秒）…' : '正在评估…';
        const args = action === 'recommend_password'
            ? [ $('#risk-context').value, revision, options() ]
            : [ candidateInput.value, $('#risk-context').value, revision, options() ];
        const response = await call(action, args);
        if (revision !== riskInputRevision) { return; }
        showRiskResult(response);
        if (response?.candidateToken) {
            hasCandidate = true;
            candidateInput.value = response.candidate;
            await fill();
        }
        updateSave();
    };
    $('#risk-assess-button').addEventListener('click', () => evaluate('assess_password'));
    $('#risk-recommend-button').addEventListener('click', () => evaluate('recommend_password'));
    $('#risk-prepare-button').addEventListener('click', () => evaluate('risk_prepare_candidate'));
    confirmCheckbox.addEventListener('change', updateSave);
    $('#risk-acknowledge').addEventListener('change', updateSave);
    $('#risk-cancel-button').addEventListener('click', invalidate);
    saveButton.addEventListener('click', async () => {
        if (saveButton.disabled) { return; }
        saveButton.disabled = true;
        const response = await call('risk_confirm_save', [ riskInputRevision, true, true ]);
        if (response?.status === 'OK' && response.saved) {
            resultElement.textContent = response.created ? '新条目已创建，数据库已保存。' : '原目标条目已更新，数据库已保存。';
            candidateInput.value = '';
            hasCandidate = false;
            confirmCheckbox.checked = false;
        } else {
            resultElement.textContent = '未保存：' + (response?.error_code ?? 'UNAVAILABLE');
        }
        updateSave();
    });
    const context = await call('risk_get_context');
    for (const account of context.accounts ?? []) {
        const option = document.createElement('option');
        option.value = account.uuid;
        option.textContent = account.login || '（无账号名称）';
        $('#risk-account-select').append(option);
    }
    $('#risk-account-select').value = context.selected ?? '';
    if (!(context.accounts?.length)) { $('#risk-context').value = 'new'; }
    const tab = await getCurrentTab();
    try {
        const fields = await browser.tabs.sendMessage(tab.id, { action: 'risk_target_fields' });
        for (const field of fields ?? []) {
            const option = document.createElement('option');
            option.value = field.index;
            option.textContent = field.label;
            $('#risk-target').append(option);
        }
    } catch (_err) { /* The pending candidate can still be reviewed without page access. */ }
    const pending = await call('risk_get_pending');
    if (pending?.status === 'OK') {
        candidateInput.value = pending.candidate;
        riskInputRevision = pending.inputRevision;
        $('#risk-context').value = pending.context;
        $('#risk-account').value = pending.account;
        $('#risk-account-select').value = pending.entryUuid;
        hasCandidate = true;
        showRiskResult({ ...pending.result, candidateToken: pending.candidateToken });
    }
    updateSave();

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
