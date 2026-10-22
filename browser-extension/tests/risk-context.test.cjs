'use strict';

// These tests execute the production background and content scripts. All
// candidates are random, memory-only fixtures; assertions expose only status,
// booleans and counts, never a candidate or an opaque native token.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

const EXTENSION_ID = 'risk-context-test-extension';
const source = (name) => fs.readFileSync(path.join(__dirname, '../extension', name), 'utf8');
const syntheticCandidate = () => crypto.randomBytes(18).toString('base64');
const deferred = () => {
    let resolve;
    const promise = new Promise((done) => { resolve = done; });
    return { promise, resolve };
};

function contentFixture(url, { subframe = false } = {}) {
    let listener;
    let writes = 0;
    let events = 0;
    const field = {
        offsetParent: {}, disabled: false, readOnly: false, maxLength: -1,
        autocomplete: 'new-password', name: 'new', id: 'new', placeholder: '',
        labels: [], getAttribute: () => '',
        set value(_value) { writes++; },
        dispatchEvent() { events++; }
    };
    const location = new URL(url);
    const document = {
        readyState: 'loading', documentElement: {}, location,
        addEventListener() {}, querySelectorAll: () => [ field ]
    };
    const window = { location, document };
    window.self = window;
    window.top = subframe ? { location } : window;
    const context = {
        console: { log() {}, error() {} }, URL, Date, setTimeout, clearTimeout,
        crypto: crypto.webcrypto, Event: class { constructor(type) { this.type = type; } },
        HTMLInputElement: class {}, DatabaseState: { DISCONNECTED: 0, LOCKED: 1, UNLOCKED: 2 },
        window, self: window, location, document,
        logDebug() {}, logError() {},
        kpxcUserAutocomplete: { closeList() {} },
        browser: { runtime: {
            id: EXTENSION_ID, sendMessage: async () => undefined,
            onMessage: { addListener: (callback) => { listener = callback; } }
        } }
    };
    vm.createContext(context);
    vm.runInContext(source('content/they-know-your-passwords-browser.js')
        + '\nglobalThis.subject = kpxc;', context);
    context.subject.siteIgnored = async () => false;
    return {
        subject: context.subject, documentId: crypto.randomUUID(),
        dispatch: (message, sender = { id: EXTENSION_ID }) => listener(message, sender),
        get writes() { return writes; }, get events() { return events; }
    };
}

