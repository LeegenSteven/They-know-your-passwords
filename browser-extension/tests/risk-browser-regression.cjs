'use strict';
// Real installed Chromium browsers, isolated profiles, production background and
// content scripts. Native/algorithm calls and unrelated UI helpers are test stubs.
// No personal profiles, registry changes, native host, real passwords, screenshots,
// videos or traces are used. Only booleans/counts/status codes leave the fixture.
const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const crypto = require('node:crypto');
const assert = require('node:assert/strict');

const browserName = process.argv[2] ?? 'chrome';
if (!['chrome', 'edge'].includes(browserName)) { throw new Error('unsupported-test-browser'); }
const executablePath = browserName === 'edge'
    ? 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
    : 'C:/Program Files/Google/Chrome/Application/chrome.exe';
const sourceRoot = path.resolve(__dirname, '../extension');
const runRoot = path.resolve(__dirname, '../../../p1-browser', browserName + '-' + crypto.randomUUID());
const extensionRoot = path.join(runRoot, 'extension');
const report = { browser: browserName, checks: [], native: 'stubbed-and-connectNative-denied', algorithm: 'stubbed',
    browserApis: 'actual-tabs-webNavigation-sendMessage', screenshots: false, trace: false, video: false, complete: false };
let context;
let server;
let stage = 'setup';
function check(name, passed, extra = {}) {
    report.checks.push({ name, passed, ...extra });
    console.log(JSON.stringify({ browser: browserName, name, passed, ...extra }));
    assert.equal(passed, true, name);
}

