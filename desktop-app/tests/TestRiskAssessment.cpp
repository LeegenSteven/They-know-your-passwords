#include "riskassess/RiskAssessmentService.h"
#include "riskassess/RiskCandidateStore.h"
#include "browser/BrowserSettings.h"
#include "browser/BrowserHost.h"
#include "core/Database.h"
#include "core/Entry.h"
#include "core/Group.h"
#include "core/Metadata.h"
#include "core/PasswordGenerator.h"
#include "core/Tools.h"
#include "crypto/Crypto.h"
#include "keys/CompositeKey.h"
#include "keys/PasswordKey.h"
#include <QTemporaryDir>
#include <QTest>
#include <QLocalServer>
#include <QLocalSocket>
#include <QSignalSpy>
#include <QFile>
#include <QProcess>
#include <QScopeGuard>

// Assertions involving secret material compare booleans, never values.
class TestRiskAssessment : public QObject
{
    Q_OBJECT
private:
    QString synthetic() {
        PasswordGenerator generator;
        generator.setLength(20);
        return generator.generatePassword();
    }
    Entry* entry(const QSharedPointer<Database>& db, const QString& account) {
        auto* value = new Entry;
        value->setUuid(QUuid::createUuid());
        value->setUsername(account);
        value->setPassword(synthetic());
        value->setGroup(db->rootGroup());
        return value;
    }
    QJsonObject binding(const QString& context, const QString& account, const QString& uuid = {}) {
        return {{"context", context}, {"account", account}, {"entryUuid", uuid},
                {"origin", "https://example.test"}, {"pageId", "1"}, {"inputRevision", 7}};
    }
    QJsonObject assessment() {
        return {{"status", "OK"}, {"resultStatus", "REVIEW_REQUIRED"}, {"candidate", synthetic()}};
    }
    QSharedPointer<Database> database(const QString& path, QSharedPointer<CompositeKey>& key) {
        auto db = QSharedPointer<Database>::create();
        db->rootGroup()->setUuid(QUuid::createUuid());
        key = QSharedPointer<CompositeKey>::create();
        key->addKey(QSharedPointer<PasswordKey>::create(synthetic()));
        db->setKey(key);
        if (!db->saveAs(path)) { return {}; }
        return db;
    }
private slots:
    void initTestCase() { QVERIFY(Crypto::init()); }
    void firstSaveCreatesAndPersists() {
        QTemporaryDir dir;
        QSharedPointer<CompositeKey> key;
        auto db = database(dir.filePath("risk.kdbx"), key);
        QVERIFY(db);
        QObject connection;
        RiskCandidateStore store;
        auto params = binding("new", "fictional-account");
        const auto result = store.prepare(db, &connection, params, assessment());
        QVERIFY(!result.value("candidateToken").toString().isEmpty());
        params["candidateToken"] = result.value("candidateToken");
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("CONFIRMATION_REQUIRED"));
        QCOMPARE(db->rootGroup()->entries().size(), 0);
        params["websiteSucceeded"] = true;
        params["acknowledgeUnknown"] = true;
        const auto saved = store.confirm(db, &connection, params);
        QVERIFY(saved.value("saved").toBool());
        QVERIFY(saved.value("created").toBool());
        QCOMPARE(db->rootGroup()->entries().size(), 1);
        auto reopened = QSharedPointer<Database>::create();
        QVERIFY(reopened->open(dir.filePath("risk.kdbx"), key));
        QCOMPARE(reopened->rootGroup()->entries().size(), 1);
        QVERIFY(reopened->rootGroup()->entries().first()->password() == result.value("candidate").toString());
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("NO_PENDING_CANDIDATE"));
    }
    void switchingAccountCannotUpdateOtherEntry() {
        auto db = QSharedPointer<Database>::create();
        auto* first = entry(db, "account-a");
        auto* second = entry(db, "account-b");
        const auto beforeFirst = first->password();
        const auto beforeSecond = second->password();
        QObject connection;
        RiskCandidateStore store;
        auto params = binding("change", first->username(), first->uuidToHex());
        params["candidateToken"] = store.prepare(db, &connection, params, assessment()).value("candidateToken");
        params["entryUuid"] = second->uuidToHex();
        params["account"] = second->username();
        params["websiteSucceeded"] = true;
        params["acknowledgeUnknown"] = true;
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("STALE_CANDIDATE_BINDING"));
        QVERIFY(first->password() == beforeFirst);
        QVERIFY(second->password() == beforeSecond);
    }
    void expirySessionRevisionAndMissingTarget() {
        auto db = QSharedPointer<Database>::create();
        auto* value = entry(db, "account-a");
        QObject connection;
        QObject otherConnection;
        RiskCandidateStore store;
        auto params = binding("change", value->username(), value->uuidToHex());
        auto prepare = [&] {
            params["candidateToken"] = store.prepare(db, &connection, params, assessment()).value("candidateToken");
        };
        prepare();
        store.m_candidates[params.value("candidateToken").toString()].expires = QDateTime::currentDateTimeUtc().addSecs(-1);
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("CANDIDATE_EXPIRED"));
        prepare();
        QCOMPARE(store.confirm(db, &otherConnection, params).value("error_code").toString(), QString("STALE_DATABASE_SESSION"));
        prepare();
        value->setPassword(synthetic());
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("STALE_VAULT_REVISION"));
        prepare();
        delete value;
        QCOMPARE(store.confirm(db, &connection, params).value("error_code").toString(), QString("STALE_VAULT_REVISION"));
        QCOMPARE(db->rootGroup()->entries().size(), 0);
    }
    void oldPasswordPriorityAndAuditExclusion() {
        auto db = QSharedPointer<Database>::create();
        auto* current = entry(db, "current");
        current->beginUpdate();
        current->setPassword(synthetic());
        current->endUpdate();
        const auto historical = current->historyItems().first()->password();
        auto oldTime = current->timeInfo();
        oldTime.setLastModificationTime(QDateTime::currentDateTimeUtc().addDays(-20));
        current->setTimeInfo(oldTime);
        for (int i = 0; i < 7; ++i) { entry(db, "other-" + QString::number(i)); }
        RiskAssessmentService service;
        const auto change = service.collectHistory(db, "change", current->uuidToHex());
        QCOMPARE(change.values.size(), 5);
        QVERIFY(change.values.first() == current->password());
        QVERIFY(change.truncatedCount > 0);
        const auto audit = service.collectHistory(db, "audit", current->uuidToHex());
        QVERIFY(!audit.values.contains(current->password()));
        QVERIFY(!audit.values.contains(historical));
        QCOMPARE(service.collectHistory(db, "generic", {}).values.size(), 0);
    }
    void constraintsRejectBeforeInference() {
        RiskAssessmentService service;
        QJsonObject result;
        service.recommendPassword({}, "new", {}, "constraint", 9, [&](const QJsonObject& value) { result = value; },
                                  {{"minLength", 15}, {"maxLength", 10}});
        QCOMPARE(result.value("error_code").toString(), QString("CONSTRAINT_CONFLICT"));
        QCOMPARE(result.value("inputRevision").toInt(), 9);
        result = {};
        service.recommendPassword({}, "new", {}, "constraint", 10, [&](const QJsonObject& value) { result = value; },
                                  {{"forbidden", "abcdefghijklmnopqrstuvwxyz"}});
        QCOMPARE(result.value("error_code").toString(), QString("CONSTRAINT_CONFLICT"));
    }
    void metadataAlsoInvalidatesCandidateRevision() {
        auto db = QSharedPointer<Database>::create();
        const auto before = RiskAssessmentService::vaultRevision(db);
        db->metadata()->setName("synthetic-vault-name");
        QVERIFY(before != RiskAssessmentService::vaultRevision(db));
    }
    void localMessagesSurvivePartialAndCoalescedReads() {
        BrowserHost host;
        const auto name = "risk-test-" + QUuid::createUuid().toString(QUuid::WithoutBraces);
        QVERIFY(host.m_localServer->listen(name));
        QSignalSpy received(&host, &BrowserHost::clientMessageReceived);
        QLocalSocket client;
        client.connectToServer(name);
        QTRY_COMPARE(host.m_socketList.size(), 1);
        client.write("{\"action\":\"synthetic");
        client.flush();
        QTest::qWait(20);
        QCOMPARE(received.size(), 0);
        client.write("\"}\n{\"action\":\"second\"}\n");
        client.flush();
        QTRY_COMPARE(received.size(), 2);
        client.disconnectFromServer();
        QTRY_COMPARE(host.m_socketList.size(), 0);
        QCOMPARE(host.m_readBuffers.size(), 0);
    }
    void hostTimeoutRestartsAndRecoversWithoutLateReply() {
        QTemporaryDir dir;
        QFile script(dir.filePath("synthetic_service.py"));
        QVERIFY(script.open(QIODevice::WriteOnly));
        script.write("import sys,json,time\nfor line in sys.stdin:\n r=json.loads(line)\n"
                     " if r['method']=='shutdown': break\n"
                     " if r['method']=='slow': time.sleep(2)\n"
                     " print(json.dumps({'id':r['id'],'status':'OK','ready':True}),flush=True)\n");
        script.close();
        QFile configFile(dir.filePath("config.toml"));
        QVERIFY(configFile.open(QIODevice::WriteOnly));
        configFile.close();
        const auto oldPython = browserSettings()->riskPythonExecutable();
        const auto oldScript = browserSettings()->riskServiceScript();
        const auto oldConfig = browserSettings()->riskServiceConfig();
        const auto oldEnabled = browserSettings()->riskAssessmentEnabled();
        auto restore = qScopeGuard([&] {
            browserSettings()->setRiskPythonExecutable(oldPython);
            browserSettings()->setRiskServiceScript(oldScript);
            browserSettings()->setRiskServiceConfig(oldConfig);
            browserSettings()->setRiskAssessmentEnabled(oldEnabled);
        });
        browserSettings()->setRiskPythonExecutable("D:/Anaconda3/envs/pytorch_cuda/python.exe");
        browserSettings()->setRiskServiceScript(script.fileName());
        browserSettings()->setRiskServiceConfig(configFile.fileName());
        browserSettings()->setRiskAssessmentEnabled(true);
        RiskAssessmentService service;
        QJsonObject first;
        int firstReplies = 0;
        service.sendRequest("slow", {}, 100, [&](const QJsonObject& result) { first = result; ++firstReplies; });
        QTRY_COMPARE_WITH_TIMEOUT(firstReplies, 1, 6000);
        QCOMPARE(first.value("error_code").toString(), QString("HOST_TIMEOUT"));
        QTRY_COMPARE(service.m_process->state(), QProcess::NotRunning);
        QJsonObject recovered;
        service.sendRequest("quick", {}, 1000, [&](const QJsonObject& result) { recovered = result; });
        QTRY_COMPARE_WITH_TIMEOUT(recovered.value("status").toString(), QString("OK"), 5000);
        QTest::qWait(250);
        QCOMPARE(firstReplies, 1);
        service.stop();
        QTRY_COMPARE(service.m_process->state(), QProcess::NotRunning);
    }
};
QTEST_GUILESS_MAIN(TestRiskAssessment)
#include "TestRiskAssessment.moc"
