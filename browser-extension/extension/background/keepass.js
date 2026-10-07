'use strict';

const keepass = {};
keepass.associated = { 'value': false, 'hash': null };
keepass.featuresList = {
    downloadFaviconAfterSave: false,
    newTotp: false,
    passwordGenerator: false,
    passkeys: false,
    passkeysDefaultGroup: false,
    requiredKeePassXCVersionFound: false,
    webSocket: false,
    riskAssessment: true
};
keepass.cacheTimeout = 30 * 1000; // Milliseconds
keepass.clientID = '';
keepass.currentKeePassXC = '';
keepass.databaseHash = '';
keepass.isConnected = false;
keepass.isDatabaseClosed = true;
keepass.isEncryptionKeyUnrecognized = false;
keepass.isKeePassXCAvailable = false;
keepass.keyPair = { publicKey: null, secretKey: null };
keepass.latestVersionUrl = 'https://api.github.com/repos/keepassxreboot/keepassxc/releases/latest';
keepass.previousDatabaseHash = '';
keepass.reconnectLoop = null;
keepass.requiredKeePassXC = '2.6.0';
keepass.serverPublicKey = '';

const DEFAULT_FETCH_TIMEOUT = 5000; // ms
const MAX_RELATED_ORIGIN_LABELS = 60;

const kpActions = {
    SET_LOGIN: 'set-login',
    GET_LOGINS: 'get-logins',
    GENERATE_PASSWORD: 'generate-password',
    ASSOCIATE: 'associate',
    TEST_ASSOCIATE: 'test-associate',
    GET_DATABASE_HASH: 'get-databasehash',
    CHANGE_PUBLIC_KEYS: 'change-public-keys',
    LOCK_DATABASE: 'lock-database',
    DATABASE_LOCKED: 'database-locked',
    DATABASE_UNLOCKED: 'database-unlocked',
    GET_DATABASE_GROUPS: 'get-database-groups',
    CREATE_NEW_GROUP: 'create-new-group',
    GET_TOTP: 'get-totp',
    REQUEST_AUTOTYPE: 'request-autotype',
    PASSKEYS_REGISTER: 'passkeys-register',
    PASSKEYS_GET: 'passkeys-get',
    ASSESS_PASSWORD: 'assess-password',
    RECOMMEND_PASSWORD: 'recommend-password'
};

browser.storage.local.get({ 'latestKeePassXC': { 'version': '', 'lastChecked': null }, 'keyRing': {} }).then((item) => {
    keepass.latestKeePassXC = item.latestKeePassXC;
    keepass.keyRing = item.keyRing;
});

//--------------------------------------------------------------------------
// Commands
//--------------------------------------------------------------------------

keepass.addCredentials = async function(tab, args = []) {
    const [ username, password, url, group, groupUuid ] = args;
    return keepass.updateCredentials(tab, [ null, username, password, url, group, groupUuid ]);
};

keepass.updateCredentials = async function(tab, args = []) {
    const [ entryId, username, password ] = args;
    const revision = (tabs.getTabFromId(tab.id)?.riskRevision ?? 0) + 1;
    const response = await keepass.prepareRiskCandidate(tab,
        [ password, entryId ? 'change' : 'new', revision, { entryUuid: entryId ?? '', account: username } ]);
    return response?.candidateToken ? 'REVIEW_REQUIRED' : CreationError.GENERAL;
};

keepass.retrieveCredentials = async function(tab, args = []) {
    try {
        const [ url, submiturl, triggerUnlock = false, httpAuth = false ] = args;
        const taResponse = await keepass.testAssociation(tab, [ false, triggerUnlock ]);
        if (!taResponse) {
            browserAction.showDefault(tab);
            return [];
        }

        keepass.clearErrorMessage(tab);

        if (!keepass.isConnected) {
            return [];
        }

        let entries = [];
        const kpAction = kpActions.GET_LOGINS;
        const nonce = keepassClient.getNonce();
        const [ dbid ] = keepass.getCryptoKey();

        const messageData = {
            action: kpAction,
            id: dbid,
            url: url,
            keys: keepass.getCryptoKeys()
        };

        if (submiturl) {
            messageData.submitUrl = submiturl;
        }

        if (httpAuth) {
            messageData.httpAuth = 'true';
        }

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            entries = removeDuplicateEntries(response.entries);
            keepass.updateLastUsed(keepass.databaseHash);

            if (entries.length === 0) {
                // Questionmark-icon is not triggered, so we have to trigger for the normal symbol
                browserAction.showDefault(tab);
            }

            logDebug(`Found ${entries.length} entries for url ${url}`);
            return entries;
        }

        browserAction.showDefault(tab);
        return [];
    } catch (err) {
        logError(`retrieveCredentials failed: ${err}`);
        return [];
    }
};

