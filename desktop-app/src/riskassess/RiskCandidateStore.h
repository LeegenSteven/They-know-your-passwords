#ifndef KEEPASSXC_RISKCANDIDATESTORE_H
#define KEEPASSXC_RISKCANDIDATESTORE_H

#include <QDateTime>
#include <QHash>
#include <QJsonObject>
#include <QPointer>
#include <QSharedPointer>

class Database;

// Tokens and candidates live only in the trusted host's memory.
class RiskCandidateStore
{
public:
    quint64 beginRequest(QObject* connection, const QJsonObject& binding);
    QJsonObject prepare(const QSharedPointer<Database>& database, QObject* connection,
                        const QJsonObject& binding, const QJsonObject& assessment, quint64 requestGeneration = 0);
    QJsonObject confirm(const QSharedPointer<Database>& database, QObject* connection, const QJsonObject& binding);
    // User/input cancellation is confined to the originating page and origin,
    // including when the caller has no pending token yet.
    void cancel(QObject* connection, const QJsonObject& binding, bool invalidateRequests = true);
    // Connection and database lifecycle cleanup must not reuse page cancellation.
    void clearConnection(QObject* connection);
    void clear();

private:
    struct Request {
        QString origin;
        quint64 generation;
    };
    struct Candidate {
        QWeakPointer<Database> database;
        QPointer<QObject> connection;
        QJsonObject binding;
        QString password;
        QString revision;
        QString targetUuid;
        QDateTime expires;
        bool reviewRequired = true;
    };
    QHash<QString, Candidate> m_candidates;
    QHash<QObject*, QHash<QString, Request>> m_requests;
    quint64 m_requestGeneration = 0;
    friend class TestRiskAssessment;
};
#endif