async function backgroundFixture({ documentIds = false } = {}) {
    const openTabs = new Map();
    const documents = new Map();
    const allDocuments = [];
    const nativeCalls = [];
    const pageMessages = [];
    const blocked = new Map();
    let beforeFill;
    let associated = true;
    let tabSubject;
    const event = { addListener() {} };
    const browser = {
        runtime: { id: EXTENSION_ID, getURL: (value) => 'chrome-extension://' + EXTENSION_ID + '/' + value },
        storage: { local: { get: async () => ({ keyRing: {} }) } },
        tabs: {
            onActivated: event, onCreated: event, onRemoved: event, onUpdated: event,
            get: async (id) => {
                if (!openTabs.has(id)) { throw new Error('fixture tab closed'); }
                return { ...openTabs.get(id) };
            },
            sendMessage: async (id, message, options = {}) => {
                pageMessages.push({ action: message.action, options });
                if (message.action === 'fill_risk_candidate' && beforeFill) {
                    const callback = beforeFill;
                    beforeFill = undefined;
                    await callback();
                }
                const current = documents.get(id);
                if (!current || (options.documentId && options.documentId !== current.documentId)) {
                    throw new Error('fixture document replaced');
                }
                return current.dispatch(message);
            }
        }
    };
    if (documentIds) {
        browser.webNavigation = { getFrame: async ({ tabId }) => ({
            documentId: documents.get(tabId)?.documentId,
            url: openTabs.get(tabId)?.url, frameId: 0
        }) };
    }
    const context = {
        console: { log() {}, error() {} }, URL, Date, setTimeout, clearTimeout, structuredClone,
        CreationError: { GENERAL: 0, UPDATED: 1, CREATED: 2 },
        EXTENSION_NAME: 'Risk context fixture', tr: (key) => key,
        browser, browserAction: { showDefault() {} },
        credentials: { clearSubmittedCredentials: async () => {} },
        page: {
            setFillAttributeContextMenuItemVisible() {},
            clearAllLogins() {
                for (const state of tabSubject.tabList.values()) { state.credentials = []; state.loginList = []; }
            },
            clearLogins: async (id) => {
                const state = tabSubject.getTabFromId(id);
                if (state) { state.credentials = []; state.loginList = []; }
            }
        },
        getCurrentTab: async () => undefined,
        logDebug() {}, logError() {}
    };
    vm.createContext(context);
    vm.runInContext(source('background/tabs.js') + '\nglobalThis.tabSubject = tabs;', context);
    tabSubject = context.tabSubject;
    vm.runInContext(source('background/keepass.js') + '\nglobalThis.subject = keepass;', context);
    const subject = context.subject;
    subject.databaseHash = 'synthetic-database-identity';
    subject.isConnected = true;
    subject.isDatabaseClosed = false;
    subject.isKeePassXCAvailable = true;
    subject.isAssociated = () => associated;
    subject.testAssociation = async () => associated;
    subject.riskSend = async (tab, action, data) => {
        nativeCalls.push({ tabId: tab.id, action, data });
        const barrier = blocked.get(action);
        if (barrier) { barrier.started.resolve(); await barrier.release.promise; }
        if (action === 'recommend-password' || action === 'prepare-risk-candidate') {
            return { status: 'OK', candidate: syntheticCandidate(), candidateToken: crypto.randomUUID(),
                inputRevision: data.inputRevision };
        }
        return { status: 'OK', saved: action === 'confirm-risk-candidate', inputRevision: data.inputRevision };
    };
    const installDocument = (id, url) => {
        const value = contentFixture(url);
        documents.set(id, value);
        allDocuments.push(value);
        return value;
    };
    const fixture = {
        subject, tabs: tabSubject, nativeCalls, pageMessages, documents,
        tab: (id = 1) => ({ ...openTabs.get(id) }),
        state: (id = 1) => tabSubject.getTabFromId(id),
        get writes() { return allDocuments.reduce((sum, item) => sum + item.writes, 0); },
        async addTab(id, url) {
            openTabs.set(id, { id, active: true, status: 'complete', url });
            installDocument(id, url);
            await tabSubject.createTabEntry(id);
        },
        async navigate(id, url, changes = {}) {
            openTabs.set(id, { ...openTabs.get(id), url, status: 'loading' });
            installDocument(id, url);
            await tabSubject.onUpdated(id, { url, status: 'loading', ...changes }, fixture.tab(id));
        },
        replaceDocument(id = 1) { return installDocument(id, openTabs.get(id).url); },
        changeUrlSilently(id, url) { openTabs.set(id, { ...openTabs.get(id), url }); installDocument(id, url); },
        beforeFill(callback) { beforeFill = callback; },
        setAssociated(value) { associated = value; },
        loadClient() {
            let disconnect;
            browser.runtime.lastError = null;
            browser.runtime.connectNative = () => ({
                onMessage: { addListener() {} },
                onDisconnect: { addListener(callback) { disconnect = callback; } }
            });
            // UI updates and subsequent database probes are outside this
            // fixture. Risk-state invalidation remains the production code.
            subject.updatePopup = () => {};
            subject.updateDatabaseHashToContent = async () => {};
            subject.updateDatabase = async () => {};
            vm.runInContext(source('background/client.js')
                + '\nglobalThis.nativeClient = keepassClient;', context);
            context.nativeClient.nativeConnect();
            return {
                disconnect: () => disconnect(),
                notify: (action) => context.nativeClient.onNativeMessage({ action })
            };
        },
        block(action) {
            const barrier = { started: deferred(), release: deferred() };
            blocked.set(action, barrier);
            return {
                started: barrier.started.promise, release: () => barrier.release.resolve(),
                allowFollowingRequests: () => blocked.delete(action)
            };
        }
    };
    await fixture.addTab(1, 'https://accounts.example.test/change');
    return fixture;
}

