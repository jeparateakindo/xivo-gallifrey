/* XIVO CTI clients
 * Copyright (C) 2007-2009  Proformatique
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License version 2 for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program; if not, write to the Free Software
 * Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA.
 *
 * Linking the Licensed Program statically or dynamically with other
 * modules is making a combined work based on the Licensed Program. Thus,
 * the terms and conditions of the GNU General Public License version 2
 * cover the whole combination.
 *
 * In addition, as a special exception, the copyright holders of the
 * Licensed Program give you permission to combine the Licensed Program
 * with free software programs or libraries that are released under the
 * GNU Library General Public License version 2.0 or GNU Lesser General
 * Public License version 2.1 or any later version of the GNU Lesser
 * General Public License, and with code included in the standard release
 * of OpenSSL under a version of the OpenSSL license (with original SSLeay
 * license) which is identical to the one that was published in year 2003,
 * or modified versions of such code, with unchanged license. You may copy
 * and distribute such a system following the terms of the GNU GPL
 * version 2 for the Licensed Program and the licenses of the other code
 * concerned, provided that you include the source code of that other code
 * when and as the GNU GPL version 2 requires distribution of source code.
 */

/* $Revision$
 * $Date$
 */

#ifndef __PEERSLAYOUT_H__
#define __PEERSLAYOUT_H__
#include <QLayout>

/*! \brief Grid layout to organize the peers
 *
 * Layout to organize the peers on the screen
 * The peers are movable.
 */
class PeersLayout : public QLayout
{
    Q_OBJECT
        public:
    //! constructor
    PeersLayout(QWidget * parent = 0);
    //! set geometry
    void setGeometry( const QRect & );
    //! return size Hint (prefered size)
    QSize sizeHint() const;
    QSize minimumSize() const;
    QSize maximumSize() const;
    void addWidget(QWidget *, QPoint);
    //! add the Item at a specific emplacement
    void addItem(QLayoutItem *, QPoint);
    //! default addItem implementation
    void addItem(QLayoutItem *);
    //! return the number of items
    int count() const;
    QLayoutItem* itemAt(int) const;
    QLayoutItem* takeAt(int);
    //! get m_nb_rows
    int nbRows() const { return m_nb_rows; };
    //! set m_nb_rows
    void setNbRows(int rows) { m_nb_rows = rows; };
    //! get m_nb_columns
    int nbColumns() const { return m_nb_columns; };
    //! set m_nb_columns
    void setNbColumns(int cols) { m_nb_columns = cols; };
    QPoint getPosInGrid(const QPoint &) const;
    QPoint getPosFromGrid(const QPoint &) const;
    void setItemPosition(int i, QPoint pos);
    void setItemPosition(QWidget * widget, QPoint pos);
    QPoint getItemPosition(int i) const;
    QPoint getItemPosition(QWidget * widget) const;
    QRect getGridRect( const QRect & ) const;
 private:
    int itemIndex(QWidget * widget) const;
    QPoint freePosition() const;
    QSize size() const;
    QSize maxItemSize() const;
    QList<QLayoutItem *> m_list;        //!< layout items list
    QList<QPoint> m_listPos;                //!< positions list
    int m_nb_rows;                                        //!< height
    int m_nb_columns;                                //!< width
};

#endif

