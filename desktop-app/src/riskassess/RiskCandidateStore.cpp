#include "RiskCandidateStore.h"
#include "RiskAssessmentService.h"
#include "core/Database.h"
#include "core/Entry.h"
#include "core/EntryPlaceholders.h"
#include "core/Group.h"
#include "core/Tools.h"
#include <QUrl>
#include <QUuid>

namespace {
QJsonObject failure(const QString& code)
{
    return {{"status", "UNAVAILABLE"}, {"error_code", code}, {"level", "UNKNOWN"}};
}
}

QJsonObject RiskCandidateStore::prepare(const QSharedPointer<Database>& database, QObject* connection,
                                       const QJsonObject& binding, const QJsonObject& assessment)
{
    if (!database || !connection || assessment.value("status") != "OK"
        || assessment.value("candidate").toString().isEmpty()) {
        return failure("CANDIDATE_NOT_READY");
    }
    const auto context = binding.value("context").toString();
    const auto origin = QUrl(binding.value("origin").toString());
    const auto account = binding.value("account").toString();
    const auto uuid = binding.value("entryUuid").toString();
    if ((origin.scheme() != "https" && origin.scheme() != "http") || origin.host().isEmpty()
        || account.isEmpty() || EntryPlaceholders::containsPlaceholder(account)
        || (context != "new" && context != "change")) {
        return failure("INVALID_CANDIDATE_BINDING");
    }
    if (context == "change") {
        const auto* entry = database->rootGroup()->findEntryByUuid(Tools::hexToUuid(uuid));
        if (!entry || entry->isRecycled() || entry->username() != account
            || EntryPlaceholders::containsPlaceholder(entry->password())) {
            return failure("TARGET_ENTRY_MISMATCH");
        }
    } else if (!uuid.isEmpty()) {
        return failure("INVALID_NEW_TARGET");
    }
    // Replace only this page's candidate; concurrent tabs have independent tokens.
    const auto page = binding.value("pageId");
    for (auto it = m_candidates.begin(); it != m_candidates.end();) {
        if (it->expires <= QDateTime::currentDateTimeUtc()
            || (it->connection == connection && it->binding.value("pageId") == page)) {
            it = m_candidates.erase(it);
        } else { ++it; }
    }
    const auto token = QUuid::createUuid().toString(QUuid::WithoutBraces)
        + QUuid::createUuid().toString(QUuid::WithoutBraces);
    Candidate candidate;
    candidate.database = database;
    candidate.connection = connection;
    candidate.binding = binding;
    candidate.password = assessment.value("candidate").toString();
    candidate.revision = RiskAssessmentService::vaultRevision(database);
    candidate.targetUuid = context == "new" ? Tools::uuidToHex(QUuid::createUuid()) : uuid;
    candidate.expires = QDateTime::currentDateTimeUtc().addSecs(600);
    candidate.reviewRequired = assessment.value("resultStatus") != "QUALIFIED";
    m_candidates.insert(token, candidate);
    auto result = assessment;
    result["candidateToken"] = token;
    result["expiresAt"] = candidate.expires.toString(Qt::ISODate);
    result["targetUuid"] = candidate.targetUuid;
    return result;
}

QJsonObject RiskCandidateStore::confirm(const QSharedPointer<Database>& database, QObject* connection,
                                       const QJsonObject& binding)
{
    const auto token = binding.value("candidateToken").toString();
    const auto it = m_candidates.find(token);
    if (it == m_candidates.end()) { return failure("NO_PENDING_CANDIDATE"); }
    const auto candidate = it.value();
    auto reject = [&](const QString& code) {
        m_candidates.remove(token);
        return failure(code);
    };
    if (candidate.expires <= QDateTime::currentDateTimeUtc()) { return reject("CANDIDATE_EXPIRED"); }
    if (!database || candidate.database.toStrongRef() != database || candidate.connection != connection) {
        return reject("STALE_DATABASE_SESSION");
    }
    for (const auto* field : {"context", "entryUuid", "account", "origin", "pageId", "inputRevision"}) {
        if (candidate.binding.value(field) != binding.value(field)) { return reject("STALE_CANDIDATE_BINDING"); }
    }
    if (RiskAssessmentService::vaultRevision(database) != candidate.revision) {
        return reject("STALE_VAULT_REVISION");
    }
    if (!binding.value("websiteSucceeded").toBool()
        || (candidate.reviewRequired && !binding.value("acknowledgeUnknown").toBool())) {
        return failure("CONFIRMATION_REQUIRED");
    }
    if (database->filePath().isEmpty()) { return failure("SAVE_TARGET_REQUIRED"); }
    Entry* target = nullptr;
    const bool create = candidate.binding.value("context") == "new";
    if (create) {
        target = new Entry;
        target->setUuid(Tools::hexToUuid(candidate.targetUuid));
        target->setTitle(QUrl(candidate.binding.value("origin").toString()).host());
        target->setUrl(candidate.binding.value("origin").toString());
        target->setUsername(candidate.binding.value("account").toString());
        target->setPassword(candidate.password);
        target->setGroup(database->rootGroup());
    } else {
        target = database->rootGroup()->findEntryByUuid(Tools::hexToUuid(candidate.targetUuid));
        if (!target || target->isRecycled()) { return reject("TARGET_ENTRY_MISSING"); }
    }
    QScopedPointer<Entry> backup(create ? nullptr : target->clone(Entry::CloneIncludeHistory));
    if (!create) {
        target->beginUpdate();
        target->setPassword(candidate.password);
        target->endUpdate();
    }
    QString error;
    if (!database->save(Database::Atomic, {}, &error)) {
        if (create) {
            delete target;
        } else {
            target->copyDataFrom(backup.data());
            target->removeHistoryItems(target->historyItems());
            for (const auto* item : backup->historyItems()) {
                target->addHistoryItem(item->clone());
            }
        }
        it->revision = RiskAssessmentService::vaultRevision(database);
        return failure("DATABASE_SAVE_FAILED");
    }
    m_candidates.remove(token);
    return {{"status", "OK"}, {"saved", true}, {"created", create}, {"entryUuid", candidate.targetUuid}};
}

void RiskCandidateStore::cancel(QObject* connection, const QString& token)
{
    for (auto it = m_candidates.begin(); it != m_candidates.end();) {
        if (it->connection == connection && (token.isEmpty() || it.key() == token)) {
            it = m_candidates.erase(it);
        } else { ++it; }
    }
}

void RiskCandidateStore::clear() { m_candidates.clear(); }