const recommend = (fixture, revision = 1, id = 1) => fixture.subject.recommendPassword(fixture.tab(id),
    [ 'new', revision, { account: 'fictional-account' } ]);
const stage = (fixture, revision = 1, id = 1) => fixture.subject.stageRiskCandidate(fixture.tab(id),
    [ undefined, revision, 0 ]);
const countNative = (fixture, action) => fixture.nativeCalls.filter((item) => item.action === action).length;

test('a valid candidate is filled once in the authenticated top document', async () => {
    const fixture = await backgroundFixture();
    assert.equal((await recommend(fixture)).status, 'OK');
    assert.equal((await stage(fixture)).status, 'OK');
    assert.equal(fixture.writes, 1);
    assert.equal(fixture.documents.get(1).events, 2);
    const messages = fixture.pageMessages.filter((item) =>
        ['risk_document_context', 'fill_risk_candidate'].includes(item.action));
    assert.ok(messages.length >= 2);
    assert.equal(messages.every((item) => item.options.frameId === 0), true);
});

for (const failure of ['origin', 'document', 'sender', 'subframe']) {
    test('content rejects an invalid ' + failure + ' binding before any DOM write', async () => {
        const fixture = contentFixture('https://accounts.example.test/change', { subframe: failure === 'subframe' });
        const message = {
            action: 'fill_risk_candidate', candidate: syntheticCandidate(), targetIndex: 0,
            expectedOrigin: 'https://accounts.example.test', documentToken: fixture.subject.riskDocumentToken
        };
        if (failure === 'origin') { message.expectedOrigin = 'https://different.example.test'; }
        if (failure === 'document') { message.documentToken = crypto.randomUUID(); }
        const sender = { id: failure === 'sender' ? 'untrusted-extension' : EXTENSION_ID };
        const result = await fixture.dispatch(message, sender);
        assert.equal(result?.status === 'OK', false);
        assert.equal(fixture.writes, 0);
        assert.equal(fixture.events, 0);
    });
}

for (const failure of ['sender', 'subframe', 'non-http']) {
    test('document context is unavailable to an invalid ' + failure + ' source', async () => {
        const fixture = contentFixture(failure === 'non-http' ? 'about:blank'
            : 'https://accounts.example.test/change', { subframe: failure === 'subframe' });
        const result = await fixture.dispatch({ action: 'risk_document_context' },
            { id: failure === 'sender' ? 'untrusted-extension' : EXTENSION_ID });
        assert.equal(Boolean(result?.documentToken), false);
        assert.equal(fixture.writes, 0);
    });
}

test('cross-origin navigation revokes a staged candidate before any page fill', async () => {
    const fixture = await backgroundFixture();
    await recommend(fixture);
    await fixture.navigate(1, 'https://different.example.test/account');
    assert.equal((await stage(fixture)).status, 'UNAVAILABLE');
    assert.equal(fixture.writes, 0);
    assert.equal(Boolean(fixture.state().riskPending), false);
    assert.equal(countNative(fixture, 'cancel-risk-candidate'), 1);
});

test('a candidate cannot fill a replacement document on the same origin', async () => {
    const fixture = await backgroundFixture();
    await recommend(fixture);
    await fixture.navigate(1, 'https://accounts.example.test/success');
    assert.equal((await stage(fixture)).status, 'UNAVAILABLE');
    assert.equal(fixture.writes, 0);
});

for (const documentIds of [false, true]) {
    test('navigation between background validation and content delivery writes nothing (documentIds=' + documentIds + ')', async () => {
        const fixture = await backgroundFixture({ documentIds });
        await recommend(fixture);
        fixture.beforeFill(() => fixture.navigate(1, 'https://accounts.example.test/replaced'));
        assert.equal((await stage(fixture)).status, 'UNAVAILABLE');
        assert.equal(fixture.writes, 0);
        if (documentIds) {
            const message = fixture.pageMessages.find((item) => item.action === 'fill_risk_candidate');
            assert.equal(Boolean(message?.options.documentId), true);
        }
    });
}

