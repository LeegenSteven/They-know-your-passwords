#include "RiskAssessmentService.h"

#include "browser/BrowserSettings.h"
#include "core/Database.h"
#include "core/Entry.h"
#include "core/EntryAttributes.h"
#include "core/EntryPlaceholders.h"
#include "core/Group.h"
#include "core/PasswordGenerator.h"

#include <QCoreApplication>
#include <QCryptographicHash>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QSet>
#include <QTimer>
#include <QUuid>

#include <algorithm>

namespace
{
constexpr int MaxHistoryItems = 5;
constexpr int ServiceTimeoutMs = 3000;
constexpr int HostTimeoutSlackMs = 1500;

bool inPardDomain(const QString& value)
{
    if (value.isEmpty() || value.size() > 20) {
        return false;
    }
    for (const auto character : value) {
        const auto code = character.unicode();
        if (code < 32 || code > 126) {
            return false;
        }
    }
    return true;
}

QJsonObject unavailable(const QString& code)
{
    return {{"status", "UNAVAILABLE"}, {"error_code", code}, {"level", "UNKNOWN"}, {"calibrated", false}};
}
}

Q_GLOBAL_STATIC(RiskAssessmentService, s_riskAssessmentService)

RiskAssessmentService::RiskAssessmentService(QObject* parent)
    : QObject(parent)
    , m_process(new QProcess(this))
{
    m_process->setProcessChannelMode(QProcess::SeparateChannels);
    connect(m_process, &QProcess::started, this, &RiskAssessmentService::writePending);
    connect(m_process, &QProcess::readyReadStandardOutput, this, &RiskAssessmentService::handleStdout);
    connect(m_process, &QProcess::readyReadStandardError, m_process, [this] {
        // Diagnostics are deliberately discarded. They may name exception types,
        // but they must never be copied into the KeePassXC log.
        m_process->readAllStandardError();
    });
    connect(m_process,
            qOverload<int, QProcess::ExitStatus>(&QProcess::finished),
            this,
            [this](int, QProcess::ExitStatus) { failAll("PROCESS_EXITED"); });
    connect(m_process, &QProcess::errorOccurred, this, [this](QProcess::ProcessError) {
        if (m_process->state() == QProcess::NotRunning) {
            failAll("PROCESS_ERROR");
        }
    });
}

RiskAssessmentService::~RiskAssessmentService()
{
    stop();
}

RiskAssessmentService* RiskAssessmentService::instance()
{
    return s_riskAssessmentService;
}

QString RiskAssessmentService::pythonExecutable() const
{
    auto value = browserSettings()->riskPythonExecutable().trimmed();
    if (value.isEmpty()) {
        value = qEnvironmentVariable("KEEPASSXC_RISK_PYTHON");
    }
    return value.isEmpty() ? QStringLiteral("python") : QDir::fromNativeSeparators(value);
}

QString RiskAssessmentService::serviceScript() const
{
    auto value = browserSettings()->riskServiceScript().trimmed();
    if (value.isEmpty()) {
        value = qEnvironmentVariable("KEEPASSXC_RISK_SERVICE");
    }
    if (value.isEmpty()) {
        value = QDir(QCoreApplication::applicationDirPath()).filePath("algo-service/server.py");
    }
    return QDir::fromNativeSeparators(value);
}

QString RiskAssessmentService::serviceConfig() const
{
    auto value = browserSettings()->riskServiceConfig().trimmed();
    if (value.isEmpty()) {
        value = qEnvironmentVariable("KEEPASSXC_RISK_CONFIG");
    }
    if (value.isEmpty()) {
        value = QDir(QFileInfo(serviceScript()).absolutePath()).filePath("config.toml");
    }
    return QDir::fromNativeSeparators(value);
}

void RiskAssessmentService::start()
{
    if (browserSettings()->riskAssessmentEnabled()) {
        ensureStarted();
    }
}