async function setupExtension() {
    await fs.mkdir(extensionRoot, { recursive: true });
    for (const file of ['common/browser-polyfill.min.js', 'background/tabs.js', 'background/keepass.js',
        'content/they-know-your-passwords-browser.js']) {
        await fs.mkdir(path.dirname(path.join(extensionRoot, file)), { recursive: true });
        await fs.copyFile(path.join(sourceRoot, file), path.join(extensionRoot, file));
    }
    await fs.writeFile(path.join(extensionRoot, 'deny-native.js'), `
globalThis.nativeConnectAttempts = 0;
Object.defineProperty(chrome.runtime, 'connectNative', { configurable: false, writable: false,
 value: () => { globalThis.nativeConnectAttempts++; throw new Error('native-messaging-forbidden-in-regression'); } });
Object.defineProperty(chrome.runtime, 'sendNativeMessage', { configurable: false, writable: false,
 value: () => { globalThis.nativeConnectAttempts++; throw new Error('native-messaging-forbidden-in-regression'); } });
`);
    await fs.writeFile(path.join(extensionRoot, 'content-prelude.js'), `
globalThis.DatabaseState = {DISCONNECTED:0,LOCKED:1,UNLOCKED:2};
globalThis.logDebug = () => {}; globalThis.logError = () => {};
globalThis.kpxcUserAutocomplete = {closeList() {}};
`);
    await fs.writeFile(path.join(extensionRoot, 'content-postlude.js'), `
// The actual listener and DOM fill branch are unchanged. Other product startup
// paths are outside this focused integration fixture.
kpxc.siteIgnored = async () => false;
`);
    await fs.writeFile(path.join(extensionRoot, 'worker.js'), `
importScripts('deny-native.js', 'common/browser-polyfill.min.js');
globalThis.logDebug=()=>{};globalThis.logError=()=>{};
globalThis.CreationError={GENERAL:0}; globalThis.EXTENSION_NAME='Isolated risk regression';
globalThis.tr = key => key;
globalThis.browserAction={showDefault(){}};
globalThis.credentials={clearSubmittedCredentials:async()=>{}};
globalThis.page={settings:{clearCredentialsTimeout:0},setFillAttributeContextMenuItemVisible(){},
 clearLogins:async id=>{const s=tabs.getTabFromId(id);if(s){s.credentials=[];s.loginList=[];}},clearCredentials(){}};
globalThis.getCurrentTab=async()=> (await browser.tabs.query({active:true,currentWindow:true}))[0];
importScripts('background/tabs.js','background/keepass.js');
keepass.databaseHash='synthetic-database';keepass.isConnected=true;keepass.isDatabaseClosed=false;
keepass.isAssociated=()=>true;keepass.testAssociation=async()=>true;
const nativeTokens=new Map();const nativeCalls=[];const targets=[];let nextRace;
globalThis.keepassClient={getRequestId:()=>crypto.randomUUID(),getNonce:()=>crypto.randomUUID(),
 sendMessage:async(action,tab,data)=>{
  nativeCalls.push({action,hasToken:!!data.candidateToken,tabId:tab.id});
  if(action==='recommend-password'||action==='prepare-risk-candidate'){
   const candidate=Array.from(crypto.getRandomValues(new Uint8Array(18)),x=>x.toString(16).padStart(2,'0')).join('');
   const candidateToken=crypto.randomUUID();nativeTokens.set(candidateToken,{tabId:tab.id,candidate});
   return {status:'OK',candidate,candidateToken};
  }
  if(action==='cancel-risk-candidate'){
   if(!data.candidateToken){nativeTokens.clear();return {status:'OK'};}
   nativeTokens.delete(data.candidateToken);return {status:'OK'};
  }
  if(action==='confirm-risk-candidate'){
   if(!nativeTokens.has(data.candidateToken))return {status:'UNAVAILABLE',error_code:'NO_PENDING_CANDIDATE'};
   nativeTokens.delete(data.candidateToken);return {status:'OK',saved:true};
  }
  return {status:'OK'};
 }};
const originalSend=browser.tabs.sendMessage.bind(browser.tabs);
browser.tabs.sendMessage=async(id,message,target)=>{
 if(['risk_document_context','fill_risk_candidate'].includes(message.action)){
  targets.push({action:message.action,frameId:target?.frameId,documentPinned:typeof target?.documentId==='string'});
 }
 if(message.action==='fill_risk_candidate'&&nextRace){
  const race=nextRace;nextRace=undefined;const before=await browser.webNavigation.getFrame({tabId:id,frameId:0});
  await browser.tabs.update(id,{url:race});
  const until=Date.now()+10000;let changed=false;
  while(Date.now()<until){
   const frame=await browser.webNavigation.getFrame({tabId:id,frameId:0}).catch(()=>undefined);
   if(frame&&frame.documentId!==before.documentId){changed=true;break;}
   await new Promise(resolve=>setTimeout(resolve,20));
  }
  if(!changed)throw new Error('test-navigation-timeout');
 }
 return originalSend(id,message,target);
};
browser.tabs.onUpdated.addListener(tabs.onUpdated);
browser.tabs.onRemoved.addListener(tabs.onRemoved);
globalThis.__riskHarness={
 async add(id){if(!tabs.getTabFromId(id))await tabs.createTabEntry(id);},
 async recommend(id,revision){const tab=await browser.tabs.get(id);return this.safe(await keepass.recommendPassword(tab,['new',revision,{account:'synthetic-account'}]));},
 async stage(id,revision){return this.safe(await keepass.stageRiskCandidate({id},[undefined,revision]));},
 async confirm(id,revision){return this.safe(await keepass.confirmRiskCandidate({id},[revision,true,true]));},
 async cancel(id,revision){return this.safe(await keepass.cancelRiskCandidate({id},[revision]));},
 safe(r){return {status:r?.status,error_code:r?.error_code,filledCount:r?.filledCount,saved:r?.saved};},
 race(url){nextRace=url;},
 snapshot(id){return {pending:!!tabs.getTabFromId(id)?.riskPending,tokens:nativeTokens.size,
  cancels:nativeCalls.filter(x=>x.action==='cancel-risk-candidate').length,
  emptyCancels:nativeCalls.filter(x=>x.action==='cancel-risk-candidate'&&!x.hasToken).length,
  confirms:nativeCalls.filter(x=>x.action==='confirm-risk-candidate').length,
  nativeConnectAttempts:globalThis.nativeConnectAttempts,
  frameZero:targets.every(x=>x.frameId===0),documentPinned:targets.every(x=>x.documentPinned),messages:targets.length};}
};
`);
    await fs.writeFile(path.join(extensionRoot, 'manifest.json'), JSON.stringify({
        manifest_version: 3, name: 'Isolated production risk context regression', version: '1.0',
        permissions: ['tabs', 'webNavigation', 'storage'], host_permissions: ['http://127.0.0.1/*', 'http://localhost/*'],
        background: { service_worker: 'worker.js' }, content_scripts: [{
            matches: ['http://127.0.0.1/*', 'http://localhost/*'], all_frames: true, run_at: 'document_start',
            js: ['deny-native.js', 'common/browser-polyfill.min.js', 'content-prelude.js',
                'content/they-know-your-passwords-browser.js', 'content-postlude.js']
        }]
    }));
}