keepass.generatePassword = async function(tab) {
    if (!keepass.isConnected) {
        return undefined;
    }

    try {
        const taResponse = await keepass.testAssociation(tab);
        if (!taResponse) {
            browserAction.showDefault(tab);
            return '';
        }

        if (!keepass.featuresList.passwordGenerator) {
            return '';
        }

        let password;
        const kpAction = kpActions.GENERATE_PASSWORD;
        const nonce = keepassClient.getNonce();

        const messageData = {
            action: kpAction,
            nonce: nonce,
            clientID: keepass.clientID,
            requestID: keepassClient.getRequestId()
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            password = response.entries ?? response.password;
            keepass.updateLastUsed(keepass.databaseHash);
        } else {
            logError('generatePassword rejected');
        }

        return password;
    } catch (err) {
        logError(`generatePassword failed: ${err}`);
        return undefined;
    }
};


keepass.getRiskEntryUuid = function(tab) {
    const state = tabs.getTabFromId(tab?.id);
    return state?.loginId || (state?.credentials?.length === 1 ? state.credentials[0].uuid : '');
};

keepass.validateRiskTab = async function(tab) {
    if (!tab?.id) { return undefined; }
    const current = await browser.tabs.get(tab.id);
    return current?.active && /^https?:\/\//i.test(current.url ?? '') ? current : undefined;
};

keepass.riskError = (code, revision = 0) =>
    ({ status: 'UNAVAILABLE', error_code: code, level: 'UNKNOWN', inputRevision: revision });

keepass.riskBinding = function(tab, context, revision, options = {}) {
    const state = tabs.getTabFromId(tab.id);
    const entryUuid = context === 'new' ? '' : (options.entryUuid ?? keepass.getRiskEntryUuid(tab));
    const entry = state?.credentials?.find((item) => item.uuid === entryUuid);
    return {
        context, entryUuid, account: context === 'new' ? (options.account ?? '') : (entry?.login ?? ''),
        origin: new URL(tab.url).origin, pageId: String(tab.id), inputRevision: revision,
        constraints: options.constraints ?? {}
    };
};

keepass.riskSend = async function(tab, action, data) {
    const revision = data.inputRevision ?? 0;
    if (!await keepass.testAssociation(tab)) { return keepass.riskError('NOT_ASSOCIATED', revision); }
    try {
        const response = await keepassClient.sendMessage(action, tab, {
            ...data, action, requestID: keepassClient.getRequestId()
        }, keepassClient.getNonce(), true);
        return { ...response, inputRevision: revision, status: response?.status ?? 'UNAVAILABLE',
            error_code: response?.error_code ?? (response?.status ? undefined : 'NATIVE_REQUEST_FAILED') };
    } catch (_err) { return keepass.riskError('NATIVE_REQUEST_FAILED', revision); }
};

keepass.assessPassword = async function(tab, args = []) {
    const [ candidate, context = 'new', revision = 0, options = {} ] = args;
    const current = await keepass.validateRiskTab(tab);
    if (!current || typeof candidate !== 'string') { return keepass.riskError('SOURCE_NOT_ALLOWED', revision); }
    return keepass.riskSend(current, 'assess-password',
        { ...keepass.riskBinding(current, context, revision, options), candidate });
};

keepass.assessGenericPassword = async function(tab, args = []) {
    const [ candidate, revision = 0 ] = args;
    const current = await keepass.validateRiskTab(tab);
    if (!current || typeof candidate !== 'string') { return keepass.riskError('SOURCE_NOT_ALLOWED', revision); }
    const response = await keepass.riskSend(current, 'assess-generic-password',
        { candidate, context: 'generic', inputRevision: revision, pageId: String(current.id), origin: new URL(current.url).origin });
    // Content receives a numerical estimate only, without vault metadata.
    return { status: response.status, error_code: response.error_code, inputRevision: revision,
        psm: response.psm, native: response.native, calibrated: false, level: 'UNKNOWN' };
};

keepass.rememberRiskCandidate = function(tab, binding, response) {
    if (!response?.candidateToken || !response?.candidate) { return; }
    tabs.updateTabValues(tab.id, { riskPending: {
        ...binding, candidate: response.candidate, candidateToken: response.candidateToken,
        databaseHash: keepass.databaseHash, createdAt: Date.now(), result: {
            status: response.status, resultStatus: response.resultStatus,
            trawling: response.trawling, reuse: response.reuse
        }
    } });
};

keepass.recommendPassword = async function(tab, args = []) {
    const [ context = 'change', revision = 0, options = {} ] = args;
    const current = await keepass.validateRiskTab(tab);
    if (!current) { return keepass.riskError('SOURCE_NOT_ALLOWED', revision); }
    await keepass.cancelRiskCandidate(current, [ revision ]);
    const binding = keepass.riskBinding(current, context, revision, options);
    if (!binding.account || (context === 'change' && !binding.entryUuid)) {
        return keepass.riskError('SELECT_ACCOUNT_REQUIRED', revision);
    }
    const state = tabs.getTabFromId(current.id);
    state.riskRevision = revision;
    const response = await keepass.riskSend(current, 'recommend-password', binding);
    if (state.riskRevision !== revision) { return keepass.riskError('STALE_INPUT_REVISION', revision); }
    keepass.rememberRiskCandidate(current, binding, response);
    return response;
};