void RiskAssessmentService::stop()
{
    if (m_process->state() == QProcess::NotRunning) {
        return;
    }

    const QJsonObject request{{"id", QUuid::createUuid().toString(QUuid::WithoutBraces)},
                              {"method", "shutdown"},
                              {"params", QJsonObject{}}};
    m_process->write(QJsonDocument(request).toJson(QJsonDocument::Compact) + '\n');
    m_process->closeWriteChannel();
    QTimer::singleShot(1000, m_process, [this] {
        if (m_process->state() != QProcess::NotRunning) {
            m_process->terminate();
        }
    });
}

void RiskAssessmentService::ensureStarted()
{
    if (m_process->state() != QProcess::NotRunning) {
        return;
    }

    const auto script = serviceScript();
    const auto config = serviceConfig();
    if (!QFileInfo(script).isFile() || !QFileInfo(config).isFile()) {
        failAll("CONFIGURATION_MISSING");
        return;
    }

    m_stdoutBuffer.clear();
    m_process->setWorkingDirectory(QFileInfo(script).absolutePath());
    m_process->setProgram(pythonExecutable());
    m_process->setArguments({"-u", script, "--config", config});
    m_process->start(QIODevice::ReadWrite);
}

void RiskAssessmentService::sendRequest(const QString& method,
                                        const QJsonObject& params,
                                        int timeoutMs,
                                        Reply reply)
{
    if (!browserSettings()->riskAssessmentEnabled()) {
        reply(unavailable("FEATURE_DISABLED"));
        return;
    }

    const auto id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const QJsonObject request{{"id", id}, {"method", method}, {"timeout_ms", timeoutMs}, {"params", params}};
    PendingRequest pending;
    pending.reply = std::move(reply);
    pending.line = QJsonDocument(request).toJson(QJsonDocument::Compact) + '\n';
    pending.timer = new QTimer(this);
    pending.timer->setSingleShot(true);
    connect(pending.timer, &QTimer::timeout, this, [this, id] {
        const auto iterator = m_pending.find(id);
        if (iterator == m_pending.end()) {
            return;
        }
        auto reply = iterator->reply;
        iterator->timer->deleteLater();
        m_pending.erase(iterator);
        reply({{"status", "TIMEOUT"},
               {"error_code", "HOST_TIMEOUT"},
               {"level", "UNKNOWN"},
               {"calibrated", false}});
    });
    m_pending.insert(id, pending);
    ensureStarted();
    writePending();
}

void RiskAssessmentService::writePending()
{
    if (m_process->state() != QProcess::Running) {
        return;
    }
    for (auto iterator = m_pending.begin(); iterator != m_pending.end(); ++iterator) {
        if (iterator->written) {
            continue;
        }
        iterator->written = true;
        m_process->write(iterator->line);
        iterator->timer->start(ServiceTimeoutMs + HostTimeoutSlackMs);
    }
}

void RiskAssessmentService::handleStdout()
{
    m_stdoutBuffer.append(m_process->readAllStandardOutput());
    while (true) {
        const auto newline = m_stdoutBuffer.indexOf('\n');
        if (newline < 0) {
            break;
        }
        const auto line = m_stdoutBuffer.left(newline);
        m_stdoutBuffer.remove(0, newline + 1);

        QJsonParseError error;
        const auto document = QJsonDocument::fromJson(line, &error);
        if (error.error != QJsonParseError::NoError || !document.isObject()) {
            failAll("INVALID_SERVICE_OUTPUT");
            m_process->kill();
            return;
        }

        const auto response = document.object();
        const auto id = response.value("id").toString();
        auto iterator = m_pending.find(id);
        if (iterator == m_pending.end()) {
            continue;
        }
        auto reply = iterator->reply;
        iterator->timer->stop();
        iterator->timer->deleteLater();
        m_pending.erase(iterator);
        reply(response);
    }
}

