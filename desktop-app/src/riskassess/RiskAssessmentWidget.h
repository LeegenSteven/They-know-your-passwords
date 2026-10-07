#ifndef KEEPASSXC_RISKASSESSMENTWIDGET_H
#define KEEPASSXC_RISKASSESSMENTWIDGET_H
#include <QWidget>
#include <QSharedPointer>
#include <QJsonObject>
#include <QDateTime>
class Database;
class Entry;
class QLineEdit;
class QLabel;
class QComboBox;
class QCheckBox;
class QPushButton;
class QSpinBox;
class QTimer;

class RiskAssessmentWidget : public QWidget
{
    Q_OBJECT
public:
    explicit RiskAssessmentWidget(QWidget* parent = nullptr);
    void load(const QSharedPointer<Database>& database, Entry* entry = nullptr, bool newEntry = false);
    void setCandidate(const QString& value);
    static QString describe(const QJsonObject& response);
signals:
    void candidateAccepted(const QString& candidate);
    void feedbackChanged(const QString& feedback);
private:
    void setFeedback(const QString& feedback);
    void assess(bool generic = false);
    void recommend();
    void invalidate();
    QWeakPointer<Database> m_database;
    QString m_entryUuid;
    QString m_context;
    QString m_candidate;
    QString m_candidateRevision;
    QDateTime m_candidateExpires;
    qint64 m_revision = 0;
    QLineEdit* m_input;
    QLineEdit* m_forbidden;
    QLabel* m_result;
    QComboBox* m_entries;
    QCheckBox* m_websiteSuccess;
    QCheckBox* m_acknowledge;
    QPushButton* m_apply;
    QSpinBox* m_length;
    QSpinBox* m_budget;
    QTimer* m_debounce;
};
#endif