keepass.prepareRiskCandidate = async function(tab, args = []) {
    const [ candidate, context = 'new', revision = 0, options = {} ] = args;
    const current = await keepass.validateRiskTab(tab);
    if (!current) { return keepass.riskError('SOURCE_NOT_ALLOWED', revision); }
    await keepass.cancelRiskCandidate(current, [ revision ]);
    const binding = keepass.riskBinding(current, context, revision, options);
    if (!binding.account || (context === 'change' && !binding.entryUuid)) {
        return keepass.riskError('SELECT_ACCOUNT_REQUIRED', revision);
    }
    const state = tabs.getTabFromId(current.id);
    state.riskRevision = revision;
    const response = await keepass.riskSend(current, 'prepare-risk-candidate', { ...binding, candidate });
    if (state.riskRevision !== revision) { return keepass.riskError('STALE_INPUT_REVISION', revision); }
    keepass.rememberRiskCandidate(current, binding, response);
    return response;
};

keepass.stageRiskCandidate = async function(tab, args = []) {
    const [ _candidate, revision = 0, targetIndex ] = args;
    const current = await keepass.validateRiskTab(tab);
    const pending = tabs.getTabFromId(current?.id)?.riskPending;
    if (!pending || pending.inputRevision !== revision) { return keepass.riskError('NO_PENDING_CANDIDATE', revision); }
    try {
        const filled = await browser.tabs.sendMessage(current.id, {
            action: 'fill_risk_candidate', candidate: pending.candidate, targetIndex
        });
        return filled?.status === 'OK' ? filled : keepass.riskError(filled?.error_code ?? 'SELECT_TARGET_FIELD', revision);
    } catch (_err) { return keepass.riskError('PAGE_FILL_FAILED', revision); }
};

keepass.getPendingRiskCandidate = async function(tab) {
    const current = await keepass.validateRiskTab(tab);
    const state = tabs.getTabFromId(current?.id);
    const pending = state?.riskPending;
    if (!pending || Date.now() - pending.createdAt > 600000
        || pending.origin !== new URL(current.url).origin || pending.databaseHash !== keepass.databaseHash
        || (pending.context === 'change' && keepass.getRiskEntryUuid(current) && keepass.getRiskEntryUuid(current) !== pending.entryUuid)) {
        if (state) { state.riskPending = undefined; }
        return keepass.riskError('NO_PENDING_CANDIDATE');
    }
    return { status: 'OK', ...pending };
};

keepass.confirmRiskCandidate = async function(tab, args = []) {
    const [ revision, websiteSucceeded = false, acknowledgeUnknown = false ] = args;
    const current = await keepass.validateRiskTab(tab);
    const pending = await keepass.getPendingRiskCandidate(tab);
    if (!current || pending.status !== 'OK') { return keepass.riskError('NO_PENDING_CANDIDATE', revision); }
    if (pending.inputRevision !== revision || !Number.isInteger(revision)) {
        return keepass.riskError('STALE_INPUT_REVISION', revision);
    }
    const response = await keepass.riskSend(current, 'confirm-risk-candidate', {
        ...pending, candidate: undefined, result: undefined, websiteSucceeded, acknowledgeUnknown
    });
    if (response.status === 'OK' || !['CONFIRMATION_REQUIRED', 'DATABASE_SAVE_FAILED', 'SAVE_TARGET_REQUIRED'].includes(response.error_code)) {
        tabs.updateTabValues(current.id, { riskPending: undefined });
    }
    if (response.status === 'OK') { await page.clearLogins(current.id); }
    return response;
};

keepass.cancelRiskCandidate = async function(tab, args = []) {
    const [ revision = 0 ] = args;
    const state = tabs.getTabFromId(tab?.id);
    const pending = state?.riskPending;
    if (state) { state.riskPending = undefined; state.riskRevision = revision; }
    if (keepass.isAssociated() && /^https?:\/\//i.test(tab?.url ?? '')) {
        return keepass.riskSend(tab, 'cancel-risk-candidate', {
            candidateToken: pending?.candidateToken ?? '', origin: new URL(tab.url).origin,
            pageId: String(tab.id), inputRevision: revision
        });
    }
    return { status: 'OK', inputRevision: revision };
};

keepass.getRiskContext = async function(tab) {
    return {
        accounts: (tabs.getTabFromId(tab?.id)?.credentials ?? []).map(({ uuid, login }) => ({ uuid, login })),
        selected: keepass.getRiskEntryUuid(tab)
    };
};