void RiskAssessmentService::failAll(const QString& errorCode)
{
    const auto requests = m_pending;
    m_pending.clear();
    for (const auto& pending : requests) {
        if (pending.timer) {
            pending.timer->stop();
            pending.timer->deleteLater();
        }
        pending.reply(unavailable(errorCode));
    }
}

RiskAssessmentService::HistorySnapshot
RiskAssessmentService::collectHistory(const QSharedPointer<Database>& database,
                                      const QString& context,
                                      const QString& entryUuid) const
{
    HistorySnapshot result;
    if (!database || !database->rootGroup()) {
        return result;
    }

    auto entries = database->rootGroup()->entriesRecursive(false);
    std::sort(entries.begin(), entries.end(), [](const Entry* left, const Entry* right) {
        return left->timeInfo().lastModificationTime() > right->timeInfo().lastModificationTime();
    });

    QCryptographicHash revision(QCryptographicHash::Sha256);
    revision.addData(database->rootGroup()->uuidToHex().toUtf8());
    QSet<QString> seen;
    const auto auditContext = context.compare("audit", Qt::CaseInsensitive) == 0;
    for (const auto* entry : entries) {
        if (!entry || entry->isRecycled() || entry->isExpired()) {
            continue;
        }
        if (auditContext && !entryUuid.isEmpty() && entry->uuidToHex() == entryUuid) {
            continue;
        }

        ++result.eligibleCount;
        revision.addData(entry->uuidToHex().toUtf8());
        revision.addData(QByteArray::number(entry->timeInfo().lastModificationTime().toMSecsSinceEpoch()));
        const auto rawPassword = entry->password();
        if (EntryPlaceholders::containsPlaceholder(rawPassword)) {
            ++result.skippedCount;
            continue;
        }
        const auto password = entry->resolveMultiplePlaceholders(rawPassword);
        if (!inPardDomain(password)) {
            ++result.skippedCount;
            continue;
        }
        if (seen.contains(password)) {
            continue;
        }
        seen.insert(password);
        if (result.values.size() < MaxHistoryItems) {
            result.values.append(password);
        } else {
            ++result.truncatedCount;
        }
    }
    result.revision = QString::fromLatin1(revision.result().toHex());
    return result;
}

QJsonObject RiskAssessmentService::decorateAssessment(const QJsonObject& response,
                                                       const QString& route,
                                                       const HistorySnapshot& history,
                                                       const QString& requestId,
                                                       qint64 inputRevision) const
{
    auto result = response;
    result.remove("id");
    result["requestID"] = requestId;
    result["inputRevision"] = inputRevision;
    result["route"] = route;
    result["historyUsed"] = history.values.size();
    result["historyEligible"] = history.eligibleCount;
    result["historySkipped"] = history.skippedCount;
    result["historyTruncated"] = history.truncatedCount;
    result["vaultRevision"] = history.revision;
    result["calibrated"] = false;
    result["level"] = "UNKNOWN";

    if (result.value("status").toString() == "OK") {
        const auto native = result.value("native").toObject();
        if (route == "reuse" && native.value("exact_match").toBool()) {
            result["level"] = "HIGH";
            result["reason"] = "EXACT_REUSE";
        } else {
            result["reason"] = "UNCALIBRATED";
        }
    } else if (!result.contains("reason")) {
        result["reason"] = result.value("error_code").toString("UNAVAILABLE");
    }
    return result;
}

