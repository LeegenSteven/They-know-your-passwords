'use strict';
// Actual installed browser + release desktop + stdio models. No trace, video or screenshots.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const { existsSync } = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const http = require('node:http');
const { spawn } = require('node:child_process');

const root = path.resolve(__dirname, '../..');
const packageRoot = path.resolve(process.env.TKYP_TEST_PACKAGE ?? path.join(root, 'out/TheyKnowYourPasswords'));
const name = process.argv[2] ?? 'chrome';
const executablePath = name === 'edge'
    ? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    : 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const fixtureExe = 'D:/tmp/TheyKnowYourPasswordsBuild/tests/riskfixture.exe';
const extensionId = 'ijlckofhohjbbifcfhpiglkmfndaaeol';
const acceptanceRoot = path.join(root, process.env.TKYP_TEST_REPORTS ?? 'out/acceptance', name);
const env = {
    ...process.env, PATH: packageRoot + ';C:/Windows/System32;C:/Windows',
    // A separate named pipe leaves an already running personal desktop untouched.
    USERNAME: 'tkyp-acceptance-' + name,
    QT_PLUGIN_PATH: packageRoot, QT_QPA_PLATFORM_PLUGIN_PATH: path.join(packageRoot, 'platforms'),
    KEEPASSXC_RISK_PYTHON: path.join(packageRoot, existsSync(path.join(packageRoot, 'runtime/python.exe'))
        ? 'runtime/python.exe' : 'runtime/Scripts/python.exe'),
    KEEPASSXC_RISK_SERVICE: path.join(packageRoot, 'algo-service/server.py'),
    KEEPASSXC_RISK_CONFIG: path.join(packageRoot, 'algo-service/config.toml')
};
delete env.DEBUG;
delete env.PWDEBUG;
const report = { browser: name, checks: [], complete: false, trace: false, screenshots: false };
let stage = 'initialization';
let desktop;
let context;
let server;
let master;
let associationKey;
let vaultPath;

async function inspectPopup(session) {
    for (let attempt = 0; attempt < 20; attempt++) {
        const targets = await session.send('Target.getTargets', { filter: [{}] });
        const possible = targets.targetInfos.filter((item) => item.type === 'other' || item.url.includes('/popups/popup.html'));
        for (const target of possible) {
            const attached = await session.send('Target.attachToTarget', { targetId: target.targetId, flatten: false });
            let serial = 0;
            const requests = new Map();
            const listener = (event) => {
                if (event.sessionId !== attached.sessionId) { return; }
                const response = JSON.parse(event.message);
                if (requests.has(response.id)) {
                    const done = requests.get(response.id);
                    requests.delete(response.id);
                    response.error ? done.reject(new Error('popup-protocol-error')) : done.resolve(response.result);
                }
            };
            session.on('Target.receivedMessageFromTarget', listener);
            const evaluate = async (expression) => {
                const id = ++serial;
                const reply = new Promise((resolve, reject) => {
                    const timer = setTimeout(() => {
                        requests.delete(id);
                        reject(new Error('popup-target-unresponsive'));
                    }, 5000);
                    requests.set(id, {
                        resolve: (result) => { clearTimeout(timer); resolve(result); },
                        reject: (error) => { clearTimeout(timer); reject(error); }
                    });
                });
                await session.send('Target.sendMessageToTarget', {
                    sessionId: attached.sessionId, message: JSON.stringify({ id, method: 'Runtime.evaluate',
                        params: { expression, awaitPromise: true, returnByValue: true } })
                });
                const result = await reply;
                if (result.exceptionDetails) { throw new Error('popup-script-error'); }
                return result.result?.value;
            };
            try {
                const href = await evaluate('location.href');
                if (href?.endsWith('/popups/popup.html')) {
                    return {
                        evaluate,
                        select: (id, value) => evaluate('(()=>{const e=document.getElementById(' + JSON.stringify(id) +
                            ');e.value=' + JSON.stringify(value) + ';e.dispatchEvent(new Event("change",{bubbles:true}));})()'),
                        input: (id, value) => evaluate('(()=>{const e=document.getElementById(' + JSON.stringify(id) +
                            ');e.value=' + JSON.stringify(value) + ';e.dispatchEvent(new Event("input",{bubbles:true}));})()'),
                        click: (id) => evaluate('document.getElementById(' + JSON.stringify(id) + ').click()'),
                        wait: async (expression, timeout = 15000) => {
                            const until = Date.now() + timeout;
                            while (Date.now() < until) {
                                if (await evaluate(expression)) { return; }
                                await new Promise((resolve) => setTimeout(resolve, 100));
                            }
                            const error = new Error('popup-wait-timeout');
                            error.name = 'TimeoutError';
                            throw error;
                        }
                    };
                }
            } catch (_error) { /* Inspect returned popup targets until its document loads. */ }
            session.off('Target.receivedMessageFromTarget', listener);
            await session.send('Target.detachFromTarget', { sessionId: attached.sessionId });
        }
        await new Promise((resolve) => setTimeout(resolve, 100));
    }
    throw new Error('popup-target-not-found');
}

