#include "RiskAssessmentWidget.h"
#include "RiskAssessmentService.h"
#include "core/Database.h"
#include "core/Entry.h"
#include "core/Group.h"
#include "core/Tools.h"
#include <QCheckBox>
#include <QComboBox>
#include <QFormLayout>
#include <QLabel>
#include <QLineEdit>
#include <QPointer>
#include <QPushButton>
#include <QSignalBlocker>
#include <QSpinBox>
#include <QTimer>
#include <QVBoxLayout>

RiskAssessmentWidget::RiskAssessmentWidget(QWidget* parent) : QWidget(parent)
{
    auto* layout = new QVBoxLayout(this);
    auto* note = new QLabel(tr("六档 PSM 展示估计猜测区间；PARD 单独展示重用。未标定、未命中或截断均待验证。"), this);
    note->setWordWrap(true);
    layout->addWidget(note);
    auto* bands = new QLabel(tr("1: [1,10³)  2: [10³,10⁶)  3: [10⁶,10⁹)\n4: [10⁹,10¹²)  5: [10¹²,10¹⁵)  6: [10¹⁵,∞)"), this);
    layout->addWidget(bands);
    m_entries = new QComboBox(this);
    layout->addWidget(m_entries);
    m_input = new QLineEdit(this);
    m_input->setEchoMode(QLineEdit::Password);
    m_input->setObjectName("riskCandidateInput");
    layout->addWidget(m_input);
    auto* form = new QFormLayout;
    m_length = new QSpinBox(this);
    m_length->setRange(5, 20);
    m_length->setValue(20);
    form->addRow(tr("生成长度"), m_length);
    m_forbidden = new QLineEdit(this);
    form->addRow(tr("网站禁用字符"), m_forbidden);
    m_budget = new QSpinBox(this);
    m_budget->setRange(3, 120);
    m_budget->setValue(3);
    m_budget->setSuffix(tr(" 秒"));
    form->addRow(tr("推荐预算（主动延长）"), m_budget);
    layout->addLayout(form);
    auto* assessButton = new QPushButton(tr("评估通用强度与历史重用"), this);
    auto* generateButton = new QPushButton(tr("随机生成与复检"), this);
    layout->addWidget(assessButton);
    layout->addWidget(generateButton);
    m_result = new QLabel(tr("尚未评估"), this);
    m_result->setWordWrap(true);
    m_result->setTextInteractionFlags(Qt::TextSelectableByMouse);
    layout->addWidget(m_result);
    m_websiteSuccess = new QCheckBox(tr("我确认网站注册或改密已成功"), this);
    m_acknowledge = new QCheckBox(tr("我了解待验证状态，并确认采用候选"), this);
    m_apply = new QPushButton(tr("将候选交给条目编辑器"), this);
    m_apply->setEnabled(false);
    layout->addWidget(m_websiteSuccess);
    layout->addWidget(m_acknowledge);
    layout->addWidget(m_apply);
    layout->addStretch();
    m_debounce = new QTimer(this);
    m_debounce->setSingleShot(true);
    m_debounce->setInterval(500);
    connect(m_debounce, &QTimer::timeout, this, [this] { assess(true); });
    connect(m_input, &QLineEdit::textChanged, this, [this] {
        invalidate();
        if (!m_input->text().isEmpty()) { m_debounce->start(); }
    });
    connect(m_forbidden, &QLineEdit::textChanged, this, [this] { invalidate(); });
    connect(m_length, qOverload<int>(&QSpinBox::valueChanged), this, [this] { invalidate(); });
    connect(m_budget, qOverload<int>(&QSpinBox::valueChanged), this, [this] { invalidate(); });
    connect(m_entries, qOverload<int>(&QComboBox::currentIndexChanged), this, [this] {
        const auto db = m_database.toStrongRef();
        m_entryUuid = m_entries->currentData().toString();
        if (db) {
            const auto* entry = db->rootGroup()->findEntryByUuid(Tools::hexToUuid(m_entryUuid));
            setCandidate(entry ? entry->password() : QString{});
        }
        invalidate();
    });
    connect(assessButton, &QPushButton::clicked, this, [this] { m_debounce->stop(); assess(); });
    connect(generateButton, &QPushButton::clicked, this, &RiskAssessmentWidget::recommend);
    auto updateApply = [this] {
        m_apply->setEnabled(!m_candidate.isEmpty() && m_websiteSuccess->isChecked() && m_acknowledge->isChecked());
    };
    connect(m_websiteSuccess, &QCheckBox::toggled, this, updateApply);
    connect(m_acknowledge, &QCheckBox::toggled, this, updateApply);
    connect(m_apply, &QPushButton::clicked, this, [this] {
        const auto db = m_database.toStrongRef();
        if (db && RiskAssessmentService::vaultRevision(db) == m_candidateRevision
            && QDateTime::currentDateTimeUtc() < m_candidateExpires
            && !m_candidate.isEmpty() && m_websiteSuccess->isChecked() && m_acknowledge->isChecked()) {
            const auto candidate = m_candidate;
            invalidate();
            emit candidateAccepted(candidate);
        } else {
            invalidate();
            setFeedback(tr("候选已过期，或条目、数据库已变化，请重新生成。"));
        }
    });
}

