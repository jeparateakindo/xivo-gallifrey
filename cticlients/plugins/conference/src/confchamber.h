#ifndef _CONFERENCE2_CONFCHAMBER_H_
#define _CONFERENCE2_CONFCHAMBER_H_
#include <QWidget>
#include <QDebug>
#include <QLabel>
#include <QTimer>
#include <QAbstractTableModel>
#include <QTableView>
#include <QModelIndex>
#include <QPushButton>
#include <QVBoxLayout>
#include <QMouseEvent>
#include <QMap>
#include <QHeaderView>

#include "baseengine.h"
class ConfChamberView;

class ConfChamberModel : public QAbstractTableModel
{
    Q_OBJECT

    public:
        ConfChamberModel(QObject *parent, const QString &);
        enum ColOrder {
            ID, ACTION_MUTE, ACTION_KICK, ACTION_TALK_TO,
            ACTION_ALLOW_IN, ACTION_RECORD, ADMIN,
            NUMBER, SINCE, NAME, NB_COL
        };
        void setView(ConfChamberView *m_view);
        QString id() const;
        QString row2participantId(int) const;
        int isAdmin() { return m_admin; };
        int isAuthed() { return m_authed; };
    private slots:
        void confRoomChange(const QString &path, DStoreEvent event);
    protected:
        void timerEvent(QTimerEvent *event);
    private:
        void extractRow2IdMap();
        void updateView();
        void sort(int, Qt::SortOrder);
        int rowCount(const QModelIndex&) const;
        int columnCount(const QModelIndex&) const;
        QVariant data(const QModelIndex&, int) const;
        QVariant headerData(int , Qt::Orientation, int) const;
        Qt::ItemFlags flags(const QModelIndex &) const;
        bool m_admin;
        bool m_authed;
        QString m_id;
        ConfChamberView *m_view;
        QMap<int, QString> m_row2id;

};

class ConfChamberView : public QTableView
{
    Q_OBJECT

    public:
        ConfChamberView(QWidget *parent, ConfChamberModel *model);
    private slots:
        void onViewClick(const QModelIndex &);
    protected:
        virtual void mousePressEvent(QMouseEvent *event);
    private:
        int lastPressed;
};

class ConfChamber : public QWidget
{
    Q_OBJECT

    public:
        ConfChamber(const QString &id);
    public slots:
        void pauseConf();
        void allowedIn();
    private:
        QString m_id;
        ConfChamberModel *m_model;
        QLabel *m_moderatedRoom;
};

#endif