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
#include <QElapsedTimer>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QProcess>
#include <QSet>
#include <QTimer>
#include <QUuid>

#include <algorithm>
#ifdef Q_OS_WIN
#include <windows.h>
#endif

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
    , m_coldTimer(new QTimer(this))
    , m_probeTimer(new QTimer(this))
{
#ifdef Q_OS_WIN
    m_process->setCreateProcessArgumentsModifier([](QProcess::CreateProcessArguments* arguments) {
        arguments->flags |= CREATE_NO_WINDOW;
    });
#endif
    m_coldTimer->setSingleShot(true);
    m_probeTimer->setInterval(500);
    connect(m_probeTimer, &QTimer::timeout, this, &RiskAssessmentService::probeReadiness);
    connect(m_coldTimer, &QTimer::timeout, this, [this] {
        failAll("COLD_START_TIMEOUT");
        m_process->kill();
    });
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
            [this](int, QProcess::ExitStatus) {
                m_ready = false;
                m_coldTimer->stop();
                m_probeTimer->stop();
                failAll("PROCESS_EXITED");
            });
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
    m_ready = false;
    m_probeTimer->stop();
    m_coldTimer->stop();
    failAll("SERVICE_STOPPED");
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
            QTimer::singleShot(1000, m_process, [this] {
                if (m_process->state() != QProcess::NotRunning) {
                    m_process->kill();
                }
            });
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
    m_ready = false;
    m_process->setWorkingDirectory(QFileInfo(script).absolutePath());
    m_process->setProgram(pythonExecutable());
    m_process->setArguments({"-u", script, "--config", config});
    m_process->start(QIODevice::ReadWrite);
    m_coldTimer->start(180000);
    m_probeTimer->start();
}

void RiskAssessmentService::probeReadiness()
{
    if (m_process->state() == QProcess::Running && !m_ready) {
        m_process->write("{\"id\":\"host-readiness\",\"method\":\"ping\",\"params\":{}}\n");
    }
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
    pending.timeoutMs = timeoutMs;
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
        m_ready = false;
        m_process->kill();
    });
    m_pending.insert(id, pending);
    ensureStarted();
    writePending();
}