for (const action of ['recommend-password', 'prepare-risk-candidate']) {
    for (const sameOrigin of [false, true]) {
        test(action + ' arriving after ' + (sameOrigin ? 'same-origin' : 'cross-origin') + ' navigation is discarded and its token revoked', async () => {
            const fixture = await backgroundFixture();
            const blocked = fixture.block(action);
            const request = action === 'recommend-password' ? recommend(fixture, 4)
                : fixture.subject.prepareRiskCandidate(fixture.tab(),
                    [ syntheticCandidate(), 'new', 4, { account: 'fictional-account' } ]);
            await blocked.started;
            await fixture.navigate(1, sameOrigin ? 'https://accounts.example.test/after'
                : 'https://different.example.test/after');
            blocked.release();
            assert.equal((await request).status, 'UNAVAILABLE');
            assert.equal(Boolean(fixture.state().riskPending), false);
            assert.equal(fixture.writes, 0);
            const cancellations = fixture.nativeCalls.filter((item) => item.action === 'cancel-risk-candidate');
            assert.equal(cancellations.length, 1);
            assert.equal(Boolean(cancellations[0]?.data.candidateToken), true);
            assert.equal(cancellations[0]?.data.origin === 'https://accounts.example.test', true);
        });
    }
}

for (const failure of ['locked', 'disconnected', 'association', 'database', 'expired', 'native-expiry', 'revision', 'account']) {
    test('staging rejects a ' + failure + ' candidate without sending a fill', async () => {
        const fixture = await backgroundFixture();
        fixture.state().credentials = [ { uuid: 'fictional-entry-a', login: 'fictional-account' } ];
        await fixture.subject.recommendPassword(fixture.tab(), [ 'change', 8 ]);
        if (failure === 'locked') { fixture.subject.isDatabaseClosed = true; }
        if (failure === 'disconnected') { fixture.subject.isConnected = false; }
        if (failure === 'association') { fixture.setAssociated(false); }
        if (failure === 'database') { fixture.subject.databaseHash = 'another-synthetic-database'; }
        if (failure === 'expired') { fixture.state().riskPending.createdAt -= 600001; }
        if (failure === 'native-expiry') { fixture.state().riskPending.expiresAt = Date.now() - 1; }
        if (failure === 'account') { fixture.state().loginId = 'fictional-entry-b'; }
        assert.equal((await stage(fixture, failure === 'revision' ? 9 : 8)).status, 'UNAVAILABLE');
        assert.equal(fixture.writes, 0);
        assert.equal(fixture.pageMessages.some((item) => item.action === 'fill_risk_candidate'), false);
    });
}

for (const failure of ['locked', 'disconnected', 'database', 'document-replaced']) {
    test('a generation reply after ' + failure + ' cannot become a pending candidate', async () => {
        const fixture = await backgroundFixture();
        const blocked = fixture.block('recommend-password');
        const request = recommend(fixture, 10);
        await blocked.started;
        if (failure === 'locked') { fixture.subject.isDatabaseClosed = true; }
        if (failure === 'disconnected') { fixture.subject.isConnected = false; }
        if (failure === 'database') { fixture.subject.databaseHash = 'another-synthetic-database'; }
        if (failure === 'document-replaced') { fixture.replaceDocument(); }
        blocked.release();
        assert.equal((await request).status, 'UNAVAILABLE');
        assert.equal(Boolean(fixture.state().riskPending), false);
        assert.equal(fixture.writes, 0);
        const cancellations = fixture.nativeCalls.filter((item) => item.action === 'cancel-risk-candidate');
        assert.equal(cancellations.length, 1);
        assert.equal(Boolean(cancellations[0].data.candidateToken), true);
    });
}

