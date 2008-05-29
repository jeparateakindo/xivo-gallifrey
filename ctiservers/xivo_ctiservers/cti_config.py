# XIVO Daemon

__version__   = '$Revision: 2730 $'
__date__      = '$Date: 2008-03-31 19:34:50 +0200 (lun, 31 mar 2008) $'
__copyright__ = 'Copyright (C) 2007, 2008, Proformatique'
__author__    = 'Corentin Le Gall'

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# Alternatively, XIVO Daemon is available under other licenses directly
# contracted with Pro-formatique SARL. See the LICENSE file at top of the
# source tree or delivered in the installable package in which XIVO Daemon
# is distributed for more details.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, you will find one at
# <http://www.gnu.org/licenses/old-licenses/gpl-2.0.html>.

from xivo import anysql
from xivo.BackSQL import backmysql
from xivo.BackSQL import backsqlite
import ConfigParser

class Config:
        def __init__(self, uri):
                self.uri = uri
                if uri.find(':') >= 0:
                        xconf = uri.split(':')
                        if xconf[0] == 'file':
                                self.kind = 'file'
                                self.xivoconf = ConfigParser.ConfigParser()
                                self.xivoconf.readfp(open(xconf[1]))
                        elif xconf[0] in ['mysql', 'sqlite']:
                                self.kind = 'sql'
                                self.conn = anysql.connect_by_uri(uri)
                                self.cursor = self.conn.cursor()
                else:
                        self.kind = 'file'
                        self.xivoconf = ConfigParser.ConfigParser()
                        self.xivoconf.readfp(open(uri))
                return


        def read_section(self, section):
                if self.kind == 'file':
                        try:
                                v = dict(self.xivoconf.items(section))
                        except Exception, exc:
                                print '--- exception (read_section) ---', self.kind, section, exc
                elif self.kind == 'sql':
                        v = {}
                        try:
                                self.cursor.query('SELECT * from %s' % section)
                                z = self.cursor.fetchall()
                                for zz in z:
                                        [num, var, val] = zz
                                        v[var] = val
                        except Exception, exc:
                                print '--- exception (read_section) ---', self.kind, section, exc
                return v
