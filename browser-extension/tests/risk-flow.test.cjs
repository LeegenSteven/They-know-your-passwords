'use strict';
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const crypto = require('node:crypto');

function fixture() {
    const state = { credentials: [], riskRevision: 0 };
    const tab = { id: 1, active: true, url: 'https://example.test/account' };
    const context = {
        console: { log() {}, error() {} }, URL, Date, setTimeout, clearTimeout,
        CreationError: { GENERAL: 0, UPDATED: 1, CREATED: 2 },
        tabs: { getTabFromId: () => state, updateTabValues: (_, value) => Object.assign(state, value) },
        page: { clearLogins: async () => {} }, browserAction: {},
        browser: {
            runtime: { id: 'test-extension', getURL: (value) => 'chrome-extension://test-extension/' + value },
            storage: { local: { get: async () => ({ keyRing: {} }) } },
            tabs: { get: async () => tab, sendMessage: async () => ({ status: 'OK' }) }
        }
    };
    vm.createContext(context);
    const source = fs.readFileSync(path.join(__dirname, '../extension/background/keepass.js'), 'utf8');
    vm.runInContext(source + '\nglobalThis.subject = keepass;', context);
    const subject = context.subject;
    subject.databaseHash = 'synthetic-database-id';
    subject.isAssociated = () => true;
    subject.testAssociation = async () => true;
    subject.riskSend = async (_, action, data) => {
        if (action === 'recommend-password' || action === 'prepare-risk-candidate') {
            return { status: 'OK', candidate: crypto.randomBytes(15).toString('base64'),
                candidateToken: 'opaque-fixture-token', inputRevision: data.inputRevision };
        }
        return { status: 'OK', saved: true, created: data.context === 'new' };
    };
    return { state, tab, subject };
}

test('first candidate can create without an existing entry', async () => {
    const { state, tab, subject } = fixture();
    const prepared = await subject.recommendPassword(tab, [ 'new', 4, { account: 'fictional' } ]);
    assert.equal(prepared.status, 'OK');
    assert.equal(state.riskPending.entryUuid, '');
    const result = await subject.confirmRiskCandidate(tab, [ 4, true, true ]);
    assert.equal(result.saved, true);
    assert.equal(result.created, true);
    assert.equal(state.riskPending, undefined);
});

test('switching accounts rejects confirmation without saving another entry', async () => {
    const { state, tab, subject } = fixture();
    state.credentials = [ { uuid: 'entry-a', login: 'fictional-a' }, { uuid: 'entry-b', login: 'fictional-b' } ];
    state.loginId = 'entry-a';
    await subject.recommendPassword(tab, [ 'change', 7 ]);
    state.loginId = 'entry-b';
    let saves = 0;
    subject.riskSend = async () => { saves++; return { status: 'OK', saved: true }; };
    const result = await subject.confirmRiskCandidate(tab, [ 7, true, true ]);
    assert.equal(result.status, 'UNAVAILABLE');
    assert.equal(saves, 0);
});

test('same-origin website success navigation keeps original binding', async () => {
    const { state, tab, subject } = fixture();
    state.credentials = [ { uuid: 'entry-a', login: 'fictional-a' } ];
    await subject.recommendPassword(tab, [ 'change', 8 ]);
    state.credentials = [];
    tab.url = 'https://example.test/success';
    const result = await subject.confirmRiskCandidate(tab, [ 8, true, true ]);
    assert.equal(result.saved, true);
});

test('expired, changed-input and cross-origin candidates are rejected', async () => {
    for (const mode of ['expired', 'revision', 'origin']) {
        const { state, tab, subject } = fixture();
        await subject.recommendPassword(tab, [ 'new', 5, { account: 'fictional' } ]);
        if (mode === 'expired') { state.riskPending.createdAt -= 600001; }
        if (mode === 'origin') { tab.url = 'https://different.test/'; }
        const result = await subject.confirmRiskCandidate(tab, [ mode === 'revision' ? 6 : 5, true, true ]);
        assert.equal(result.status, 'UNAVAILABLE');
    }
});

test('old generation reply is discarded after input cancellation', async () => {
    const { state, tab, subject } = fixture();
    let resolve;
    const original = subject.riskSend;
    subject.riskSend = (_, action, data) => action === 'recommend-password'
        ? new Promise((done) => { resolve = () => original(tab, action, data).then(done); })
        : original(tab, action, data);
    const generation = subject.recommendPassword(tab, [ 'new', 10, { account: 'fictional' } ]);
    await new Promise((done) => setImmediate(done));
    await subject.cancelRiskCandidate(tab, [ 11 ]);
    resolve();
    assert.equal((await generation).error_code, 'STALE_INPUT_REVISION');
    assert.equal(state.riskPending, undefined);
});