keepass.associate = async function(tab) {
    if (keepass.isAssociated()) {
        return AssociatedAction.ASSOCIATED;
    }

    try {
        await keepass.getDatabaseHash(tab);
        if (keepass.isDatabaseClosed || !keepass.isKeePassXCAvailable) {
            return AssociatedAction.NOT_ASSOCIATED;
        }

        keepass.clearErrorMessage(tab);

        const kpAction = kpActions.ASSOCIATE;
        const key = nacl.util.encodeBase64(keepass.keyPair.publicKey);
        const nonce = keepassClient.getNonce();
        const idKeyPair = nacl.box.keyPair();
        const idKey = nacl.util.encodeBase64(idKeyPair.publicKey);

        const messageData = {
            action: kpAction,
            key: key,
            idKey: idKey
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce, false, true);
        if (response) {
            keepass.setCryptoKey(response.id, idKey);
            keepass.associated.value = true;
            keepass.associated.hash = response.hash || 0;

            browserAction.showDefault(tab);
            return AssociatedAction.NEW_ASSOCIATION;
        }

        keepass.handleError(tab, kpErrors.ASSOCIATION_FAILED);
        return AssociatedAction.NOT_ASSOCIATED;
    } catch (err) {
        logError(`associate failed: ${err}`);
    }

    return AssociatedAction.NOT_ASSOCIATED;
};

keepass.testAssociation = async function(tab, args = []) {
    keepass.clearErrorMessage(tab);

    try {
        const [ enableTimeout = false, triggerUnlock = false ] = args;
        const dbHash = await keepass.getDatabaseHash(tab, [ enableTimeout, triggerUnlock ]);
        if (!dbHash) {
            return false;
        }

        if (keepass.isDatabaseClosed || !keepass.isKeePassXCAvailable) {
            return false;
        }

        if (!keepass.serverPublicKey) {
            if (tab && tabs.getTabFromId(tab.id)) {
                keepass.handleError(tab, kpErrors.PUBLIC_KEY_NOT_FOUND);
            }
            return false;
        }

        const kpAction = kpActions.TEST_ASSOCIATE;
        const nonce = keepassClient.getNonce();
        const [ dbid, dbkey ] = keepass.getCryptoKey();

        if (dbkey === null || dbid === null) {
            if (tab && tabs.getTabFromId(tab.id)) {
                keepass.handleError(tab, kpErrors.NO_SAVED_DATABASES_FOUND);
            }
            return false;
        }

        const messageData = {
            action: kpAction,
            id: dbid,
            key: dbkey
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce, enableTimeout);
        if (!response) {
            const hash = response.hash || 0;
            keepass.deleteKey(hash);
            keepass.isEncryptionKeyUnrecognized = true;
            keepass.handleError(tab, kpErrors.ENCRYPTION_KEY_UNRECOGNIZED);
            keepass.associated.value = false;
            keepass.associated.hash = null;
        } else if (!keepass.isAssociated()) {
            keepass.handleError(tab, kpErrors.ASSOCIATION_FAILED);
        } else {
            keepass.isEncryptionKeyUnrecognized = false;
            keepass.clearErrorMessage(tab);
        }

        return keepass.isAssociated();
    } catch (err) {
        logError(`testAssociation failed: ${err}`);
        return false;
    }
};

keepass.getDatabaseHash = async function(tab, args = []) {
    if (!keepass.isConnected) {
        keepass.handleError(tab, kpErrors.TIMEOUT_OR_NOT_CONNECTED);
        return '';
    }

    if (!keepass.serverPublicKey) {
        keepass.changePublicKeys(tab);
    }

    const [ enableTimeout = false, triggerUnlock = false ] = args;
    const kpAction = kpActions.GET_DATABASE_HASH;
    const [ nonce, incrementedNonce ] = keepassClient.getNonces();

    const messageData = {
        action: kpAction,
        connectedKeys: Object.keys(keepass.keyRing) // This will be removed in the future
    };

    const encrypted = keepassClient.encrypt(messageData, nonce);
    if (encrypted.length <= 0) {
        keepass.handleError(tab, kpErrors.PUBLIC_KEY_NOT_FOUND);
        keepass.updateDatabaseHashToContent();
        return keepass.databaseHash;
    }

    try {
        const request = keepassClient.buildRequest(
            kpAction,
            keepassClient.encrypt(messageData, nonce),
            nonce,
            keepass.clientID,
            triggerUnlock,
        );
        const response = await keepassClient.sendNativeMessage(request, enableTimeout);
        if (response.message && response.nonce) {
            const res = keepassClient.decrypt(response.message, response.nonce);
            if (!res) {
                keepass.handleError(tab, kpErrors.CANNOT_DECRYPT_MESSAGE);
                return '';
            }

            const message = nacl.util.encodeUTF8(res);
            const parsed = JSON.parse(message);
            if (keepassClient.verifyDatabaseResponse(parsed, incrementedNonce) && parsed.hash) {
                const oldDatabaseHash = keepass.databaseHash;
                keepass.setcurrentKeePassXCVersion(parsed.version);
                keepass.databaseHash = parsed.hash || '';

                if (oldDatabaseHash && oldDatabaseHash !== keepass.databaseHash) {
                    keepass.associated.value = false;
                    keepass.associated.hash = null;
                }

                keepass.isDatabaseClosed = false;
                keepass.isKeePassXCAvailable = true;

                // Update the databaseHash from legacy hash
                if (parsed.oldHash) {
                    keepass.updateDatabaseHash(parsed.oldHash, parsed.hash);
                }

                return parsed.hash;
            } else if (parsed.errorCode) {
                keepass.databaseHash = '';
                keepass.isDatabaseClosed = true;
                keepass.handleError(tab, kpErrors.DATABASE_NOT_OPENED);
                return keepass.databaseHash;
            }

            return keepass.databaseHash;
        }

        keepass.databaseHash = '';
        keepass.isDatabaseClosed = true;
        if ((response.message && response.message === '') || response.errorCode === kpErrors.TIMEOUT_OR_NOT_CONNECTED) {
            keepass.isKeePassXCAvailable = false;
            keepass.isConnected = false;
            keepass.handleError(tab, kpErrors.TIMEOUT_OR_NOT_CONNECTED);
        } else {
            keepass.handleError(tab, response.errorCode, response.error);
        }
        return keepass.databaseHash;
    } catch (err) {
        logError(`getDatabaseHash failed: ${err}`);
        return keepass.databaseHash;
    }
};

