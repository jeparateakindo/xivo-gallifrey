######################################################################
# Automatically generated by qmake (2.01a) jeu. f�vr. 1 18:28:19 2007
######################################################################

SBDIR = ../../switchboard/client
TEMPLATE = app
TARGET = 
DEPENDPATH += .
INCLUDEPATH += . $${SBDIR}
CONFIG -= debug
CONFIG += static

# Input
HEADERS += confwidget.h engine.h mainwidget.h popup.h
HEADERS += xmlhandler.h remotepicwidget.h urllabel.h servicepanel.h searchpanel.h
HEADERS += $${SBDIR}/dialpanel.h      $${SBDIR}/logwidget.h           $${SBDIR}/logeltwidget.h
HEADERS += $${SBDIR}/directorypanel.h $${SBDIR}/extendedtablewidget.h $${SBDIR}/peerchannel.h
HEADERS += $${SBDIR}/peerwidget.h     $${SBDIR}/peeritem.h

SOURCES += confwidget.cpp engine.cpp main.cpp mainwidget.cpp popup.cpp
SOURCES += xmlhandler.cpp remotepicwidget.cpp urllabel.cpp servicepanel.cpp
SOURCES += logwidget.cpp searchpanel.cpp peerwidget.cpp
SOURCES += $${SBDIR}/dialpanel.cpp           $${SBDIR}/logeltwidget.cpp $${SBDIR}/directorypanel.cpp
SOURCES += $${SBDIR}/extendedtablewidget.cpp $${SBDIR}/peerchannel.cpp  $${SBDIR}/peeritem.cpp

QT += network
QT += xml
RESOURCES += appli.qrc
TRANSLATIONS = xivoclient_fr.ts
RC_FILE = appli.rc