void RiskAssessmentWidget::load(const QSharedPointer<Database>& database, Entry* entry, bool newEntry)
{
    m_database = database;
    m_context = entry ? (newEntry ? "new" : "change") : "audit";
    m_entryUuid = entry && !newEntry ? entry->uuidToHex() : QString{};
    QSignalBlocker block(m_entries);
    m_entries->clear();
    if (!entry && database) {
        for (const auto* value : database->rootGroup()->entriesRecursive(false)) {
            if (!value->isRecycled() && !value->isExpired()) {
                m_entries->addItem(value->title() + " · " + value->username(), value->uuidToHex());
            }
        }
        m_entryUuid = m_entries->currentData().toString();
        entry = database->rootGroup()->findEntryByUuid(Tools::hexToUuid(m_entryUuid));
    }
    m_entries->setVisible(m_context == "audit");
    m_apply->setVisible(m_context != "audit");
    m_websiteSuccess->setVisible(m_context != "audit");
    m_acknowledge->setVisible(m_context != "audit");
    setCandidate(entry ? entry->password() : QString{});
}

void RiskAssessmentWidget::setCandidate(const QString& value)
{
    QSignalBlocker block(m_input);
    m_input->setText(value);
    invalidate();
    if (!value.isEmpty() && !m_database.isNull()) { m_debounce->start(); }
}

void RiskAssessmentWidget::setFeedback(const QString& feedback)
{
    m_result->setText(feedback);
    emit feedbackChanged(feedback);
}

void RiskAssessmentWidget::invalidate()
{
    ++m_revision;
    m_debounce->stop();
    m_candidate.clear();
    m_apply->setEnabled(false);
    m_websiteSuccess->setChecked(false);
    m_acknowledge->setChecked(false);
    setFeedback(tr("输入已变化，旧结论已撤销。"));
}

QString RiskAssessmentWidget::describe(const QJsonObject& response)
{
    QStringList lines;
    const auto trawling = response.contains("trawling") ? response.value("trawling").toObject() : response;
    const auto psm = trawling.value("psm").toObject();
    if (trawling.value("status") == "OK" && !psm.isEmpty()) {
        if (psm.value("band").isDouble()) {
            lines << tr("PSM 第 %1/6 档：估计区间、待验证").arg(psm.value("band").toInt());
        } else {
            lines << tr("参考表截断；猜测次数下界 ≥ %1，待验证").arg(psm.value("lower_bound").toDouble(), 0, 'e', 3);
        }
        lines << tr("RankGuess 估计猜测次数：%1").arg(trawling.value("native").toObject().value("guess_number").toDouble(), 0, 'e', 3);
    } else {
        lines << tr("通用强度未知：%1").arg(trawling.value("error_code").toString(trawling.value("status").toString()));
    }
    const auto reuse = response.value("reuse").toObject();
    if (!reuse.isEmpty()) {
        const auto native = reuse.value("native").toObject();
        if (reuse.value("status") != "OK") {
            lines << tr("PARD 重用未知：%1").arg(reuse.value("error_code").toString(reuse.value("status").toString()));
        } else {
            lines << (native.value("exact_match").toBool() ? tr("PARD：精确复用，高风险")
                      : native.value("best_rank").isDouble() ? tr("PARD：搜索命中，排名 %1，待验证").arg(native.value("best_rank").toInt())
                      : tr("PARD：搜索未命中，风险未知"));
            lines << tr("搜索预算 beam=%1，top-k=%2；历史使用 %3 条")
                         .arg(native.value("beam_width").toInt()).arg(native.value("top_k").toInt())
                         .arg(reuse.value("historyUsed").toInt());
        }
    }
    return lines.join('\n');
}

void RiskAssessmentWidget::assess(bool generic)
{
    const auto revision = ++m_revision;
    const QPointer<RiskAssessmentWidget> guard(this);
    setFeedback(tr("正在评估；冷启动最多等待 180 秒…"));
    RiskAssessmentService::instance()->assessPassword(m_database.toStrongRef(), m_input->text(),
        generic ? "generic" : m_context, m_entryUuid, QString::number(revision), revision,
        [guard, revision](const QJsonObject& result) {
            if (guard && guard->m_revision == revision) {
                guard->setFeedback(describe(result));
            }
        });
}

void RiskAssessmentWidget::recommend()
{
    invalidate();
    const auto revision = m_revision;
    const QPointer<RiskAssessmentWidget> guard(this);
    setFeedback(tr("正在生成与复检；冷启动最多等待 180 秒…"));
    const QJsonObject constraints{{"length", m_length->value()}, {"forbidden", m_forbidden->text()},
                                  {"budgetMs", m_budget->value() * 1000}};
    RiskAssessmentService::instance()->recommendPassword(m_database.toStrongRef(), m_context,
        m_entryUuid, QString::number(revision), revision, [guard, revision](const QJsonObject& result) {
            if (!guard || guard->m_revision != revision) { return; }
            guard->setFeedback(describe(result));
            if (!result.value("candidate").toString().isEmpty()) {
                guard->m_candidate = result.value("candidate").toString();
                guard->m_candidateRevision = RiskAssessmentService::vaultRevision(guard->m_database.toStrongRef());
                guard->m_candidateExpires = QDateTime::currentDateTimeUtc().addSecs(600);
                QSignalBlocker block(guard->m_input);
                guard->m_input->setText(guard->m_candidate);
            }
        }, constraints);
}
