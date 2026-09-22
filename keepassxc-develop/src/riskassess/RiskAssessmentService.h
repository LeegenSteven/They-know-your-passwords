/*
 * Local password-risk orchestration for the browser integration.
 * Password material is kept in memory and is only sent to the child process
 * over its inherited stdin/stdout pipes.
 */

#ifndef KEEPASSXC_RISKASSESSMENTSERVICE_H
#define KEEPASSXC_RISKASSESSMENTSERVICE_H

#include <QHash>
#include <QJsonObject>
#include <QObject>
#include <QSharedPointer>
#include <QStringList>

#include <functional>

class Database;
class QProcess;
class QTimer;

class RiskAssessmentService : public QObject
{
    Q_OBJECT

public:
    using Reply = std::function<void(const QJsonObject&)>;

    explicit RiskAssessmentService(QObject* parent = nullptr);
    ~RiskAssessmentService() override;

    static RiskAssessmentService* instance();

    void start();
    void stop();
    void assessPassword(const QSharedPointer<Database>& database,
                        const QString& candidate,
                        const QString& context,
                        const QString& entryUuid,
                        const QString& requestId,
                        qint64 inputRevision,
                        Reply reply);
    void recommendPassword(const QSharedPointer<Database>& database,
                           const QString& context,
                           const QString& entryUuid,
                           const QString& requestId,
                           qint64 inputRevision,
                           Reply reply);

private:
    struct HistorySnapshot
    {
        QStringList values;
        QString revision;
        int eligibleCount = 0;
        int skippedCount = 0;
        int truncatedCount = 0;
    };

    struct PendingRequest
    {
        Reply reply;
        QTimer* timer = nullptr;
        QByteArray line;
        bool written = false;
    };

    HistorySnapshot collectHistory(const QSharedPointer<Database>& database,
                                   const QString& context,
                                   const QString& entryUuid) const;
    QJsonObject decorateAssessment(const QJsonObject& response,
                                   const QString& route,
                                   const HistorySnapshot& history,
                                   const QString& requestId,
                                   qint64 inputRevision) const;
    void sendRequest(const QString& method, const QJsonObject& params, int timeoutMs, Reply reply);
    void ensureStarted();
    void writePending();
    void handleStdout();
    void failAll(const QString& errorCode);
    QString pythonExecutable() const;
    QString serviceScript() const;
    QString serviceConfig() const;

    QProcess* m_process;
    QByteArray m_stdoutBuffer;
    QHash<QString, PendingRequest> m_pending;
};

#endif // KEEPASSXC_RISKASSESSMENTSERVICE_H
