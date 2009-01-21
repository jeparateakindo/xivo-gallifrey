# XIVO Daemon

__version__   = '$Revision$'
__date__      = '$Date$'
__copyright__ = 'Copyright (C) 2007-2009 Proformatique'
__author__    = 'Corentin Le Gall'

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
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
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import cjson
import logging
import urllib

log = logging.getLogger('capas')

class Capabilities:
        allowed_funcs = ['agents',
                         
                         'conference',
                         'customerinfo',
                         'dial',
                         'directory',
                         'fax',
                         'features',
                         'history',
                         'messages',
                         
                         'presence',
                         'database',
                         'switchboard']
        
        allowed_xlets = {'agents' : ['agents'],
                         'agentsnext' : ['agents'],
                         'agentdetails' : ['agents'],
                         'queues' : ['agents'],
                         'queuedetails' : ['agents'],
                         'queueentrydetails' : ['agents'],
                         
                         'calls' : ['dial'],
                         'parking' : ['dial'],
                         'switchboard' : ['dial'],
                         
                         'customerinfo' : ['customerinfo'],
                         'datetime' : [],
                         'dial' : ['dial'],
                         'directory' : ['directory'],
                         'outlook' : [],
                         'fax' : ['fax'],
                         'features' : ['features'],
                         'history' : ['history'],
                         'identity' : [],     # might need 'agents'
                         'search' : ['dial'], # might need 'agents'
                         
                         'messages' : [],
                         'conference' : ['conference'],
                         'operator' : ['dial'],
                         
                         'mylocaldir' : [],
                         'callcampaign' : [],
                         'xletproto' : [],
                         'xletweb' : [],
                         'tabber' : [],
                         'video' : []}

        def __init__(self):
                self.capafuncs = []
                self.capadisps = []
                self.capaservices = []
                self.appliname = 'Client'
                self.guisettings = {}
                self.conngui = 0
                self.maxgui = -1
                self.presenceid = 'none'
                self.watchedpresenceid = 'none'
                self.guiurl = None
                return
        
        def setfuncs(self, capalist):
                for capa in capalist:
                        if capa in self.allowed_funcs and capa not in self.capafuncs:
                                self.capafuncs.append(capa)
                return
        
        def setxlets(self, capalist):
                for capa in capalist:
                        detail = capa.split('-')
                        if detail[0] in self.allowed_xlets.keys():
                                if capa not in self.capadisps:
                                        self.capadisps.append(capa)
                                self.setfuncs(self.allowed_xlets[detail[0]])
                return
        
        def setappliname(self, appliname):
                self.appliname = appliname
                return
        
        def setservices(self, services):
                self.capaservices = services
                return
        
        def setpresenceid(self, presenceid):
                self.presenceid = presenceid
                if self.watchedpresenceid == 'none':
                        self.watchedpresenceid = presenceid
                return
        
        def setwatchedpresenceid(self, watchedpresenceid):
                self.watchedpresenceid = watchedpresenceid
                return
        
        def getguisettings(self):
                guisettings = {}
                if self.guiurl is not None:
                        try:
                                gui = urllib.urlopen(self.guiurl)
                                guisettings = cjson.decode(gui.read())
                                gui.close()
                        except Exception:
                                log.exception('problem when reading guisettings from %s' % self.guiurl)
                return guisettings
        
        def setguisettings(self, urlsettings):
                self.guiurl = urlsettings
                return
        
        # maxgui's
        def setmaxgui(self, maxgui):
                self.maxgui = int(maxgui)
                return
        
        def getmaxgui(self):
                if self.maxgui == -1:
                        return 'infinite'
                else:
                        return str(self.maxgui)

        def toomuchusers(self):
                if self.maxgui == -1 or self.conngui < self.maxgui:
                        return False
                else:
                        return True

        def conn_inc(self):
                self.conngui += 1
                return

        def conn_dec(self):
                self.conngui -= 1
                return


        def all(self):
                return (2 ** (len(self.allowed_funcs)) - 1)

        def tostringlist(self, capalist_int):
                lst = []
                for capa in self.capafuncs:
                        n = 2 ** self.allowed_funcs.index(capa)
                        if (n & capalist_int):
                                lst.append(capa)
                return lst

        def match_funcs(self, ucapas, capa_str):
                capas = capa_str.split(',')
                for cap in capas:
                        if cap in self.capafuncs:
                                n = 2 ** self.allowed_funcs.index(cap)
                                if (n & ucapas):
                                        return True
                return False