void RiskAssessmentService::assessPassword(const QSharedPointer<Database>& database,
                                           const QString& candidate,
                                           const QString& context,
                                           const QString& entryUuid,
                                           const QString& requestId,
                                           qint64 inputRevision,
                                           Reply reply)
{
    const auto history = collectHistory(database, context, entryUuid);
    if (history.eligibleCount > 0 && history.values.isEmpty()) {
        auto response = unavailable("UNUSABLE_HISTORY");
        response["status"] = "OUT_OF_DOMAIN";
        reply(decorateAssessment(response, "reuse", history, requestId, inputRevision));
        return;
    }

    const auto route = history.values.isEmpty() ? QStringLiteral("trawling") : QStringLiteral("reuse");
    QJsonObject params{{"candidate", candidate}};
    if (route == "reuse") {
        params["history"] = QJsonArray::fromStringList(history.values);
    }
    const auto method = route == "reuse" ? QStringLiteral("assess_reuse") : QStringLiteral("assess_trawling");
    sendRequest(method, params, ServiceTimeoutMs, [this,
                                                   database,
                                                   context,
                                                   entryUuid,
                                                   history,
                                                   route,
                                                   requestId,
                                                   inputRevision,
                                                   reply](const QJsonObject& raw) {
        if (collectHistory(database, context, entryUuid).revision != history.revision) {
            auto stale = unavailable("STALE_VAULT_REVISION");
            reply(decorateAssessment(stale, route, history, requestId, inputRevision));
            return;
        }
        reply(decorateAssessment(raw, route, history, requestId, inputRevision));
    });
}

void RiskAssessmentService::recommendPassword(const QSharedPointer<Database>& database,
                                              const QString& context,
                                              const QString& entryUuid,
                                              const QString& requestId,
                                              qint64 inputRevision,
                                              Reply reply)
{
    const auto history = collectHistory(database, context, entryUuid);
    if (history.eligibleCount > 0 && history.values.isEmpty()) {
        auto response = unavailable("UNUSABLE_HISTORY");
        response["status"] = "OUT_OF_DOMAIN";
        response["requestID"] = requestId;
        response["inputRevision"] = inputRevision;
        reply(response);
        return;
    }

    PasswordGenerator generator;
    generator.loadSettingsFromConfig();
    if (!generator.isValid()) {
        reply(unavailable("GENERATOR_CONFIGURATION_INVALID"));
        return;
    }
    const auto candidate = generator.generatePassword();

    auto finishTrawling = [this,
                           database,
                           context,
                           entryUuid,
                           history,
                           candidate,
                           requestId,
                           inputRevision,
                           reply](const QJsonObject& reuse) {
        sendRequest("assess_trawling", {{"candidate", candidate}}, ServiceTimeoutMs,
                    [this,
                     database,
                     context,
                     entryUuid,
                     history,
                     candidate,
                     requestId,
                     inputRevision,
                     reuse,
                     reply](const QJsonObject& raw) {
            if (collectHistory(database, context, entryUuid).revision != history.revision) {
                auto stale = unavailable("STALE_VAULT_REVISION");
                stale["requestID"] = requestId;
                stale["inputRevision"] = inputRevision;
                reply(stale);
                return;
            }
            const auto trawling = decorateAssessment(raw, "trawling", history, requestId, inputRevision);
            QJsonObject result{{"requestID", requestId},
                               {"inputRevision", inputRevision},
                               {"status", "OK"},
                               {"resultStatus", "REVIEW_REQUIRED"},
                               {"candidate", candidate},
                               {"attempts", 1},
                               {"trawling", trawling},
                               {"calibrated", false}};
            if (!reuse.isEmpty()) {
                result["reuse"] = reuse;
            }
            if (raw.value("status").toString() != "OK"
                || (!reuse.isEmpty() && reuse.value("status").toString() != "OK")) {
                result["resultStatus"] = "UNAVAILABLE";
                result.remove("candidate");
            }
            reply(result);
        });
    };

    if (history.values.isEmpty()) {
        finishTrawling({});
        return;
    }
    sendRequest("assess_reuse",
                {{"candidate", candidate}, {"history", QJsonArray::fromStringList(history.values)}},
                ServiceTimeoutMs,
                [this, history, requestId, inputRevision, finishTrawling](const QJsonObject& raw) {
                    finishTrawling(decorateAssessment(raw, "reuse", history, requestId, inputRevision));
                });
}