function record(check, passed, extra = {}) {
    report.checks.push({ check, passed, ...extra });
    console.log(JSON.stringify({ browser: name, check, passed, ...extra }));
    if (!passed) { const error = new Error('acceptance-check-failed'); error.name = 'AcceptanceFailure'; throw error; }
}
async function fixture(operation, extras = {}) {
    const child = spawn(fixtureExe, [], { env, windowsHide: true, stdio: ['pipe', 'pipe', 'ignore'] });
    let output = '';
    child.stdout.on('data', (data) => { output += data.toString(); });
    child.stdin.end(JSON.stringify({ operation, master, associationKey, path: vaultPath, ...extras }) + '\n');
    const code = await new Promise((resolve, reject) => { child.on('exit', resolve); child.on('error', reject); });
    if (code !== 0) { throw new Error('fixture-failed'); }
    return JSON.parse(output);
}
async function startDesktop() {
    desktop = spawn(path.join(packageRoot, 'TheyKnowYourPasswords.exe'),
        ['--config', path.join(acceptanceRoot, 'settings.ini'), '--localconfig', path.join(acceptanceRoot, 'local.ini'),
            '--pw-stdin', vaultPath],
        { env, windowsHide: true, stdio: ['pipe', 'ignore', 'ignore'] });
    desktop.stdin.end(master + '\n');
    await new Promise((resolve) => setTimeout(resolve, 2500));
    record('release-desktop-running', desktop.exitCode === null);
}
async function stopDesktop() {
    if (desktop && desktop.exitCode === null) {
        desktop.kill();
        await new Promise((resolve) => desktop.once('exit', resolve));
    }
}

