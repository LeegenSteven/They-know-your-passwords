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
    QJsonObject prepare(const QSharedPointer<Database>& database, QObject* connection,
                        const QJsonObject& binding, const QJsonObject& assessment);
    QJsonObject confirm(const QSharedPointer<Database>& database, QObject* connection, const QJsonObject& binding);
    void cancel(QObject* connection, const QString& token = {});
    void clear();

private:
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
    friend class TestRiskAssessment;
};
#endif