keepass.changePublicKeys = async function(tab, enableTimeout = false, connectionTimeout) {
    if (!keepass.isConnected) {
        keepass.handleError(tab, kpErrors.TIMEOUT_OR_NOT_CONNECTED);
        return false;
    }

    const kpAction = kpActions.CHANGE_PUBLIC_KEYS;
    const key = nacl.util.encodeBase64(keepass.keyPair.publicKey);
    const [ nonce, incrementedNonce ] = keepassClient.getNonces();
    keepass.clientID = nacl.util.encodeBase64(nacl.randomBytes(keepassClient.keySize));

    const request = {
        action: kpAction,
        publicKey: key,
        nonce: nonce,
        clientID: keepass.clientID
    };

    try {
        const response = await keepassClient.sendNativeMessage(request, enableTimeout, connectionTimeout);
        keepass.setcurrentKeePassXCVersion(response.version);
        keepass.updateFeaturesList(response.version);

        if (!keepassClient.verifyKeyResponse(response, key, incrementedNonce)) {
            if (tab && tabs.getTabFromId(tab.id)) {
                keepass.handleError(tab, kpErrors.KEY_CHANGE_FAILED);
            }

            keepass.updateDatabaseHashToContent();
            return false;
        }

        keepass.isKeePassXCAvailable = true;
        console.log(`${EXTENSION_NAME}: Server public key: ${nacl.util.encodeBase64(keepass.serverPublicKey)}`);
        return true;
    } catch (err) {
        logError(`changePublicKeys failed: ${err}`);
        return false;
    }
};

keepass.lockDatabase = async function(tab) {
    if (!keepass.isConnected) {
        keepass.handleError(tab, kpErrors.TIMEOUT_OR_NOT_CONNECTED);
        return false;
    }

    const kpAction = kpActions.LOCK_DATABASE;
    const nonce = keepassClient.getNonce();

    const messageData = {
        action: kpAction
    };

    try {
        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            keepass.isDatabaseClosed = true;
            keepass.updateDatabase();

            // Display error message in the popup
            keepass.handleError(tab, kpErrors.DATABASE_NOT_OPENED);
            return true;
        } else {
            keepass.isDatabaseClosed = true;
        }

        return false;
    } catch (err) {
        logError(`ockDatabase failed: ${err}`);
        return false;
    }
};

keepass.getDatabaseGroups = async function(tab) {
    try {
        const taResponse = await keepass.testAssociation(tab, [ false ]);
        if (!taResponse) {
            browserAction.showDefault(tab);
            return [];
        }

        keepass.clearErrorMessage(tab);

        if (!keepass.isConnected) {
            return [];
        }

        let groups = [];
        const kpAction = kpActions.GET_DATABASE_GROUPS;
        const nonce = keepassClient.getNonce();

        const messageData = {
            action: kpAction
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            groups = response.groups;
            groups.defaultGroup = page.settings.defaultGroup;
            groups.defaultGroupAlwaysAsk = page.settings.defaultGroupAlwaysAsk;
            keepass.updateLastUsed(keepass.databaseHash);
            return groups;
        }

        browserAction.showDefault(tab);
        return [];
    } catch (err) {
        logError(`getDatabaseGroups failed: ${err}`);
        return [];
    }
};

keepass.createNewGroup = async function(tab, args = []) {
    try {
        const [ groupName ] = args;
        const taResponse = await keepass.testAssociation(tab, [ false ]);
        if (!taResponse) {
            browserAction.showDefault(tab);
            return [];
        }

        keepass.clearErrorMessage(tab);

        if (!keepass.isConnected) {
            return [];
        }

        const kpAction = kpActions.CREATE_NEW_GROUP;
        const nonce = keepassClient.getNonce();

        const messageData = {
            action: kpAction,
            groupName: groupName
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            keepass.updateLastUsed(keepass.databaseHash);
            return response;
        } else {
            logError('getDatabaseGroups rejected');
        }

        browserAction.showDefault(tab);
        return [];
    } catch (err) {
        logError(`createNewGroup failed: ${err}`);
        return [];
    }
};

