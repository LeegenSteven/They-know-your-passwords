#ifndef KEEPASSXC_REPORTSPAGERISK_H
#define KEEPASSXC_REPORTSPAGERISK_H
#include "ReportsDialog.h"
#include "riskassess/RiskAssessmentWidget.h"
#include "gui/Icons.h"

class ReportsPageRisk : public IReportsPage
{
public:
    QString name() override { return QObject::tr("模型风险报告"); }
    QIcon icon() override { return icons()->icon("health"); }
    QWidget* createWidget() override { return new RiskAssessmentWidget; }
    void loadSettings(QWidget* widget, QSharedPointer<Database> db) override {
        static_cast<RiskAssessmentWidget*>(widget)->load(db);
    }
    void saveSettings(QWidget*) override {}
};
#endif