test('same-origin website success navigation retains confirmation of the original entry', async () => {
    const fixture = await backgroundFixture();
    fixture.state().credentials = [ { uuid: 'fictional-entry-a', login: 'fictional-account' } ];
    await fixture.subject.recommendPassword(fixture.tab(), [ 'change', 9 ]);
    await fixture.navigate(1, 'https://accounts.example.test/success');
    const result = await fixture.subject.confirmRiskCandidate(fixture.tab(), [ 9, true, true ]);
    assert.equal(result.status, 'OK');
    assert.equal(result.saved, true);
    const saves = fixture.nativeCalls.filter((item) => item.action === 'confirm-risk-candidate');
    assert.equal(saves.length, 1);
    assert.equal(saves[0].data.entryUuid === 'fictional-entry-a', true);
    assert.equal(Boolean(fixture.state().riskPending), false);
});

test('cancelling a page with no pending candidate never sends a broad native cancellation', async () => {
    const fixture = await backgroundFixture();
    await fixture.addTab(2, 'https://other.example.test/change');
    await recommend(fixture, 3, 2);
    const token = fixture.state(2).riskPending.candidateToken;
    const count = countNative(fixture, 'cancel-risk-candidate');
    await fixture.subject.cancelRiskCandidate(fixture.tab(1), [ 4 ]);
    assert.equal(countNative(fixture, 'cancel-risk-candidate'), count);
    assert.equal(fixture.state(2).riskPending?.candidateToken === token, true);
    assert.equal((await stage(fixture, 3, 2)).status, 'OK');
});

test('cancellation uses its own original binding and leaves a second page candidate intact', async () => {
    const fixture = await backgroundFixture();
    await fixture.addTab(2, 'https://other.example.test/change');
    await recommend(fixture, 2, 1);
    await recommend(fixture, 5, 2);
    const firstToken = fixture.state(1).riskPending.candidateToken;
    const secondToken = fixture.state(2).riskPending.candidateToken;
    fixture.changeUrlSilently(1, 'https://different.example.test/after');
    await fixture.subject.cancelRiskCandidate(fixture.tab(1), [ 6 ]);
    const cancellations = fixture.nativeCalls.filter((item) => item.action === 'cancel-risk-candidate');
    assert.equal(cancellations.length, 1);
    assert.equal(cancellations[0].data.candidateToken === firstToken, true);
    assert.equal(cancellations[0].data.origin === 'https://accounts.example.test', true);
    assert.equal(cancellations[0].data.pageId === '1', true);
    assert.equal(fixture.state(2).riskPending?.candidateToken === secondToken, true);
    assert.equal((await stage(fixture, 5, 2)).status, 'OK');
});

test('a late reply after cancellation is discarded even when the revision number is reused', async () => {
    const fixture = await backgroundFixture();
    const blocked = fixture.block('recommend-password');
    const request = recommend(fixture, 7);
    await blocked.started;
    await fixture.subject.cancelRiskCandidate(fixture.tab(), [ 7 ]);
    blocked.release();
    assert.equal((await request).status, 'UNAVAILABLE');
    assert.equal(Boolean(fixture.state().riskPending), false);
    assert.equal(fixture.writes, 0);
    const cancellations = fixture.nativeCalls.filter((item) => item.action === 'cancel-risk-candidate');
    assert.equal(cancellations.length, 1);
    assert.equal(Boolean(cancellations[0].data.candidateToken), true);
});

test('reclaiming an older reply cannot invalidate a newer pending candidate on the same page', async () => {
    const fixture = await backgroundFixture();
    const blocked = fixture.block('recommend-password');
    const olderRequest = recommend(fixture, 10);
    await blocked.started;
    blocked.allowFollowingRequests();
    assert.equal((await recommend(fixture, 11)).status, 'OK');
    const newerToken = fixture.state().riskPending.candidateToken;
    blocked.release();
    assert.equal((await olderRequest).status, 'UNAVAILABLE');
    const cancellations = fixture.nativeCalls.filter((item) => item.action === 'cancel-risk-candidate');
    assert.equal(cancellations.length, 1);
    assert.equal(Boolean(cancellations[0].data.candidateToken), true);
    assert.equal(cancellations[0].data.candidateToken !== newerToken, true);
    assert.equal(cancellations[0].data.invalidateRequests, false);
    assert.equal(fixture.state().riskPending?.candidateToken === newerToken, true);
    assert.equal(fixture.state().riskRevision, 11);
    assert.equal((await stage(fixture, 11)).status, 'OK');
    assert.equal(fixture.writes, 1);
});