keepass.getTotp = async function(tab, args = []) {
    const [ uuid, oldTotp ] = args;
    if (!keepass.featuresList.newTotp) {
        return oldTotp;
    }

    const taResponse = await keepass.testAssociation(tab, [ false ]);
    if (!taResponse || !keepass.isConnected) {
        return;
    }

    const kpAction = kpActions.GET_TOTP;
    const nonce = keepassClient.getNonce();

    const messageData = {
        action: kpAction,
        uuid: uuid
    };

    try {
        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            keepass.updateLastUsed(keepass.databaseHash);
            return response.totp;
        }

        return;
    } catch (err) {
        logError(`getTotp failed: ${err}`);
    }
};

keepass.requestAutotype = async function(tab, args = []) {
    if (!keepass.isConnected) {
        keepass.handleError(tab, kpErrors.TIMEOUT_OR_NOT_CONNECTED);
        return false;
    }

    const kpAction = kpActions.REQUEST_AUTOTYPE;
    const nonce = keepassClient.getNonce();
    const search = await page.getBaseDomainFromUrl(args[0]);

    const messageData = {
        action: kpAction,
        search: search
    };

    try {
        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        return response;
    } catch (err) {
        logError(`requestAutotype failed: ${err}`);
        return false;
    }
};

keepass.passkeysRegister = async function(tab, args = []) {
    try {
        const taResponse = await keepass.testAssociation(tab, [ false ]);
        if (!taResponse || !keepass.isConnected || args.length < 2) {
            browserAction.showDefault(tab);
            return null;
        }

        const kpAction = kpActions.PASSKEYS_REGISTER;
        const nonce = keepassClient.getNonce();
        const [ publicKey, origin ] = args;
        const passkeyPublicKey = JSON.parse(JSON.stringify(publicKey));
        const relatedOrigins = await keepass.getPasskeysRelatedOrigins(passkeyPublicKey?.rp?.id);

        const messageData = {
            action: kpAction,
            publicKey: passkeyPublicKey,
            origin: origin,
            relatedOrigins: relatedOrigins,
            groupName: page?.settings?.defaultPasskeyGroup,
            keys: keepass.getCryptoKeys()
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            return response;
        }

        browserAction.showDefault(tab);
        return null;
    } catch (err) {
        logError(`passkeysRegister failed: ${err}`);
        return null;
    }
};

keepass.passkeysGet = async function(tab, args = []) {
    try {
        const taResponse = await keepass.testAssociation(tab, [ false ]);
        if (!taResponse || !keepass.isConnected || args.length < 2) {
            browserAction.showDefault(tab);
            return null;
        }

        const kpAction = kpActions.PASSKEYS_GET;
        const nonce = keepassClient.getNonce();
        const [ publicKey, origin ] = args;
        const passkeyPublicKey = JSON.parse(JSON.stringify(publicKey));
        const relatedOrigins = await keepass.getPasskeysRelatedOrigins(passkeyPublicKey?.rp?.id);

        const messageData = {
            action: kpAction,
            publicKey: passkeyPublicKey,
            origin: origin,
            relatedOrigins: relatedOrigins,
            keys: keepass.getCryptoKeys()
        };

        const response = await keepassClient.sendMessage(kpAction, tab, messageData, nonce);
        if (response) {
            return response;
        }

        browserAction.showDefault(tab);
        return null;
    } catch (err) {
        logError(`passkeysGet failed: ${err}`);
        return null;
    }
};

//--------------------------------------------------------------------------
// Keyring
//--------------------------------------------------------------------------

keepass.migrateKeyRing = function() {
    return new Promise((resolve, reject) => {
        browser.storage.local.get('keyRing').then((item) => {
            const keyring = item.keyRing;
            // Change dates to numbers, for compatibility with Chromium based browsers
            if (keyring) {
                let num = 0;
                for (const keyHash in keyring) {
                    const key = keyring[keyHash];
                    [ 'created', 'lastUsed' ].forEach((fld) => {
                        const v = key[fld];
                        if (v instanceof Date && v.valueOf() >= 0) {
                            key[fld] = v.valueOf();
                            num++;
                        } else if (typeof v !== 'number') {
                            key[fld] = Date.now().valueOf();
                            num++;
                        }
                    });
                }
                if (num > 0) {
                    browser.storage.local.set({ keyRing: keyring });
                }
            }
            resolve();
        });
    });
};

keepass.saveKey = function(hash, id, key) {
    if (!Object.hasOwn(keepass.keyRing, hash)) {
        keepass.keyRing[hash] = {
            id: id,
            key: key,
            hash: hash,
            created: new Date().valueOf(),
            lastUsed: new Date().valueOf()
        };
    } else {
        keepass.keyRing[hash].id = id;
        keepass.keyRing[hash].key = key;
        keepass.keyRing[hash].hash = hash;
        keepass.keyRing[hash].created = new Date().valueOf();
        keepass.keyRing[hash].lastUsed = new Date().valueOf();
    }

    browser.storage.local.set({ 'keyRing': keepass.keyRing });
};