async function main() {
    await fs.mkdir(acceptanceRoot, { recursive: true });
    env.TEMP = path.join(root, '.tools/tmp');
    env.TMP = env.TEMP;
    master = crypto.randomBytes(24).toString('base64');
    associationKey = crypto.randomBytes(32).toString('base64');
    vaultPath = path.join(acceptanceRoot, 'isolated.kdbx');
    await fs.writeFile(path.join(acceptanceRoot, 'settings.ini'),
        '[General]\nSingleInstance=false\nAutoSaveAfterEveryChange=false\nAutoSaveOnExit=false\n' +
        '[Browser]\nEnabled=true\nUpdateBinaryPath=false\nAlwaysAllowAccess=true\nNoMigrationPrompt=true\n' +
        '[GUI]\nLanguage=en_US\n');
    await fs.writeFile(path.join(acceptanceRoot, 'local.ini'), '[Browser]\nRiskAssessmentEnabled=true\n');
    const created = await fixture('create');
    record('independent-encrypted-vault-created', created.entryCount === 0);
    const accounts = new Map();
    server = http.createServer(async (request, response) => {
        if (request.method === 'POST') {
            let body = '';
            for await (const chunk of request) { body += chunk.toString(); }
            const values = new URLSearchParams(body);
            const account = values.get('account');
            const password = values.get('password');
            const success = request.url === '/login'
                ? accounts.get(account) === password
                : password && password === values.get('confirm');
            if (success && request.url !== '/login') { accounts.set(account, password); }
            response.writeHead(success ? 200 : 400, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' });
            response.end(JSON.stringify({ success: Boolean(success) }));
            return;
        }
        response.writeHead(200, { 'Content-Type': 'text/html;charset=utf-8', 'Cache-Control': 'no-store' });
        const loginPage = request.url === '/login';
        response.end('<!doctype html><html><head><title>Isolated acceptance site</title></head><body>' +
            '<form id="form"><label>账号<input id="account" name="account" autocomplete="username"></label>' +
            '<label>口令<input id="password" name="password" type="password" autocomplete="' + (loginPage ? 'current-password' : 'new-password') + '" maxlength="20"></label>' +
            (loginPage ? '' : '<label>确认<input id="confirm" name="confirm" type="password" autocomplete="new-password"></label>') +
            '<button id="submit" type="submit">网站确认成功</button></form><div id="status"></div>' +
            '<script>document.getElementById("form").onsubmit=async e=>{e.preventDefault();const r=await fetch("/register",{method:"POST",body:new URLSearchParams(new FormData(e.target))});' +
            'document.getElementById("status").textContent=r.ok?"WEBSITE_SUCCESS":"WEBSITE_REJECTED";}</script></body></html>');
    });
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    const origin = 'http://127.0.0.1:' + server.address().port;
    await startDesktop();
    stage = 'browser-launch';
    context = await chromium.launchPersistentContext(path.join(acceptanceRoot, 'browser-profile'), {
        executablePath, headless: false, env, ignoreDefaultArgs: ['--disable-extensions'],
        args: ['--enable-unsafe-extension-debugging', '--no-first-run'],
        viewport: { width: 1280, height: 900 }
    });
    context.setDefaultTimeout(15000);
    const session = await context.browser().newBrowserCDPSession();
    const loaded = await session.send('Extensions.loadUnpacked', { path: path.join(packageRoot, 'extension-chromium') });
    record('fixed-extension-loaded', loaded.id === extensionId);
    stage = 'worker-startup';
    let worker = context.serviceWorkers().find((item) => item.url().includes(extensionId));
    if (!worker) { worker = await context.waitForEvent('serviceworker', { timeout: 20000 }); }
    await worker.evaluate(({ hash, key }) => {
        keepass.keyRing = { [hash]: { id: 'acceptance-local', key, hash, created: Date.now(), lastUsed: Date.now() } };
        return browser.storage.local.set({ keyRing: keepass.keyRing });
    }, { hash: created.databaseHash, key: associationKey });
    const site = context.pages()[0] ?? await context.newPage();
    await site.goto(origin);
    await site.locator('#account').fill('fictional-' + name);
    await site.bringToFront();
    stage = 'native-handshake';
    const associated = await worker.evaluate(async () => {
        await keepass.reconnect(null, 5000);
        return { associated: keepass.isAssociated(), closed: keepass.isDatabaseClosed,
            available: keepass.isKeePassXCAvailable };
    });
    record('native-associated-unlocked', associated.associated && !associated.closed && associated.available);
    stage = 'page-input-feedback';
    await site.locator('#password').fill(crypto.randomBytes(15).toString('base64'));
    await site.waitForFunction(() => document.querySelector('.tkyp-strength')?.textContent.includes('待验证'), null, { timeout: 185000 });
    record('real-page-debounced-generic-feedback', true);
    stage = 'assessment';
    const assessed = await worker.evaluate(async (candidate) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        const result = await keepass.assessGenericPassword(tab, [ candidate, 1 ]);
        return { status: result.status, error_code: result.error_code, psm: result.psm };
    }, crypto.randomBytes(15).toString('base64'));
    record('actual-native-generic-assessment', assessed.status === 'OK', { status: assessed.status, errorCode: assessed.error_code });
    stage = 'native-generation';
    const recommended = await worker.evaluate(async (account) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        const result = await Promise.race([
            keepass.recommendPassword(tab, [ 'new', Date.now(), { account } ]),
            new Promise((resolve) => setTimeout(() => resolve({ status: 'DIAGNOSTIC_TIMEOUT',
                actions: messageBuffer.buffer.map((item) => item.request.action) }), 15000))
        ]);
        const summary = { status: result.status, errorCode: result.error_code, tokenPrepared: !!result.candidateToken };
        if (result.status !== 'DIAGNOSTIC_TIMEOUT') { await keepass.cancelRiskCandidate(tab, [ Date.now() ]); }
        if (result.actions) { summary.pendingActions = result.actions; }
        return summary;
    }, 'fictional-' + name);
    record('actual-native-generation', recommended.status === 'OK' && recommended.tokenPrepared, recommended);
    stage = 'popup';
    const targets = await session.send('Target.getTargets', { filter: [{}] });
    const target = targets.targetInfos.find((item) => item.type === 'tab' && item.url.startsWith(origin));
    await session.send('Extensions.triggerAction', { id: extensionId, targetId: target.targetId });
    const popup = await inspectPopup(session);
    record('actual-extension-popup-open', true);
    await popup.wait('!!document.getElementById("risk-recommend-button")');
    await popup.select('risk-context', 'new');
    await popup.input('risk-account', 'fictional-' + name);
    await popup.select('risk-budget', '30000');
    stage = 'generation';
    const started = Date.now();
    await popup.click('risk-recommend-button');
    await popup.wait('!document.getElementById("risk-result").textContent.startsWith("正在")', 185000);
    const generated = await popup.evaluate('document.getElementById("risk-result").textContent.includes("候选已准备")');
    const errorCode = await popup.evaluate('(document.getElementById("risk-result").textContent.match(/状态：([A-Z_]+)/)||[])[1]');
    record('real-model-generation-rechecked', generated, { elapsedMs: Date.now() - started, errorCode });
    const pending = await worker.evaluate(() => keepass.getPendingRiskCandidate({ id: tabs.currentTabId }));
    record('host-token-prepared', pending.status === 'OK' && Boolean(pending.candidateToken));
    const filled = await site.evaluate(() => {
        const first = document.getElementById('password').value;
        return first.length === 20 && first === document.getElementById('confirm').value;
    });
    record('new-and-confirm-fields-filled', filled);
    const before = await fixture('inspect');
    record('not-saved-before-website-success', before.entryCount === 0);
    // Closing the popup leaves the pending candidate in host/tab memory.
    await popup.evaluate('window.close()').catch(() => {});
    await site.bringToFront();
    await site.locator('#submit').click();
    await site.waitForFunction(() => document.getElementById('status').textContent === 'WEBSITE_SUCCESS');
    record('website-success', true);
    await new Promise((resolve) => setTimeout(resolve, 600));
    stage = 'popup-confirm-open';
    const saveTargets = await session.send('Target.getTargets', { filter: [{}] });
    const saveTarget = saveTargets.targetInfos.find((item) => item.type === 'tab' && item.url.startsWith(origin));
    await session.send('Extensions.triggerAction', { id: extensionId, targetId: saveTarget.targetId });
    stage = 'popup-confirm-inspect';
    const savePopup = await inspectPopup(session);
    stage = 'popup-confirm-wait';
    await savePopup.wait('document.getElementById("risk-result")?.textContent.includes("候选已准备")');
    await savePopup.evaluate('for(const id of ["risk-confirm-success","risk-acknowledge"]){const e=document.getElementById(id);e.checked=true;e.dispatchEvent(new Event("change"));}');
    await savePopup.click('risk-save-button');
    await savePopup.wait('document.getElementById("risk-result").textContent.includes("数据库已保存")');
    record('actual-popup-first-save', true);
    const persisted = await fixture('inspect', { account: 'fictional-' + name, expected: pending.candidate });
    record('kdbx-persisted', persisted.entryCount === 1 && persisted.expectedMatches);
    stage = 'restart-fill';
    await stopDesktop();
    await startDesktop();
    await worker.evaluate(async () => { await keepass.reconnect(null, 5000); });
    await site.goto(origin + '/login');
    await site.bringToFront();
    const entries = await worker.evaluate(async (url) => {
        const tab = await browser.tabs.query({ active: true, currentWindow: true });
        return keepass.retrieveCredentials(tab[0], [ url, url ]);
    }, origin);
    record('restart-retrieval', entries?.length === 1);
    await site.locator('#account').click();
    // Exercise the real content fill message, not a simulated DOM setter.
    const fillResult = await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        await page.retrieveCredentials(tab, [ tab.url, tab.url ]);
        return browser.tabs.sendMessage(tab.id, { action: 'fill_username_password' });
    });
    await site.waitForFunction(() => document.getElementById('password').value.length > 0);
    const authentication = await site.evaluate(async () => {
        const data = new URLSearchParams({ account: document.getElementById('account').value,
            password: document.getElementById('password').value });
        return (await fetch('/login', { method: 'POST', body: data })).ok;
    });
    record('return-visit-fill-authenticates', authentication);
    stage = 'two-account-regression';
    await site.goto(origin);
    await site.locator('#account').fill('fictional-second');
    const second = await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        const result = await keepass.recommendPassword(tab, ['new', Date.now(), { account: 'fictional-second', constraints: { budgetMs: 30000 } }]);
        if (result.status === 'OK') { await keepass.stageRiskCandidate(tab, ['', result.inputRevision]); }
        return result;
    });
    record('second-account-generation', second.status === 'OK' && !!second.candidateToken,
        { status: second.status, errorCode: second.error_code });
    await site.locator('#submit').click();
    await site.waitForFunction(() => document.getElementById('status').textContent === 'WEBSITE_SUCCESS');
    const secondSaved = await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        return keepass.confirmRiskCandidate(tab, [tabs.getTabFromId(tab.id).riskPending.inputRevision, true, true]);
    });
    record('second-account-persisted', secondSaved.status === 'OK' && secondSaved.created);
    const accountIds = await worker.evaluate(async (account) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        const credentials = await page.retrieveCredentials(tab, [tab.url, tab.url, true]);
        const first = credentials.find((item) => item.login === account);
        const second = credentials.find((item) => item.login === 'fictional-second');
        return { first: first?.uuid, second: second?.uuid, count: credentials.length };
    }, 'fictional-' + name);
    record('two-distinct-target-entries', accountIds.count === 2 && accountIds.first !== accountIds.second);
    const ordinary = await worker.evaluate(async (ids) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        await page.setLoginId(tab, ids.first);
        const result = await keepass.recommendPassword(tab, ['change', Date.now(), { entryUuid: ids.first }]);
        return { status: result.status, resultStatus: result.resultStatus, level: result.level,
            tokenPrepared: !!result.candidateToken, budgetMs: 3000 };
    }, accountIds);
    record('default-budget-safe-result-before-explicit-retry',
        (ordinary.status === 'TIMEOUT' && !ordinary.tokenPrepared && ordinary.level === 'UNKNOWN')
        || (ordinary.status === 'OK' && ordinary.resultStatus === 'REVIEW_REQUIRED'), ordinary);
    await new Promise((resolve) => setTimeout(resolve, 600));
    const switched = await worker.evaluate(async (ids) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        await page.setLoginId(tab, ids.first);
        const result = await keepass.recommendPassword(tab, ['change', Date.now(), { entryUuid: ids.first, constraints: { budgetMs: 30000 } }]);
        if (!result.candidateToken) { return { generated: false, errorCode: result.error_code }; }
        await page.setLoginId(tab, ids.second);
        const save = await keepass.confirmRiskCandidate(tab, [result.inputRevision, true, true]);
        return { generated: true, rejected: save.status !== 'OK', errorCode: save.error_code };
    }, accountIds);
    record('generated-then-account-switch-refuses-save', switched.generated && switched.rejected, switched);
    const unchanged = await fixture('inspect', { account: 'fictional-second', expected: second.candidate });
    record('switch-did-not-update-other-entry', unchanged.entryCount === 2 && unchanged.expectedMatches);
    stage = 'change-original-entry';
    await site.locator('#account').fill('fictional-' + name);
    await site.evaluate((value) => {
        const old = document.createElement('input');
        old.id = 'old-password'; old.name = 'currentPassword'; old.type = 'password';
        old.autocomplete = 'current-password'; old.value = value;
        document.getElementById('form').prepend(old);
    }, pending.candidate);
    const changed = await worker.evaluate(async (ids) => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        await page.setLoginId(tab, ids.first);
        const result = await keepass.recommendPassword(tab, ['change', Date.now(), { entryUuid: ids.first, constraints: { budgetMs: 30000 } }]);
        if (result.status === 'OK') {
            const fill = await keepass.stageRiskCandidate(tab, ['', result.inputRevision]);
            result.fillStatus = fill.status;
            result.fillError = fill.error_code;
        }
        return result;
    }, accountIds);
    record('change-rechecks-independent-pard', changed.status === 'OK' && changed.reuse?.status === 'OK'
        && changed.reuse.historyUsed >= 1 && !!changed.reuse.native.search_budget,
        { status: changed.status, historyUsed: changed.reuse?.historyUsed, errorCode: changed.error_code });
    record('change-candidate-filled', changed.fillStatus === 'OK', { status: changed.fillStatus, errorCode: changed.fillError });
    const oldPreserved = await site.evaluate((value) => document.getElementById('old-password').value === value, pending.candidate);
    record('change-fill-preserves-current-password-field', oldPreserved);
    const changeFields = await site.evaluate(() => ({ equal: document.getElementById('password').value === document.getElementById('confirm').value,
        length: document.getElementById('password').value.length }));
    record('change-page-field-values-agree', changeFields.equal && changeFields.length === 20, changeFields);
    await site.evaluate(() => { document.getElementById('status').textContent = ''; });
    await site.locator('#submit').click();
    await new Promise((resolve) => setTimeout(resolve, 500));
    const changeOutcome = await site.evaluate(() => ({
        websiteAccepted: document.getElementById('status').textContent === 'WEBSITE_SUCCESS',
        websiteRejected: document.getElementById('status').textContent === 'WEBSITE_REJECTED',
        equal: document.getElementById('password').value === document.getElementById('confirm').value,
        length: document.getElementById('password').value.length
    }));
    record('change-website-accepted', changeOutcome.websiteAccepted, changeOutcome);
    await site.waitForFunction(() => document.getElementById('status').textContent === 'WEBSITE_SUCCESS');
    const changedSaved = await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        return keepass.confirmRiskCandidate(tab, [tabs.getTabFromId(tab.id).riskPending.inputRevision, true, true]);
    });
    record('change-updates-only-original-entry', changedSaved.status === 'OK' && !changedSaved.created
        && changedSaved.entryUuid === accountIds.first);
    const changedDisk = await fixture('inspect', { account: 'fictional-' + name, expected: changed.candidate });
    const secondDisk = await fixture('inspect', { account: 'fictional-second', expected: second.candidate });
    record('changed-vault-persisted-other-account-intact', changedDisk.entryCount === 2
        && changedDisk.expectedMatches && secondDisk.expectedMatches);
    stage = 'lock';
    await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        await keepass.lockDatabase(tab);
    });
    const locked = await worker.evaluate(async () => {
        const tab = (await browser.tabs.query({ active: true, currentWindow: true }))[0];
        const result = await keepass.assessGenericPassword(tab, [''.padEnd(8, 'X'), Date.now()]);
        return { closed: keepass.isDatabaseClosed, refused: result.status !== 'OK' };
    });
    record('locked-database-refuses-risk', locked.closed && locked.refused);
    report.complete = true;
}

main().catch((error) => {
    report.failure = { stage, type: error.name ?? 'Error' };
    if (['popup-target-not-found', 'popup-script-error', 'popup-target-unresponsive', 'popup-protocol-error'].includes(error.message)) {
        report.failure.code = error.message;
    }
    if (stage === 'popup-confirm-open' && error.message.includes('Protocol error (Extensions.triggerAction)')) {
        report.failure.code = error.message.slice(error.message.indexOf('Protocol error (Extensions.triggerAction)'));
    }
    if (stage === 'popup-confirm-wait') { report.failure.code = error.message; }
    console.log(JSON.stringify({ browser: name, failedStage: stage, ...report.failure }));
    process.exitCode = 1;
}).finally(async () => {
    if (context) { await context.close().catch(() => {}); }
    await stopDesktop();
    if (server) { await new Promise((resolve) => server.close(resolve)); }
    await fs.writeFile(path.join(acceptanceRoot, 'report.json'), JSON.stringify(report, null, 2));
    master = undefined;
    associationKey = undefined;
});