void RiskAssessmentService::writePending()
{
    if (m_process->state() != QProcess::Running || !m_ready) {
        return;
    }
    for (auto iterator = m_pending.begin(); iterator != m_pending.end(); ++iterator) {
        if (iterator->written) {
            continue;
        }
        iterator->written = true;
        m_process->write(iterator->line);
        iterator->timer->start(iterator->timeoutMs + HostTimeoutSlackMs);
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
        if (id == "host-readiness") {
            if (response.value("status") == "OK" && response.value("ready").toBool()) {
                m_ready = true;
                m_probeTimer->stop();
                m_coldTimer->stop();
                writePending();
            } else if (response.value("status") != "LOADING") {
                failAll("MODEL_LOAD_FAILED");
                m_process->kill();
            }
            continue;
        }
        auto iterator = m_pending.find(id);
        if (iterator == m_pending.end()) {
            continue;
        }
        auto reply = iterator->reply;
        iterator->timer->stop();
        iterator->timer->deleteLater();
        m_pending.erase(iterator);
        reply(response);
        if (response.value("status") == "TIMEOUT") {
            // A Python inference cannot be forcibly interrupted safely in-process.
            // Restart the child so a timed-out worker cannot starve later requests.
            m_ready = false;
            m_process->kill();
        }
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

    result.revision = vaultRevision(database);
    if (context == "generic") {
        return result;
    }
    QList<const Entry*> entries;
    const Entry* current = nullptr;
    for (const auto* entry : database->rootGroup()->entriesRecursive(false)) {
        if (!entry || entry->isRecycled() || entry->isExpired()) {
            continue;
        }
        if (entry->uuidToHex() == entryUuid) {
            if (context == "audit") {
                continue;
            }
            current = entry;
        }
        entries.append(entry);
        for (const auto* snapshot : entry->historyItems()) {
            if (snapshot && !snapshot->isExpired()) {
                entries.append(snapshot);
            }
        }
    }
    std::stable_sort(entries.begin(), entries.end(), [](const Entry* left, const Entry* right) {
        return left->timeInfo().lastModificationTime() > right->timeInfo().lastModificationTime();
    });
    if (context == "change" && current) {
        entries.removeAll(current);
        entries.prepend(current);
    }
    QSet<QString> seen;
    for (const auto* entry : entries) {
        if (!entry || entry->isRecycled() || entry->isExpired()) {
            continue;
        }
        ++result.eligibleCount;
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
    return result;
}

QString RiskAssessmentService::vaultRevision(const QSharedPointer<Database>& database)
{
    if (!database || !database->rootGroup()) {
        return {};
    }
    QCryptographicHash hash(QCryptographicHash::Sha256);
    auto add = [&hash](const QString& value) {
        const auto bytes = value.toUtf8();
        hash.addData(QByteArray::number(bytes.size()) + ':');
        hash.addData(bytes);
    };
    add(database->rootGroup()->uuidToHex());
    add(QString::number(database->contentRevision()));
    for (const auto* entry : database->rootGroup()->entriesRecursive(true)) {
        add(entry->uuidToHex());
        add(entry->username());
        add(entry->password());
        add(entry->url());
        add(entry->timeInfo().lastModificationTime().toString(Qt::ISODateWithMs));
        add(entry->timeInfo().expiryTime().toString(Qt::ISODateWithMs));
        add(entry->timeInfo().expires() ? "expires" : "never-expires");
        add(entry->isRecycled() ? "recycled" : "active");
    }
    return QString::fromLatin1(hash.result().toHex());
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
    auto elapsed = QSharedPointer<QElapsedTimer>::create();
    auto finish = [this, database, history, requestId, inputRevision, reply](QJsonObject result) {
        if (vaultRevision(database) != history.revision) {
            result = unavailable("STALE_VAULT_REVISION");
        }
        result["requestID"] = requestId;
        result["inputRevision"] = inputRevision;
        reply(result);
    };
    sendRequest("ping", {}, ServiceTimeoutMs, [=](const QJsonObject& ready) {
        if (ready.value("status") != "OK") {
            finish(ready);
            return;
        }
        elapsed->start();
        sendRequest("assess_trawling", {{"candidate", candidate}}, ServiceTimeoutMs, [=](const QJsonObject& raw) {
            auto trawling = decorateAssessment(raw, "trawling", history, requestId, inputRevision);
            if (context == "generic") {
                trawling.remove("vaultRevision");
                finish(trawling);
                return;
            }
            if (history.values.isEmpty()) {
                auto result = trawling;
                result["trawling"] = trawling;
                if (history.eligibleCount > 0) {
                    result["reuse"] = unavailable("UNUSABLE_HISTORY");
                }
                finish(result);
                return;
            }
            const auto remaining = ServiceTimeoutMs - int(elapsed->elapsed());
            if (remaining <= 0) {
                trawling["reuse"] = QJsonObject{{"status", "TIMEOUT"}, {"level", "UNKNOWN"}};
                trawling["trawling"] = trawling;
                finish(trawling);
                return;
            }
            sendRequest("assess_reuse",
                        {{"candidate", candidate}, {"history", QJsonArray::fromStringList(history.values)}},
                        remaining, [=](const QJsonObject& rawReuse) {
                auto result = trawling;
                result["trawling"] = trawling;
                result["reuse"] = decorateAssessment(rawReuse, "reuse", history, requestId, inputRevision);
                finish(result);
            });
        });
    });
}

void RiskAssessmentService::recommendPassword(const QSharedPointer<Database>& database,
                                              const QString& context,
                                              const QString& entryUuid,
                                              const QString& requestId,
                                              qint64 inputRevision,
                                              Reply reply,
                                              const QJsonObject& constraints)
{
    const auto history = collectHistory(database, context, entryUuid);
    auto finish = [=](QJsonObject result) {
        if (vaultRevision(database) != history.revision) {
            result = unavailable("STALE_VAULT_REVISION");
        }
        result["requestID"] = requestId;
        result["inputRevision"] = inputRevision;
        reply(result);
    };
    const int minimum = constraints.value("minLength").toInt(5);
    const int maximum = constraints.value("maxLength").toInt(20);
    const int length = constraints.value("length").toInt(qMin(20, maximum));
    const int budget = constraints.value("budgetMs").toInt(ServiceTimeoutMs);
    PasswordGenerator generator;
    generator.setLength(length);
    PasswordGenerator::CharClasses classes = PasswordGenerator::NoClass;
    if (constraints.value("lower").toBool(true)) { classes |= PasswordGenerator::LowerLetters; }
    if (constraints.value("upper").toBool(true)) { classes |= PasswordGenerator::UpperLetters; }
    if (constraints.value("digits").toBool(true)) { classes |= PasswordGenerator::Numbers; }
    if (constraints.value("symbols").toBool(true)) { classes |= PasswordGenerator::SpecialCharacters; }
    generator.setCharClasses(classes);
    generator.setFlags(PasswordGenerator::CharFromEveryGroup);
    generator.setExcludedCharacterSet(constraints.value("forbidden").toString());
    QStringList requiredGroups;
    const QString forbidden = constraints.value("forbidden").toString();
    auto require = [&](bool enabled, QString pool) {
        if (!enabled) { return; }
        for (const auto ch : forbidden) { pool.remove(ch); }
        requiredGroups.append(pool);
    };
    require(constraints.value("lower").toBool(true), "abcdefghijklmnopqrstuvwxyz");
    require(constraints.value("upper").toBool(true), "ABCDEFGHIJKLMNOPQRSTUVWXYZ");
    require(constraints.value("digits").toBool(true), "0123456789");
    require(constraints.value("symbols").toBool(true), "!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~");
    generator.setFlags(PasswordGenerator::NoFlags);
    const bool emptyGroup = std::any_of(requiredGroups.cbegin(), requiredGroups.cend(), [](const QString& pool) { return pool.isEmpty(); });
    if (minimum < 5 || maximum > 20 || minimum > maximum || length < minimum || length > maximum
        || budget < 100 || budget > 120000 || emptyGroup || requiredGroups.size() > length || !generator.isValid()) {
        finish(unavailable("CONSTRAINT_CONFLICT"));
        return;
    }
    if (history.eligibleCount > 0 && history.values.isEmpty()) {
        finish(unavailable("UNUSABLE_HISTORY"));
        return;
    }

    struct Generation {
        QElapsedTimer elapsed;
        int attempts = 0;
        QString candidate;
        QJsonObject reuse;
    };
    auto state = QSharedPointer<Generation>::create();
    // Weak capture avoids a cycle in the asynchronous retry closure.
    auto attempt = QSharedPointer<std::function<void()>>::create();
    QWeakPointer<std::function<void()>> weakAttempt(attempt);
    *attempt = [=] {
        auto retry = weakAttempt.toStrongRef();
        if (!retry) { return; }
        auto remaining = budget - int(state->elapsed.elapsed());
        if (remaining <= 0 || state->attempts >= 20) {
            finish({{"status", "TIMEOUT"}, {"error_code", "GENERATION_BUDGET_EXHAUSTED"},
                    {"level", "UNKNOWN"}, {"attempts", state->attempts}});
            return;
        }
        ++state->attempts;
        state->candidate = generator.generatePassword();
        for (const auto& pool : requiredGroups) {
            bool contains = false;
            for (const auto ch : state->candidate) { contains |= pool.contains(ch); }
            if (!contains) {
                QTimer::singleShot(0, this, [retry] { (*retry)(); });
                return;
            }
        }
        auto checkTrawling = [=](const QJsonObject& reuse) {
            state->reuse = reuse;
            if (!reuse.isEmpty() && reuse.value("status") != "OK") {
                finish(reuse);
                return;
            }
            const auto native = reuse.value("native").toObject();
            if (native.value("exact_match").toBool() || native.value("in_top_k").toBool()) {
                (*retry)();
                return;
            }
            const auto left = budget - int(state->elapsed.elapsed());
            if (left <= 0) {
                finish({{"status", "TIMEOUT"}, {"error_code", "GENERATION_BUDGET_EXHAUSTED"}, {"level", "UNKNOWN"}});
                return;
            }
            sendRequest("assess_trawling", {{"candidate", state->candidate}}, left, [=](const QJsonObject& raw) {
                if (raw.value("status") != "OK") {
                    finish(raw);
                    return;
                }
                const auto native = raw.value("native").toObject();
                if (!native.value("table_capped").toBool() && native.value("guess_number").toDouble() < 1e9) {
                    (*retry)();
                    return;
                }
                // Numerical intervals do not establish calibrated passing thresholds.
                QJsonObject result{{"status", "OK"}, {"resultStatus", "REVIEW_REQUIRED"},
                                   {"candidate", state->candidate}, {"attempts", state->attempts},
                                   {"calibrated", false}, {"constraints", constraints},
                                   {"trawling", decorateAssessment(raw, "trawling", history, requestId, inputRevision)}};
                if (!reuse.isEmpty()) {
                    result["reuse"] = decorateAssessment(reuse, "reuse", history, requestId, inputRevision);
                }
                finish(result);
            });
        };
        if (history.values.isEmpty()) {
            checkTrawling({});
        } else {
            sendRequest("assess_reuse",
                        {{"candidate", state->candidate}, {"history", QJsonArray::fromStringList(history.values)}},
                        remaining, checkTrawling);
        }
    };
    sendRequest("ping", {}, ServiceTimeoutMs, [=](const QJsonObject& ready) {
        if (ready.value("status") != "OK") {
            finish(ready);
            return;
        }
        state->elapsed.start();
        (*attempt)();
    });
}