keepass.updateLastUsed = function(hash) {
    if (Object.hasOwn(keepass.keyRing, hash)) {
        keepass.keyRing[hash].lastUsed = new Date().valueOf();
        browser.storage.local.set({ 'keyRing': keepass.keyRing });
    }
};

// Update the databaseHash from legacy hash
keepass.updateDatabaseHash = function(oldHash, newHash) {
    if (!oldHash || !newHash || oldHash === newHash) {
        return;
    }

    if ((oldHash in keepass.keyRing)) {
        keepass.keyRing[newHash] = keepass.keyRing[oldHash];
        keepass.keyRing[newHash].hash = newHash;
        delete keepass.keyRing[oldHash];
        browser.storage.local.set({ 'keyRing': keepass.keyRing });
    }
};

keepass.deleteKey = function(hash) {
    delete keepass.keyRing[hash];
    browser.storage.local.set({ 'keyRing': keepass.keyRing });
};

keepass.getCryptoKey = function() {
    let dbkey = null;
    let dbid = null;

    if (!(keepass.databaseHash in keepass.keyRing)) {
        return [ dbid, dbkey ];
    }

    dbid = keepass.keyRing[keepass.databaseHash].id;

    if (dbid) {
        dbkey = keepass.keyRing[keepass.databaseHash].key;
    }

    return [ dbid, dbkey ];
};

keepass.setCryptoKey = function(id, key) {
    keepass.saveKey(keepass.databaseHash, id, key);
};

keepass.getCryptoKeys = function() {
    const keys = [];

    for (const keyHash in keepass.keyRing) {
        keys.push({
            id: keepass.keyRing[keyHash].id,
            key: keepass.keyRing[keyHash].key
        });
    }

    return keys;
};

//--------------------------------------------------------------------------
// Connection
//--------------------------------------------------------------------------

keepass.enableAutomaticReconnect = async function() {
    // Disable for Windows if KeePassXC is older than 2.3.4
    if (!page.settings.autoReconnect) {
        return;
    }
    if (keepass.reconnectLoop === null) {
        keepass.reconnectLoop = setInterval(async () => {
            if (!keepass.isKeePassXCAvailable) {
                keepass.reconnect();
            }
        }, 1000);
    }
};

keepass.disableAutomaticReconnect = function() {
    clearInterval(keepass.reconnectLoop);
    keepass.reconnectLoop = null;
};

keepass.reconnect = async function(tab = null, connectionTimeout = 1500) {
    if (page?.settings?.connectionMethod === ConnectionMethod.WEBSOCKET) {
        await keepassClient.connectToWebSocket();
    } else {
        keepassClient.connectToNative();
    }

    keepass.generateNewKeyPair();
    const keyChangeResult = await keepass
        .changePublicKeys(tab, !!connectionTimeout, connectionTimeout)
        .catch(() => false);

    // Change public keys timeout
    if (!keyChangeResult) {
        return false;
    }

    const hash = await keepass.getDatabaseHash(tab);
    if (hash !== '') {
        keepass.clearErrorMessage(tab);
    }

    await keepass.testAssociation();
    await keepass.isConfigured();
    keepass.updateDatabaseHashToContent();
    return true;
};

//--------------------------------------------------------------------------
// Utils
//--------------------------------------------------------------------------

keepass.getErrorMessage = async function(tab, errorCode) {
    return kpErrors.getError(errorCode);
};

keepass.generateNewKeyPair = function() {
    keepass.keyPair = nacl.box.keyPair();
};

keepass.isConfigured = async function() {
    if (typeof(keepass.databaseHash) === 'undefined') {
        const hash = keepass.getDatabaseHash();
        return Object.hasOwn(keepass.keyRing, hash);
    }

    return keepass.databaseHash in keepass.keyRing;
};

keepass.checkDatabaseHash = async function(tab) {
    return keepass.databaseHash;
};

keepass.isAssociated = function() {
    return (keepass.associated.value && keepass.associated.hash && keepass.associated.hash === keepass.databaseHash);
};

keepass.setcurrentKeePassXCVersion = function(version) {
    if (version) {
        keepass.currentKeePassXC = version;
    }
};

keepass.keePassXCUpdateAvailable = async function() {
    const checkUpdate = Number(page.settings.checkUpdateKeePassXC);
    if (checkUpdate !== CHECK_UPDATE_NEVER) {
        const lastChecked = keepass.latestKeePassXC.lastChecked
            ? new Date(keepass.latestKeePassXC.lastChecked)
            : new Date(1986, 11, 21);
        const daysSinceLastCheck = Math.floor(((new Date()).getTime() - lastChecked.getTime()) / 86400000);
        if (daysSinceLastCheck >= checkUpdate) {
            await keepass.checkForNewKeePassXCVersion();
        }

        return compareVersion(keepass.currentKeePassXC, keepass.latestKeePassXC.version, false);
    }

    return false;
};

