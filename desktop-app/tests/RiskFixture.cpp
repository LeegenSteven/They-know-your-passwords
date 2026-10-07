// Isolated acceptance-fixture helper. Secrets enter through stdin and are never returned.
#include "core/Database.h"
#include "core/CustomData.h"
#include "core/Group.h"
#include "core/Entry.h"
#include "core/Metadata.h"
#include "crypto/Crypto.h"
#include "keys/CompositeKey.h"
#include "keys/PasswordKey.h"
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTextStream>

int main(int argc, char** argv)
{
    QCoreApplication app(argc, argv);
    if (!Crypto::init()) { return 2; }
    QTextStream input(stdin);
    const auto request = QJsonDocument::fromJson(input.readLine().toUtf8()).object();
    auto key = QSharedPointer<CompositeKey>::create();
    key->addKey(QSharedPointer<PasswordKey>::create(request.value("master").toString()));
    auto db = QSharedPointer<Database>::create();
    const auto path = request.value("path").toString();
    if (request.value("operation") == "create") {
        db->rootGroup()->setUuid(QUuid::createUuid());
        db->metadata()->setName("Isolated browser acceptance");
        db->metadata()->customData()->set(CustomData::getKeyWithPrefix(CustomData::BrowserKeyPrefix, "acceptance-local"),
                                          request.value("associationKey").toString());
        db->setKey(key);
        if (!db->saveAs(path)) { return 3; }
    } else if (!db->open(path, key)) { return 4; }
    bool expectedMatches = false;
    for (const auto* entry : db->rootGroup()->entriesRecursive(false)) {
        if (entry->username() == request.value("account").toString()) {
            expectedMatches = entry->password() == request.value("expected").toString();
        }
    }
    const QJsonObject result{{"status", "OK"},
        {"databaseHash", QString::fromLatin1(QCryptographicHash::hash(db->rootGroup()->uuidToHex().toUtf8(), QCryptographicHash::Sha256).toHex())},
        {"entryCount", db->rootGroup()->entriesRecursive(false).size()}, {"expectedMatches", expectedMatches}};
    QTextStream output(stdout);
    output << QJsonDocument(result).toJson(QJsonDocument::Compact) << Qt::endl;
    return 0;
}