test('the real native disconnect callback prevents an old candidate returning after same-database reconnection', async () => {
    const fixture = await backgroundFixture();
    const client = fixture.loadClient();
    await recommend(fixture, 12);
    const databaseHash = fixture.subject.databaseHash;
    const revision = fixture.state().riskRevision;
    const sequence = fixture.state().riskRequestSequence;
    client.disconnect();
    assert.equal(Boolean(fixture.state().riskPending), false);
    assert.equal(fixture.state().riskRevision > revision, true);
    assert.equal(fixture.state().riskRequestSequence > sequence, true);
    fixture.subject.databaseHash = databaseHash;
    fixture.subject.isConnected = true;
    fixture.subject.isDatabaseClosed = false;
    fixture.subject.isKeePassXCAvailable = true;
    fixture.setAssociated(true);
    assert.equal((await stage(fixture, 12)).status, 'UNAVAILABLE');
    assert.equal(fixture.writes, 0);
    assert.equal(fixture.pageMessages.some((item) => item.action === 'fill_risk_candidate'), false);
});

test('an in-flight generation cannot survive disconnect and reconnect to the same database', async () => {
    const fixture = await backgroundFixture();
    const client = fixture.loadClient();
    const databaseHash = fixture.subject.databaseHash;
    const blocked = fixture.block('recommend-password');
    const request = recommend(fixture, 14);
    await blocked.started;
    client.disconnect();
    fixture.subject.databaseHash = databaseHash;
    fixture.subject.isConnected = true;
    fixture.subject.isDatabaseClosed = false;
    fixture.subject.isKeePassXCAvailable = true;
    fixture.setAssociated(true);
    blocked.release();
    assert.equal((await request).status, 'UNAVAILABLE');
    assert.equal(Boolean(fixture.state().riskPending), false);
    assert.equal((await stage(fixture, 14)).status, 'UNAVAILABLE');
    assert.equal(fixture.writes, 0);
});

for (const action of ['database-locked', 'database-unlocked']) {
    test('the real ' + action + ' notification invalidates pending candidates and in-flight sequences', async () => {
        const fixture = await backgroundFixture();
        const client = fixture.loadClient();
        await recommend(fixture, 13);
        const revision = fixture.state().riskRevision;
        const sequence = fixture.state().riskRequestSequence;
        client.notify(action);
        assert.equal(Boolean(fixture.state().riskPending), false);
        assert.equal(fixture.state().riskRevision > revision, true);
        assert.equal(fixture.state().riskRequestSequence > sequence, true);
        assert.equal((await stage(fixture, 13)).status, 'UNAVAILABLE');
        assert.equal(fixture.writes, 0);
    });
}

test('closing a tab revokes its pending candidate and removes its state', async () => {
    const fixture = await backgroundFixture();
    await recommend(fixture);
    await fixture.tabs.onRemoved(1, {});
    assert.equal(Boolean(fixture.state()), false);
    assert.equal(countNative(fixture, 'cancel-risk-candidate'), 1);
});

test('non-HTTP navigation revokes the candidate without attempting page fill', async () => {
    const fixture = await backgroundFixture();
    await recommend(fixture);
    await fixture.navigate(1, 'about:blank');
    assert.equal((await stage(fixture)).status, 'UNAVAILABLE');
    assert.equal(fixture.writes, 0);
    assert.equal(Boolean(fixture.state().riskPending), false);
    assert.equal(countNative(fixture, 'cancel-risk-candidate'), 1);
});