keepass.checkForNewKeePassXCVersion = async function() {
    let version = -1;

    try {
        const response = await fetch(keepass.latestVersionUrl, { signal: AbortSignal.timeout(DEFAULT_FETCH_TIMEOUT) });
        const jsonData = await response.json();
        if (jsonData?.tag_name && jsonData?.prerelease === false) {
            version = jsonData.tag_name;
            keepass.latestKeePassXC.version = version;
        }
    } catch (ex) {
        logError(`checkForNewKeePassXCVersion error: ${ex}`);
    }
    keepass.latestKeePassXC.lastChecked = new Date().valueOf();
};

// Implements retrieval of Related Origin Requests for passkeys
// https://www.w3.org/TR/webauthn-3/#sctn-related-origins
keepass.getPasskeysRelatedOrigins = async function(rpId) {
    if (!rpId) {
        return [];
    }

    try {
        const response = await fetch(`https://${rpId}/.well-known/webauthn`, {
            signal: AbortSignal.timeout(DEFAULT_FETCH_TIMEOUT),
        });

        // Basic reply validation, see: https://www.w3.org/TR/webauthn-3/#sctn-validating-relation-origin
        const isJson = response?.headers?.get('content-type')?.includes('application/json');
        if (!isJson) {
            logError('getRelatedOrigins error: Content-Type is not JSON');
            return [];
        }

        const jsonData = await response.json();
        if (!Array.isArray(jsonData?.origins)
            || jsonData?.origins?.length === 0
            || jsonData?.origins?.length > MAX_RELATED_ORIGIN_LABELS
            || !jsonData?.origins?.every((origin) => typeof origin === 'string')) {
            logError(
                `getRelatedOrigins error: origins is not a list of strings, or it exceeds the maximum count of ${MAX_RELATED_ORIGIN_LABELS}`,
            );
            return [];
        }

        return jsonData.origins;
    } catch (ex) {
        logError(`getRelatedOrigins error: ${ex}`);
    }

    return [];
};

keepass.clearErrorMessage = function(tab) {
    tabs.updateTabValues(tab?.id, { errorMessage: undefined });
};

keepass.handleError = function(tab, errorCode, errorMessage = '') {
    if (errorMessage.length === 0) {
        errorMessage = kpErrors.getError(errorCode);
    }

    logError(`${errorCode}: ${errorMessage}`);
    tabs.updateTabValues(tab?.id, { errorMessage: errorMessage });
};

keepass.updatePopup = function() {
    if (page && tabs.tabList.length > 0) {
        browserAction.showDefault();
    }
};

// Updates the database hashes to content script
keepass.updateDatabase = async function() {
    keepass.associated.value = false;
    keepass.associated.hash = null;
    page.clearAllLogins();

    await keepass.testAssociation(null, [ true ]);

    keepass.updatePopup();
    keepass.updateDatabaseHashToContent();
};

keepass.updateDatabaseHashToContent = async function() {
    try {
        // Get all active tabs from all windows
        const currentWindowTabs = await browser.tabs.query({ active: true, currentWindow: true, discarded: false });
        const otherTabs = await browser.tabs.query({ active: true, currentWindow: false, discarded: false });
        const allTabs = [ ...currentWindowTabs, ...otherTabs ];

        for (const tab of allTabs) {
            if (tab?.id) {
                // Send message to content script
                browser.tabs.sendMessage(tab.id, {
                    action: 'check_database_hash',
                    hash: { old: keepass.previousDatabaseHash, new: keepass.databaseHash },
                    connected: keepass.isKeePassXCAvailable
                }).catch((err) => {
                    logError('No content script available for this tab.');
                });
            }
        }

        keepass.previousDatabaseHash = keepass.databaseHash;
    } catch (err) {
        logError(`updateDatabaseHashToContent failed: ${err}`);
    }
};

keepass.updateFeaturesList = function (currentVersion) {
    const versionResults = keepass.compareMultipleVersions([
        keepass.requiredKeePassXC,
        '2.6.1',
        '2.7.0',
        '2.7.7',
        '2.7.10'
    ], currentVersion);

    keepass.featuresList = {
        downloadFaviconAfterSave: versionResults['2.7.0'],
        newTotp: versionResults['2.6.1'],
        passwordGenerator: versionResults['2.7.0'],
        passkeys: versionResults['2.7.7'],
        passkeysDefaultGroup: versionResults['2.7.10'],
        requiredKeePassXCVersionFound: versionResults[keepass.requiredKeePassXC],
        webSocket: false, // TODO: Enable when released in KeePassXC
        riskAssessment: true
    };
};

// Expects an array of versions to compare
keepass.compareMultipleVersions = function(versions, current, canBeEqual = true) {
    if (!Array.isArray(versions)) {
        return {};
    }

    const result = {};
    for (const version of versions) {
        result[version] = compareVersion(version, current, canBeEqual);
    }

    return result;
};

const removeDuplicateEntries = function(arr) {
    const newArray = [];

    for (const a of arr) {
        if (newArray.some(i => i.uuid === a.uuid)) {
            continue;
        }

        newArray.push(a);
    }

    return newArray;
};