async function main() {
    await setupExtension();
    server = http.createServer((request, response) => {
        response.writeHead(200, { 'Content-Type': 'text/html;charset=utf-8', 'Cache-Control': 'no-store' });
        response.end('<!doctype html><title>Isolated risk regression</title>' +
            '<input id="current" type="password" autocomplete="current-password">' +
            '<input id="new" type="password" autocomplete="new-password">' +
            '<input id="confirm" type="password" autocomplete="new-password">' +
            (request.url === '/frame' ? '' : '<iframe src="/frame"></iframe>') +
            '<script>window.inputEvents=0;window.changeEvents=0;document.addEventListener("input",()=>window.inputEvents++);' +
            'document.addEventListener("change",()=>window.changeEvents++);</script>');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    const port = server.address().port;
    const origin = 'http://127.0.0.1:' + port;
    // localhost is an independent origin; it resolves only to this loopback server.
    const other = 'http://localhost:' + port;
    stage = 'launch';
    context = await chromium.launchPersistentContext(path.join(runRoot, 'profile'), {
        executablePath, headless: false, ignoreDefaultArgs: ['--disable-extensions'],
        args: ['--enable-unsafe-extension-debugging', '--no-first-run', '--no-default-browser-check'],
        viewport: { width: 900, height: 700 }
    });
    const session = await context.browser().newBrowserCDPSession();
    const loaded = await session.send('Extensions.loadUnpacked', { path: extensionRoot });
    let worker = context.serviceWorkers().find(item => item.url().includes(loaded.id));
    if (!worker) { worker = await context.waitForEvent('serviceworker', { timeout: 15000 }); }
    const run = (method, ...args) => worker.evaluate(async ({ method, args }) => __riskHarness[method](...args), { method, args });
    const tabFor = page => worker.evaluate(async url => (await browser.tabs.query({})).find(t => t.url === url)?.id, page.url());
    const open = async suffix => {
        const page = await context.newPage();await page.goto(origin + suffix);await page.bringToFront();
        const id = await tabFor(page);await run('add', id);return { page, id };
    };
    const fields = page => page.evaluate(() => ({ empty: ['current','new','confirm'].every(id=>document.getElementById(id).value===''),
        newEqual:document.getElementById('new').value.length>0&&document.getElementById('new').value===document.getElementById('confirm').value,
        currentEmpty:document.getElementById('current').value==='',inputEvents:window.inputEvents,changeEvents:window.changeEvents }));
    const noPending = async id => {
        const until=Date.now()+5000;while(Date.now()<until){if(!(await run('snapshot',id)).pending)return true;await new Promise(r=>setTimeout(r,20));}return false;
    };
    stage = 'normal-fill';
    const a = await open('/normal');
    check('production-recommendation-path', (await run('recommend', a.id, 1)).status === 'OK');
    const normal = await run('stage', a.id, 1);
    const normalFields = await fields(a.page);
    const iframeEmpty = await a.page.frames()[1].evaluate(()=>[...document.querySelectorAll('input')].every(input=>input.value===''));
    check('one-fill-writes-only-new-and-confirm', normal.status==='OK'&&normal.filledCount===2&&normalFields.newEqual&&normalFields.currentEmpty&&normalFields.inputEvents===2&&normalFields.changeEvents===2);
    check('actual-frame-target-is-top-document', iframeEmpty&&(await run('snapshot',a.id)).frameZero);
    const pinned = await run('snapshot',a.id);
    check('actual-webNavigation-documentId-targeted', pinned.documentPinned&&pinned.messages>=3);
    await run('cancel',a.id,2);
    await a.page.reload();
    stage='cross-origin-navigation';
    check('cross-origin-candidate-prepared',(await run('recommend',a.id,3)).status==='OK');
    await a.page.goto(other+'/cross');await a.page.bringToFront();
    check('cross-origin-navigation-revokes-pending',await noPending(a.id));
    check('cross-origin-document-cannot-fill',(await run('stage',a.id,3)).status!=='OK'&&(await fields(a.page)).empty);
    stage='same-origin-document';
    await a.page.goto(origin+'/same');await a.page.bringToFront();
    check('same-origin-candidate-prepared',(await run('recommend',a.id,4)).status==='OK');
    await a.page.reload();
    check('same-origin-new-document-rejects-fill',(await run('stage',a.id,4)).status!=='OK'&&(await fields(a.page)).empty);
    check('same-origin-navigation-retains-confirmation',(await run('snapshot',a.id)).pending);
    check('same-origin-website-success-can-confirm',(await run('confirm',a.id,4)).status==='OK'&&!(await run('snapshot',a.id)).pending);
    for(const [label,url] of [['same-origin',origin+'/race-same'],['cross-origin',other+'/race-cross']]){
        stage='delivery-race-'+label;await a.page.goto(origin+'/race-start-'+label);await a.page.bringToFront();
        check(label+'-race-prepared',(await run('recommend',a.id,5)).status==='OK');
        await run('race',url);const raced=await run('stage',a.id,5);
        await a.page.waitForURL(url);await a.page.waitForLoadState('load');
        check(label+'-navigation-between-check-and-delivery-blocked',raced.status!=='OK'&&(await fields(a.page)).empty,{errorCode:raced.error_code});
        await run('cancel',a.id,6);
    }
    stage='tab-isolation';
    const b=await open('/tab-b');check('tab-b-candidate-prepared',(await run('recommend',b.id,7)).status==='OK');
    const before=await run('snapshot',b.id);
    await a.page.goto(origin+'/empty-tab-a');await a.page.bringToFront();
    await run('cancel',a.id,8);await a.page.close();await b.page.bringToFront();
    const after=await run('snapshot',b.id);
    check('empty-tab-a-cancel-navigation-close-do-not-revoke-b',after.pending&&after.tokens===1&&after.cancels===before.cancels&&after.emptyCancels===0);
    check('tab-b-still-fills-and-confirms',(await run('stage',b.id,7)).status==='OK'&&(await fields(b.page)).newEqual&&(await run('confirm',b.id,7)).status==='OK');
    const final=await run('snapshot',b.id);
    check('no-native-host-call-and-no-empty-cancel',final.nativeConnectAttempts===0&&final.emptyCancels===0&&final.tokens===0);
    report.browserVersion=context.browser().version();report.complete=true;
}

(async()=>{
 try{await main();}catch(error){report.failedStage=stage;report.errorName=error.name;console.error(JSON.stringify({browser:browserName,failedStage:stage,errorName:error.name,diagnostic:error.message.split('\n')[0]}));process.exitCode=1;}
 finally{if(context)await context.close();if(server)await new Promise(resolve=>server.close(resolve));await fs.mkdir(runRoot,{recursive:true});await fs.writeFile(path.join(runRoot,'report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify({browser:browserName,complete:report.complete,checks:report.checks.length,report:path.join(runRoot,'report.json')}));}
})();
