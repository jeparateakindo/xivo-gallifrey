# XIVO Daemon

__version__   = '$Revision$'
__date__      = '$Date$'
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

"""
This is the XivoCTI class.
"""

import base64
import cjson
import csv
import logging
import os
import random
import re
import socket
import sha
import string
import threading
import time
import urllib
import zlib
import Queue
from xivo_ctiservers import cti_capas
from xivo_ctiservers import cti_fax
from xivo_ctiservers import cti_presence
from xivo_ctiservers import cti_userlist
from xivo_ctiservers import cti_urllist
from xivo_ctiservers import cti_agentlist
from xivo_ctiservers import cti_queuelist
from xivo_ctiservers import cti_phonelist
from xivo_ctiservers import cti_meetmelist
from xivo_ctiservers import xivo_commandsets
from xivo_ctiservers import xivo_ldap
from xivo_ctiservers.xivo_commandsets import BaseCommand
from xivo import anysql
from xivo.BackSQL import backmysql
from xivo.BackSQL import backsqlite

log = logging.getLogger('xivocti')

XIVOVERSION_NUM = '0.4'
XIVOVERSION_NAME = 'k-9'
REQUIRED_CLIENT_VERSION = 4661
__revision__ = __version__.split()[1]
__alphanums__ = string.uppercase + string.lowercase + string.digits
HISTSEPAR = ';'
DEFAULT_CONTEXT = 'default'
AGENT_NO_PHONENUM = 'N.A.'
AMI_ORIGINATE = 'originate'
MONITORDIR = '/var/spool/asterisk/monitor'
PRESENCE_UNKNOWN = 'Absent'

class XivoCTICommand(BaseCommand):

        xdname = 'XIVO Daemon'
        xivoclient_session_timeout = 60 # XXX

        fullstat_heavies = {}
        queues_channels_list = {}
        commnames = ['login_id', 'login_pass', 'login_capas',
                     'history', 'directory-search',
                     'featuresget', 'featuresput',
                     'getguisettings',
                     'phones',
                     'agents',
                     'queues',
                     'users',
                     'agent-status', 'agent',
                     'queue-status',
                     'callcampaign',
                     'faxsend',
                     'filetransfer',
                     'database',
                     'meetme',
                     'message',
                     'actionfiche',
                     'availstate',
                     'keepalive',
                     'originate', 'transfer', 'atxfer', 'hangup', 'simplehangup', 'pickup']
        
        def __init__(self, amilist, ctiports, queued_threads_pipe):
		BaseCommand.__init__(self)
                self.amilist = amilist
                self.capas = {}
                self.ulist_ng = cti_userlist.UserList()
                self.ulist_ng.setcommandclass(self)
                self.weblist = { 'agents' : {},
                                 'queues' : {},
                                 'vqueues' : {},
                                 'phones' : {},
                                 'meetme' : {} }
                # self.plist_ng = cti_phonelist.PhoneList()
                # self.plist_ng.setcommandclass(self)
                self.uniqueids = {}
                self.transfers_buf = {}
                self.transfers_ref = {}
                self.filestodownload = {}
                self.faxes = {}
                self.queued_threads_pipe = queued_threads_pipe
                self.disconnlist = []
                self.sheet_actions = {}
                self.ldapids = {}
                self.chans_incomingqueue = []
                self.chans_incomingdid = []
                self.meetme = {}
                self.tqueue = Queue.Queue()
                self.timeout_login = {}
                self.parkedcalls = {}
                self.stats_queues = {}
                self.ignore_dtmf = {}
                self.presence_sections = {}
                self.display_hints = {}
                
                # actionid (AMI) indexed hashes
                self.getvar_requests = {}
                self.ami_requests = {}
                
                return
        
        def get_list_commands(self):
                return ['json']
        
        def parsecommand(self, linein):
                params = linein.split()
                cmd = xivo_commandsets.Command(params[0], params[1:])
                cmd.struct = {}
                cmd.type = xivo_commandsets.CMD_OTHER
                try:
                        cmd.struct = cjson.decode(linein)
                        cmd.name = 'json'
                        if cmd.struct.get('class') == 'login_id':
                                cmd.type = xivo_commandsets.CMD_LOGIN_ID
                        elif cmd.struct.get('class') == 'login_pass':
                                cmd.type = xivo_commandsets.CMD_LOGIN_PASS
                        elif cmd.struct.get('class') == 'login_capas':
                                cmd.type = xivo_commandsets.CMD_LOGIN_CAPAS
                        elif cmd.struct.get('class') == 'filetransfer':
                                cmd.type = xivo_commandsets.CMD_TRANSFER
                except Exception, exc:
                        log.error('--- exception --- parsing json for <%s> : %s' % (linein, exc))
                        cmd.struct = {}
                return cmd
        
        def reset(self, mode, conn = None):
                """
                Tells a CTI client that the server will shortly be down.
                """
                if conn is None:
                        self.__fill_ctilog__('daemon stop', mode)
                else:
                        tosend = { 'class' : 'serverdown',
                                   'direction' : 'client',
                                   'mode' : mode }
                        conn.sendall(cjson.encode(tosend) + '\n')
                return
        
        def transfer_addbuf(self, req, buf):
                self.transfers_buf[req].append(buf)
                return
        
        def transfer_addref(self, connid, commandstruct):
                requester = '%s:%d' % connid.getpeername()
                fileid = commandstruct.get('fileid')
                tdir = commandstruct.get('tdirection')
                
                if connid in self.timeout_login:
                        self.timeout_login[connid].cancel()
                        del self.timeout_login[connid]
                if tdir == 'upload':
                        self.transfers_ref[requester] = fileid
                        self.transfers_buf[requester] = []
                        tosend = { 'class' : 'fileref',
                                   'direction' : 'client',
                                   'fileid' : fileid }
                        connid.sendall(cjson.encode(tosend) + '\n')
                else:
                        if fileid in self.filestodownload:
                                fname = self.filestodownload[fileid]
                                data = urllib.urlopen('file:%s' % fname).read()
                                tosend = { 'class' : 'fileref',
                                           'direction' : 'client',
                                           'filename' : fname,
                                           'payload' : base64.b64encode(data) }
                                connid.sendall(cjson.encode(tosend) + '\n')
                                del self.filestodownload[fileid]
                        else:
                                log.warning('fileid %s does not exist' % fileid)
                return
        
        def transfer_endbuf(self, req):
                log.info('full buffer received for %s : len=%d %s'
                          % (req, len(''.join(self.transfers_buf[req])), self.transfers_ref))
                if req in self.transfers_ref:
                        ref = self.transfers_ref[req]
                        if ref in self.faxes:
                                uinfo = self.faxes[ref].uinfo
                                astid = uinfo.get('astid')
                                reply = self.faxes[ref].sendfax(''.join(self.transfers_buf[req]),
                                                                self.configs[astid].faxcallerid,
                                                                self.amilist.ami[astid])
                                tosend = { 'class' : 'faxprogress',
                                           'direction' : 'client',
                                           'status' : reply.split(';')[0],
                                           'reason' : reply.split(';')[1] }
                                self.__send_msg_to_cti_client__(uinfo, cjson.encode(tosend))
                                del self.transfers_ref[req]
                                del self.transfers_buf[req]
                return
        
        def get_login_params(self, command, astid, connid):
                return command.struct

        def manage_login(self, loginparams, phase, uinfo):
                if phase == xivo_commandsets.CMD_LOGIN_ID:
                        missings = []
                        for argum in ['company', 'userid', 'ident', 'xivoversion', 'version']:
                                if argum not in loginparams:
                                        missings.append(argum)
                        if len(missings) > 0:
                                log.warning('missing args in loginparams : %s' % ','.join(missings))
                                return 'missing:%s' % ','.join(missings)

                        # trivial checks (version, client kind) dealing with the software used
                        xivoversion = loginparams.get('xivoversion')
                        if xivoversion != XIVOVERSION_NUM:
                                return 'xivoversion_client:%s;%d' % (xivoversion, XIVOVERSION_NUM)
                        svnversion = loginparams.get('version')
                        ident = loginparams.get('ident')
                        if len(ident.split('@')) == 2:
                                [whoami, whatsmyos] = ident.split('@')
                                # return 'wrong_client_identifier:%s' % whoami
                                if whatsmyos[:3] not in ['X11', 'WIN', 'MAC']:
                                        return 'wrong_os_identifier:%s' % whatsmyos
                        else:
                                return 'wrong_client_os_identifier:%s' % ident
                        if (not svnversion.isdigit()) or int(svnversion) < REQUIRED_CLIENT_VERSION:
                                return 'version_client:%s;%d' % (svnversion, REQUIRED_CLIENT_VERSION)
                        
                        # user match
                        userinfo = self.ulist_ng.finduser(loginparams.get('userid'), loginparams.get('company'))
                        if userinfo == None:
                                return 'user_not_found'
                        userinfo['prelogin'] = {'cticlienttype' : whoami,
                                                'cticlientos' : whatsmyos,
                                                'version' : svnversion,
                                                'sessionid' : ''.join(random.sample(__alphanums__, 10))}
                        return userinfo

                elif phase == xivo_commandsets.CMD_LOGIN_PASS:
                        # user authentication
                        missings = []
                        for argum in ['hashedpassword']:
                                if argum not in loginparams:
                                        missings.append(argum)
                        if len(missings) > 0:
                                log.warning('missing args in loginparams : %s' % ','.join(missings))
                                return 'missing:%s' % ','.join(missings)

                        if uinfo is not None:
                                userinfo = uinfo
                        hashedpassword = loginparams.get('hashedpassword')
                        tohash = '%s:%s' % (userinfo['prelogin']['sessionid'], userinfo.get('password'))
                        sha1sum = sha.sha(tohash).hexdigest()
                        if sha1sum != hashedpassword:
                                return 'login_password'
                        
                        iserr = self.__check_user_connection__(userinfo)
                        if iserr is not None:
                                return iserr
                        return userinfo

                elif phase == xivo_commandsets.CMD_LOGIN_CAPAS:
                        missings = []
                        for argum in ['state', 'capaid', 'lastconnwins', 'loginkind']:
                                if argum not in loginparams:
                                        missings.append(argum)
                        if len(missings) > 0:
                                log.warning('missing args in loginparams : %s' % ','.join(missings))
                                return 'missing:%s' % ','.join(missings)
                        
                        if uinfo is not None:
                                userinfo = uinfo
                        # settings (in agent mode for instance)
                        # userinfo['agent']['phonenum'] = phonenum
                        
                        state = loginparams.get('state')
                        capaid = loginparams.get('capaid')
                        subscribe = loginparams.get('subscribe')
                        lastconnwins = loginparams.get('lastconnwins')
                        
                        iserr = self.__check_capa_connection__(userinfo, capaid)
                        if iserr is not None:
                                return iserr
                        
                        self.__connect_user__(userinfo, state, capaid, lastconnwins)
                        
                        loginkind = loginparams.get('loginkind')
                        if loginkind == 'agent':
                                userinfo['agentphonenum'] = loginparams.get('phonenumber')
                                if loginparams.get('agentlogin'):
                                        self.__login_agent__(userinfo)
                        if subscribe is not None:
                                userinfo['subscribe'] = 0
                        return userinfo


        def manage_logout(self, userinfo, when):
                log.info('logout (%s) %s'
                          % (when, userinfo))
                userinfo['logouttime-ascii-last'] = time.asctime()
                self.__logout_agent__(userinfo)
                self.__disconnect_user__(userinfo)
                self.__fill_user_ctilog__(userinfo, 'cti_logout')
                return


        def __check_user_connection__(self, userinfo):
                #if userinfo.has_key('init'):
                #       if not userinfo['init']:
                #              return 'uninit_phone'

                ## if time.time() - userinfo['login'].get('sessiontimestamp') < self.xivoclient_session_timeout:
                if userinfo.has_key('login') and userinfo['login'].has_key('sessiontimestamp'):
                                log.warning('user %s already connected from %s'
                                          % (userinfo['user'], userinfo['login']['connection'].getpeername()))
                                if 'lastconnwins' in userinfo:
                                        if userinfo['lastconnwins']:
                                                # one should then disconnect the already connected instance
                                                pass
                                        else:
                                                return 'already_connected:%s:%d' % userinfo['login']['connection'].getpeername()
                                else:
                                        return 'already_connected:%s:%d' % userinfo['login']['connection'].getpeername()
                return None


        def __check_capa_connection__(self, userinfo, capaid):
                if capaid in self.capas and capaid in userinfo.get('capaids'):
                        if self.capas[capaid].toomuchusers():
                                return 'toomuchusers:%s' % self.capas[capaid].getmaxgui()
                else:
                        return 'capaid_undefined'
                return None


        def __connect_user__(self, userinfo, state, capaid, lastconnwins):
                try:
                        userinfo['capaid'] = capaid
                        userinfo['login'] = {}
                        userinfo['login']['sessiontimestamp'] = time.time()
                        userinfo['login']['logintimestamp'] = time.time()
                        userinfo['login']['logintime-ascii'] = time.asctime()
                        for v, vv in userinfo['prelogin'].iteritems():
                                userinfo['login'][v] = vv
                        del userinfo['prelogin']
                        # lastconnwins was introduced in the aim of forcing a new connection to take on for
                        # a given user, however it might breed problems if the previously logged-in process
                        # tries to reconnect ... unless we send something asking to Kill the process
                        userinfo['lastconnwins'] = lastconnwins

                        # we first check if 'state' has already been set for this customer, in which case
                        # the CTI clients will be sent back this previous state
                        presenceid = self.capas[capaid].presenceid
                        if 'state' in userinfo:
                                futurestate = userinfo.get('state')
                                # only if it was a "defined" state anyway
                                if presenceid in self.presence_sections and futurestate in self.presence_sections[presenceid].getstates():
                                        state = futurestate

                        if presenceid in self.presence_sections:
                                if state in self.presence_sections[presenceid].getstates() and state not in ['onlineoutgoing', 'onlineincoming']:
                                        userinfo['state'] = state
                                else:
                                        log.warning('(user %s) : state <%s> is not an allowed one => <%s>'
                                                    % (userinfo.get('user'), state, self.presence_sections[presenceid].getdefaultstate()))
                                        userinfo['state'] = self.presence_sections[presenceid].getdefaultstate()
                        else:
                                userinfo['state'] = 'xivo_unknown'

                        self.capas[capaid].conn_inc()
                except Exception, exc:
                        log.error("--- exception --- connect_user %s : %s" % (userinfo, exc))


        def __disconnect_user__(self, userinfo):
                try:
                        # state is unchanged
                        if 'login' in userinfo:
                                capaid = userinfo.get('capaid')
                                self.capas[capaid].conn_dec()
                                userinfo['last-version'] = userinfo['login']['version']
                                del userinfo['login']
                                userinfo['state'] = 'xivo_unknown'
                                self.__update_availstate__(userinfo, userinfo.get('state'))
                                # do not remove 'capaid' in order to keep track of it
                                # del userinfo['capaid'] # after __update_availstate__
                        else:
                                log.warning('userinfo does not contain login field : %s' % userinfo)
                except Exception, exc:
                        log.error("--- exception --- disconnect_user %s : %s" % (userinfo, exc))


        def loginko(self, loginparams, errorstring, connid):
                log.warning('user can not connect (%s) : sending %s' % (loginparams, errorstring))
                if connid in self.timeout_login:
                        self.timeout_login[connid].cancel()
                        del self.timeout_login[connid]
                tosend = { 'class' : 'loginko',
                           'direction' : 'client',
                           'errorstring' : errorstring }
                connid.sendall('%s\n' % cjson.encode(tosend))
                return

        def telldisconn(self, connid):
                tosend = { 'class' : 'disconn',
                           'direction' : 'client' }
                connid.sendall('%s\n' % cjson.encode(tosend))
                return

        def loginok(self, loginparams, userinfo, connid, phase):
                if phase == xivo_commandsets.CMD_LOGIN_ID:
                        tosend = { 'class' : 'login_id_ok',
                                   'direction' : 'client',
                                   'xivoversion' : XIVOVERSION_NUM,
                                   'version' : __revision__,
                                   'sessionid' : userinfo['prelogin']['sessionid'] }
                        repstr = cjson.encode(tosend)
                elif phase == xivo_commandsets.CMD_LOGIN_PASS:
                        tosend = { 'class' : 'login_pass_ok',
                                   'direction' : 'client',
                                   'capalist' : userinfo.get('capaids') }
                        repstr = cjson.encode(tosend)
                elif phase == xivo_commandsets.CMD_LOGIN_CAPAS:
                        capaid = userinfo.get('capaid')
                        presenceid = self.capas[capaid].presenceid
                        if presenceid in self.presence_sections:
                                allowed = self.presence_sections[presenceid].allowed(userinfo.get('state'))
                                details = self.presence_sections[presenceid].getdisplaydetails()
                        else:
                                allowed = {}
                                details = {}
                        wpid = self.capas[capaid].watchedpresenceid
                        if wpid in self.presence_sections:
                                cstatus = self.presence_sections[wpid].countstatus(self.__counts__(wpid))
                        else:
                                cstatus = {}
                        
                        tosend = { 'class' : 'login_capas_ok',
                                   'direction' : 'client',
                                   'astid' : userinfo.get('astid'),
                                   'xivo_userid' : userinfo.get('xivo_userid'),
                                   'capafuncs' : self.capas[capaid].tostringlist(self.capas[capaid].all()),
                                   'capaxlets' : self.capas[capaid].capadisps,
                                   'appliname' : self.capas[capaid].appliname,
                                   'guisettings' : self.capas[capaid].guisettings,
                                   'capapresence' : { 'names'   : details,
                                                      'state'   : userinfo.get('state'),
                                                      'allowed' : allowed },
                                   'presencecounter' : cstatus
                                   }
                        repstr = cjson.encode(tosend)
                        # if 'features' in capa_user:
                        # repstr += ';capas_features:%s' %(','.join(configs[astid].capafeatures))
                        if connid in self.timeout_login:
                                self.timeout_login[connid].cancel()
                                del self.timeout_login[connid]
                        self.__fill_user_ctilog__(userinfo, 'cti_login', '%s:%d' % connid.getpeername())
                        # we could also log : client's OS & version
                connid.sendall(repstr + '\n')

                if phase == xivo_commandsets.CMD_LOGIN_CAPAS:
                        self.__update_availstate__(userinfo, userinfo.get('state'))
                
                return

        def set_cticonfig(self, lconf):
                self.lconf = lconf

                self.sheet_actions = {}
                for where, sheetaction in lconf.read_section('sheet_events', 'sheet_events').iteritems():
                        if where in self.sheet_allowed_events and len(sheetaction) > 0:
                                self.sheet_actions[where] = lconf.read_section('sheet_action', sheetaction)
                return

        def set_ctilog(self, ctilog):
                if ctilog is not None:
                        self.ctilog_conn = anysql.connect_by_uri(ctilog)
                        self.ctilog_cursor = self.ctilog_conn.cursor()
                        self.__fill_ctilog__('daemon start', __revision__)
                else:
                        self.ctilog_conn = None
                        self.ctilog_cursor = None
                return
        
        def __fill_ctilog__(self, what, options = ''):
                if self.ctilog_conn is not None and self.ctilog_cursor is not None:
                        try:
                                datetime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                                columns = ('eventdate', 'loginclient', 'company', 'status', 'action', 'arguments')
                                self.ctilog_cursor.query("INSERT INTO ctilog (${columns}) "
                                                         "VALUES (%s, NULL, NULL, NULL, %s, %s)",
                                                         columns,
                                                         (datetime, what, options))
                        except Exception, exc:
                                log.error('--- exception --- (__fill_ctilog__) %s' % exc)
                        self.ctilog_conn.commit()
                return
        
        def __fill_user_ctilog__(self, uinfo, what, options = ''):
                if self.ctilog_conn is not None and self.ctilog_cursor is not None:
                        try:
                                datetime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
                                columns = ('eventdate', 'loginclient', 'company', 'status', 'action', 'arguments')
                                self.ctilog_cursor.query("INSERT INTO ctilog (${columns}) "
                                                         "VALUES (%s, %s, %s, %s, %s, %s)",
                                                         columns,
                                                         (datetime, uinfo.get('user'), uinfo.get('company'), uinfo.get('state'), what, options))
                        except Exception, exc:
                                log.error('--- exception --- (__fill_user_ctilog__) %s' % exc)
                        self.ctilog_conn.commit()
                return
        
        def set_options(self, xivoconf, allconf):
                self.xivoconf = xivoconf
                for var, val in self.xivoconf.iteritems():
                        if var.find('-') > 0 and val:
                                [name, prop] = var.split('-', 1)
                                if name not in self.capas:
                                        self.capas[name] = cti_capas.Capabilities()
                                if prop == 'xlets':
                                        self.capas[name].setxlets(val.split(','))
                                elif prop == 'funcs':
                                        self.capas[name].setfuncs(val.split(','))
                                elif prop == 'maxgui':
                                        self.capas[name].setmaxgui(val)
                                elif prop == 'appliname':
                                        self.capas[name].setappliname(val)
                                elif prop == 'guisettings':
                                        self.capas[name].setguisettings(val)
                                elif prop == 'presence':
                                        self.capas[name].setpresenceid(val)
                                        if val not in self.presence_sections:
                                                self.presence_sections[val] = cti_presence.Presence(allconf.read_section('presence', val))
                                elif prop == 'watchedpresence':
                                        self.capas[name].setwatchedpresenceid(val)
                                        if val not in self.presence_sections:
                                                self.presence_sections[val] = cti_presence.Presence(allconf.read_section('presence', val))
                for v, vv in allconf.read_section('phonehints', 'phonehints').iteritems():
                        vvv = vv.split(',')
                        if len(vvv) > 1:
                                self.display_hints[v] = { 'longname' : vvv[0],
                                                          'color' : vvv[1] }
                return
        
        def set_configs(self, configs):
                self.configs = configs
                return
        
        def set_phonelist(self, astid, urllist_phones):
                if astid not in self.uniqueids:
                        self.uniqueids[astid] = {}
                if astid not in self.parkedcalls:
                        self.parkedcalls[astid] = {}
                if astid not in self.ignore_dtmf:
                        self.ignore_dtmf[astid] = {}
                self.weblist['phones'][astid] = cti_phonelist.PhoneList(urllist_phones)
                self.weblist['phones'][astid].setcommandclass(self)
                self.weblist['phones'][astid].setdisplayhints(self.display_hints)
                return
        
        def set_agentlist(self, astid, urllist_agents):
                self.weblist['agents'][astid] = cti_agentlist.AgentList(urllist_agents)
                self.weblist['agents'][astid].setcommandclass(self)
                return
        
        def set_vqueuelist(self, astid, urllist_vqueues):
                self.weblist['vqueues'][astid] = cti_queuelist.QueueList(urllist_vqueues, True)
                self.weblist['vqueues'][astid].setcommandclass(self)
                return
        
        def set_queuelist(self, astid, urllist_queues):
                self.weblist['queues'][astid] = cti_queuelist.QueueList(urllist_queues)
                self.weblist['queues'][astid].setcommandclass(self)
                return
        
        def set_meetmelist(self, astid, urllist_meetme):
                self.weblist['meetme'][astid] = cti_meetmelist.MeetmeList(urllist_meetme)
                self.weblist['meetme'][astid].setcommandclass(self)
                return
        
        def set_contextlist(self, ctxlist):
                self.ctxlist = ctxlist
                return
        
        def getprofilelist(self):
                ret = {}
                for capaid in self.capas.keys():
                        ret[capaid] = self.capas[capaid].appliname.decode('utf8')
                return cjson.encode(ret)
        
        def updates(self):
                u_update = self.ulist_ng.update()
                # self.plist_ng.update()
                for astid, plist in self.weblist['phones'].iteritems():
                        for itemname in ['agents', 'queues', 'vqueues', 'phones', 'meetme']:
                                try:
                                        updatestatus = self.weblist[itemname][astid].update()
                                        for function in ['del', 'add']:
                                                if updatestatus[function]:
                                                        log.info('%s %s %s : %s' % (astid, itemname, function, updatestatus[function]))
                                                        tosend = { 'class' : itemname,
                                                                   'function' : function,
                                                                   'direction' : 'client',
                                                                   'astid' : astid,
                                                                   'deltalist' : updatestatus[function] }
                                                        self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                                        if itemname == 'queues':
                                                for qname, qq in self.weblist['queues'][astid].keeplist.iteritems():
                                                        qq['stats']['Xivo-Join'] = 0
                                                        qq['stats']['Xivo-Link'] = 0
                                                        qq['stats']['Xivo-Lost'] = 0
                                                        qq['stats']['Xivo-Rate'] = -1
                                                        qq['stats']['Xivo-Chat'] = 0
                                                        qq['stats']['Xivo-Wait'] = 0
                                                        self.__update_queue_stats__(astid, qname)
                                except Exception:
                                        log.exception('--- exception --- (updates : %s)' % itemname)
                        self.askstatus(astid, self.weblist['phones'][astid].keeplist)
                # check : agentnumber should be unique
                return

        def set_userlist_urls(self, urls):
                self.ulist_ng.setandupdate(urls)
                return

        def getagentslist(self, alist):
                lalist = {}
                for aitem in alist:
                        try:
                                if not aitem.get('commented'):
                                        aid = aitem.get('id')
                                        lalist[aid] = {'firstname' :  aitem.get('firstname'),
                                                       'lastname' :   aitem.get('lastname'),
                                                       'number' :     aitem.get('number'),
                                                       'password' :   aitem.get('passwd'),
                                                       'context' :    aitem.get('context'),
                                                       'ackcall' :    aitem.get('ackcall'),
                                                       'wrapuptime' : aitem.get('wrapuptime'),
                                                       
                                                       'queues' : {},
                                                       'stats' : {}}
                        except Exception, exc:
                                log.error('--- exception --- (getagentslist) : %s : %s' % (aitem, exc))
                return lalist

        def getphoneslist(self, plist):
                lplist = {}
                for pitem in plist:
                        try:
                                idx = '.'.join([pitem.get('protocol'), pitem.get('context'), pitem.get('name'), pitem.get('number')])
                                lplist[idx] = { 'number' : pitem.get('number'),
                                                'tech' : pitem.get('protocol'),
                                                'context': pitem.get('context'),
                                                'enable_hint': pitem.get('enablehint'),
                                                'initialized': pitem.get('initialized'),
                                                'phoneid': pitem.get('name'),

                                                'hintstatus' : 'none',
                                                'comms' : {}
                                                }
                        except Exception, exc:
                                log.error('--- exception --- (getphoneslist) : %s : %s' % (pitem, exc))
                return lplist

        def getmeetmelist(self, mlist):
                lmlist = {}
                for mitem in mlist:
                        try:
                                if not mitem.get('commented'):
                                        lmlist[mitem.get('id')] = { 'number' : mitem.get('number'),
                                                                    'name' : mitem.get('name'),
                                                                    'context' : mitem.get('context'),
                                                                    'pin' : mitem.get('pin'),
                                                                    'admin-pin' : mitem.get('admin-pin')
                                                                    }
                        except Exception:
                                log.error('--- exception --- (getmeetmelist : %s)' % mitem)
                return lmlist

        def getqueueslist(self, dlist):
                lqlist = {}
                for qitem in dlist:
                        try:
                                if not qitem.get('commented'):
                                        queuename = qitem.get('name')
                                        lqlist[queuename] = {'queuename' : queuename,
                                                             'number' : qitem.get('number'),
                                                             'context' : qitem.get('context'),
                                                             'id' : qitem.get('id'),
                                                             
                                                             'agents' : {},
                                                             'channels' : {},
                                                             'stats' : {}}
                        except Exception, exc:
                                log.error('--- exception --- (getqueueslist) : %s : %s' % (qitem, exc))
                return lqlist
        
        # fields set at startup by reading informations
        userfields = ['user', 'company', 'astid', 'password', 'fullname', 'capaids', 'context', 'phonenum', 'techlist', 'agentid', 'xivo_userid']
        def getuserslist(self, dlist):
                lulist = {}
                for uitem in dlist:
                        try:
                                # if uitem.get('enableclient') and uitem.get('loginclient'):
                                if True:
                                        astid = uitem.get('astid', 'xivo')
                                        uid = astid + '/' + uitem.get('id')
                                        lulist[uid] = {'user' : uitem.get('loginclient'),
                                                       'company' : uitem.get('context'),
                                                       'password' : uitem.get('passwdclient'),
                                                       'capaids' : uitem.get('profileclient').split(','),
                                                       'fullname' : uitem.get('fullname'),
                                                       'astid' : astid,
                                                       'techlist' : ['.'.join([uitem.get('protocol'), uitem.get('context'),
                                                                               uitem.get('name'), uitem.get('number')])],
                                                       'context' : uitem.get('context'),
                                                       'phoneid' : uitem.get('name'),
                                                       'phonenum' : uitem.get('number'),
                                                       'xivo_userid' : uitem.get('id'),
                                                       
                                                       'state'    : 'xivo_unknown'
                                                       }
                                        # set a default capaid value for users with only one capaid (most common case)
                                        if len(lulist[uid]['capaids']) == 1:
                                                lulist[uid]['capaid'] = lulist[uid]['capaids'][0]
                                        if uitem.get('enablevoicemail'):
                                                lulist[uid]['mwi'] = ['0', '0', '0']
                                        else:
                                                lulist[uid]['mwi'] = []
                                        if uitem.get('agentid') is not None:
                                                lulist[uid]['agentid'] = uitem.get('agentid')
                                        else:
                                                lulist[uid]['agentid'] = ''
                        except Exception, exc:
                                log.error('--- exception --- (getuserslist) : %s : %s' % (uitem, exc))
                return lulist
        
        def version(self):
                return __revision__
        
        def agents(self):
                return self.weblist['agents']
        
        def queues(self):
                return self.weblist['queues']
        
        def phones(self):
                return self.weblist['phones']
        
        def users(self):
                return self.ulist_ng.users()
        
        def connected_users(self):
                return self.ulist_ng.connected_users()
        
        def askstatus(self, astid, npl):
                for a, b in npl.iteritems():
                        self.__ami_execute__(astid, 'sendextensionstate', b['number'], b['context'])
                        self.__ami_execute__(astid, 'mailbox', b['number'], b['context'])
                return
        
        def __callback_timer__(self, what):
                thisthread = threading.currentThread()
                tname = thisthread.getName()
                log.info('__callback_timer__ (timer finished at %s) %s %s' % (time.asctime(), tname, what))
                thisthread.setName('%s-%s' % (what, tname))
                self.tqueue.put(thisthread)
                os.write(self.queued_threads_pipe[1], what)
                return

        def connected(self, connid):
                """
                Send a banner at login time
                """
                connid.sendall('XIVO CTI Server Version %s(%s) svn:%s\n' % (XIVOVERSION_NUM, XIVOVERSION_NAME,
                                                                            __revision__))
                self.timeout_login[connid] = threading.Timer(5, self.__callback_timer__, ('login',))
                self.timeout_login[connid].start()
                return

        def checkqueue(self):
                buf = os.read(self.queued_threads_pipe[0], 1024)
                log.info('checkqueue : read buf = %s, tqueue size = %d' % (buf, self.tqueue.qsize()))
                todisconn = []
                while self.tqueue.qsize() > 0:
                        thisthread = self.tqueue.get()
                        tname = thisthread.getName()
                        if tname.startswith('login'):
                                for connid, conntimerthread in self.timeout_login.iteritems():
                                        if conntimerthread == thisthread:
                                                todisconn.append(connid)
                for connid in todisconn:
                        del self.timeout_login[connid]
                return { 'disconnlist-user' : self.disconnlist,
                         'disconnlist-tcp'  : todisconn }

        def clear_disconnlist(self):
                self.disconnlist = []
                return


        def __send_msg_to_cti_client__(self, userinfo, strupdate):
                try:
                        if 'login' in userinfo and 'connection' in userinfo.get('login'):
                                mysock = userinfo.get('login')['connection']
                                mysock.sendall(strupdate + '\n', socket.MSG_WAITALL)
                except Exception, exc:
                        log.error('--- exception --- (__send_msg_to_cti_client__) : %s (%s) userinfo = %s'
                                  % (exc, exc.__class__, userinfo))
                        if userinfo not in self.disconnlist:
                                self.disconnlist.append(userinfo)
                                os.write(self.queued_threads_pipe[1], 'uinfo')
                return

        def __send_msg_to_cti_clients__(self, strupdate):
                try:
                        if strupdate is not None:
                                for userinfo in self.ulist_ng.keeplist.itervalues():
                                        self.__send_msg_to_cti_client__(userinfo, strupdate)
                except Exception, exc:
                        log.warning('--- exception --- (__send_msg_to_cti_clients__) : %s' % exc)
                return

        def __send_msg_to_cti_clients_except__(self, uinfo, strupdate):
                try:
                        if strupdate is not None:
                                for userinfo in self.ulist_ng.keeplist.itervalues():
                                        if userinfo is not uinfo:
                                                self.__send_msg_to_cti_client__(userinfo, strupdate)
                except Exception, exc:
                        log.warning('--- exception --- (__send_msg_to_cti_clients__) : %s' % exc)
                return


        sheet_allowed_events = ['incomingqueue', 'incomingdid',
                                'agentcalled', 'agentlinked', 'agentunlinked',
                                'agi', 'link', 'unlink', 'hangup',
                                'faxreceived',
                                'callmissed', # see GG (in order to tell a user that he missed a call)
                                'localphonecalled', 'outgoing']

        def __build_xmlqtui__(self, sheetkind, actionopt, inputvars):
                linestosend = []
                whichitem = actionopt.get(sheetkind)
                if whichitem is not None and len(whichitem) > 0:
                        for k, v in self.lconf.read_section('sheet_qtui', whichitem).iteritems():
                                try:
                                        r = urllib.urlopen(v)
                                        t = r.read()
                                        r.close()
                                except Exception, exc:
                                        log.error('--- exception --- __build_xmlqtui__ %s %s : %s' % (sheetkind, whichitem, exc))
                                        t = None
                                if t is not None:
                                        linestosend.append('<%s name="%s"><![CDATA[%s]]></%s>' % (sheetkind, k, t, sheetkind))
                return linestosend
        
        def __build_xmlsheet__(self, sheetkind, actionopt, inputvars):
                linestosend = []
                whichitem = actionopt.get(sheetkind)
                if whichitem is not None and len(whichitem) > 0:
                        for k, v in self.lconf.read_section('sheet_action', whichitem).iteritems():
                                try:
                                        vsplit = v.split('|')
                                        if len(vsplit) == 4:
                                                [title, type, defaultval, format] = v.split('|')
                                                basestr = format
                                                for kk, vv in inputvars.iteritems():
                                                        basestr = basestr.replace('{%s}' % kk, vv)
                                                basestr = re.sub('{[a-z\-]*}', defaultval, basestr)
                                                linestosend.append('<%s order="%s" name="%s" type="%s"><![CDATA[%s]]></%s>'
                                                                   % (sheetkind, k, title, type, basestr, sheetkind))
                                        else:
                                                log.warning('__build_xmlsheet__ wrong number of fields in definition for %s %s %s'
                                                          % (sheetkind, whichitem, k))
                                except Exception, exc:
                                        log.error('--- exception --- __build_xmlsheet__ %s %s : %s' % (sheetkind, whichitem, exc))
                return linestosend


        def __sheet_alert__(self, where, astid, context, event, extraevent = {}):
                # fields to display :
                # - internal asterisk/xivo : caller, callee, queue name, sda
                # - custom ext database
                # - custom (F)AGI
                # options :
                # - urlauto, urlx
                # - popup or not
                # - actions ?
                # - dispatch to one given person / all / subscribed ones
                if where in self.sheet_actions:
                        userinfos = []
                        actionopt = self.sheet_actions.get(where)
                        whoms = actionopt.get('whom')
                        if whoms is None or whoms == '':
                                log.warning('__sheet_alert__ (%s) : whom field for %s action has not been defined'
                                          % (astid, where))
                                return

                        linestosend = ['<?xml version="1.0" encoding="utf-8"?>',
                                       '<profile sessionid="sessid">',
                                       '<user>']
                        # XXX : sessid => uniqueid, in order to update (did => queue => ... hangup ...)
                        linestosend.append('<internal name="datetime"><![CDATA[%s]]></internal>' % time.asctime())
                        itemdir = {'xivo-where' : where,
                                   'xivo-astid' : astid,
                                   'xivo-context' : context,
                                   'xivo-time' : time.strftime('%H:%M:%S', time.localtime()),
                                   'xivo-date' : time.strftime('%Y-%m-%d', time.localtime())}
                        if actionopt.get('focus') == 'no':
                                linestosend.append('<internal name="nofocus"></internal>')

                        # 1/4
                        # fill a dict with the appropriate values + set the concerned users' list
                        if where == 'outgoing':
                                exten = event.get('Extension')
                                application = event.get('Application')
                                if application == 'Dial' and exten.isdigit():
                                        pass
                                
                        elif where == 'faxreceived':
                                itemdir['xivo-callerid'] = event.get('CallerID')
                                itemdir['xivo-faxpages'] = event.get('PagesTransferred')
                                itemdir['xivo-faxfile'] = event.get('FileName')
                                itemdir['xivo-faxstatus'] = event.get('PhaseEString')
                                
                        elif where in ['agentlinked', 'agentunlinked']:
                                dst = event.get('Channel2')[6:]
                                src = event.get('CallerID1')
                                chan = event.get('Channel1')
                                queuename = extraevent.get('xivo_queuename')
                                
                                itemdir['xivo-channel'] = chan
                                itemdir['xivo-queuename'] = queuename
                                itemdir['xivo-callerid'] = src
                                itemdir['xivo-agentnumber'] = dst
                                itemdir['xivo-uniqueid'] = event.get('Uniqueid1')
                                
                                userinfos.extend(self.__find_userinfos_by_agentnum__(astid, dst))
                                
                        elif where == 'agi':
                                r_caller = extraevent.get('caller_num')
                                r_called = extraevent.get('called_num')
                                for uinfo in self.ulist_ng.keeplist.itervalues():
                                        if uinfo.get('astid') == astid and uinfo.get('phonenum') == r_called:
                                                userinfos.append(uinfo)
                                itemdir['xivo-callerid'] = r_caller
                                itemdir['xivo-calledid'] = r_called
                                itemdir['xivo-tomatch-callerid'] = r_caller
                                itemdir['xivo-channel'] = extraevent.get('channel')
                                itemdir['xivo-uniqueid'] = extraevent.get('uniqueid')

                                linestosend.append('<internal name="called"><![CDATA[%s]]></internal>' % r_called)

                        elif where in ['link', 'unlink']:
                                itemdir['xivo-channel'] = event.get('Channel1')
                                itemdir['xivo-channelpeer'] = event.get('Channel2')
                                itemdir['xivo-uniqueid'] = event.get('Uniqueid1')
                                itemdir['xivo-callerid'] = event.get('CallerID1')
                                itemdir['xivo-calledid'] = event.get('CallerID2')
                                for uinfo in self.ulist_ng.keeplist.itervalues():
                                        if uinfo.get('astid') == astid and uinfo.get('phonenum') == itemdir['xivo-calledid']:
                                                userinfos.append(uinfo)
                                
                        elif where == 'hangup':
                                # print where, event
                                itemdir['xivo-channel'] = event.get('Channel')
                                itemdir['xivo-uniqueid'] = event.get('Uniqueid')
                                # find which userinfo's to contact ...

                        elif where == 'incomingdid':
                                chan = event.get('CHANNEL', '')
                                uid = event.get('UNIQUEID', '')
                                clid = event.get('XIVO_SRCNUM')
                                did  = event.get('XIVO_EXTENPATTERN')
                                
                                print 'ALERT %s %s (%s) uid=%s %s %s did=%s' % (astid, where, time.asctime(),
                                                                                uid, clid, chan, did)
                                self.chans_incomingdid.append(chan)
                                
                                itemdir['xivo-channel'] = chan
                                itemdir['xivo-uniqueid'] = uid
                                itemdir['xivo-did'] = did
                                itemdir['xivo-callerid'] = clid
                                if len(clid) > 7 and clid != '<unknown>':
                                        itemdir['xivo-tomatch-callerid'] = clid
                                linestosend.append('<internal name="uniqueid"><![CDATA[%s]]></internal>' % uid)

                                # userinfos.append() : users matching the SDA ?

                                # interception :
                                # self.__ami_execute__(astid, 'transfer', chan, shortphonenum, 'default')
                                
                        elif where == 'incomingqueue':
                                clid = event.get('CallerID')
                                chan = event.get('Channel')
                                uid = event.get('Uniqueid')
                                queue = event.get('Queue')
                                
                                # find who are the queue members
                                for agent_channel, status in self.weblist['queues'][astid].keeplist[queue]['agents'].iteritems():
                                        if status.get('Paused') == '0':
                                                userinfos.extend(self.__find_userinfos_by_agentnum__(astid, agent_channel[6:]))
                                print 'ALERT %s %s (%s) uid=%s %s %s queue=(%s %s %s)' % (astid, where, time.asctime(), uid, clid, chan,
                                                                                          queue, event.get('Position'), event.get('Count'))
                                self.chans_incomingqueue.append(chan)
                                
                                itemdir['xivo-channel'] = chan
                                itemdir['xivo-uniqueid'] = uid
                                itemdir['xivo-queuename'] = queue
                                itemdir['xivo-callerid'] = clid
                                if len(clid) > 7 and clid != '<unknown>':
                                        itemdir['xivo-tomatch-callerid'] = clid

                        # 2/4
                        # call a database for xivo-callerid matching (or another pattern to set somewhere)
                        dirlist = actionopt.get('directories')
                        if 'xivo-tomatch-callerid' in itemdir:
                                callingnum = itemdir['xivo-tomatch-callerid']
                                if dirlist is not None:
                                        for dirname in dirlist.split(','):
                                                if context in self.ctxlist.ctxlist and dirname in self.ctxlist.ctxlist[context]:
                                                        dirdef = self.ctxlist.ctxlist[context][dirname]
                                                        try:
                                                                y = self.__build_customers_bydirdef__(dirname, callingnum, dirdef)
                                                        except Exception, exc:
                                                                log.error('--- exception --- (xivo-tomatch-callerid : %s, %s) : %s'
                                                                          % (dirname, context, exc))
                                                                y = []
                                                        if len(y) > 0:
                                                                for g, gg in y[0].iteritems():
                                                                        itemdir[g] = gg
                                if callingnum[:2] == '00':
                                        internatprefix = callingnum[2:6]
                        # print where, itemdir

                        # 3/4
                        # build XML items from daemon-config + filled-in items
                        if 'xivo-channel' in itemdir:
                                linestosend.append('<internal name="channel"><![CDATA[%s]]></internal>'
                                                   % itemdir['xivo-channel'])
                        if 'xivo-uniqueid' in itemdir:
                                linestosend.append('<internal name="sessionid"><![CDATA[%s]]></internal>'
                                                   % itemdir['xivo-uniqueid'])
                        linestosend.extend(self.__build_xmlqtui__('sheet_qtui', actionopt, itemdir))
                        linestosend.extend(self.__build_xmlsheet__('action_info', actionopt, itemdir))
                        linestosend.extend(self.__build_xmlsheet__('sheet_info', actionopt, itemdir))
                        linestosend.extend(self.__build_xmlsheet__('systray_info', actionopt, itemdir))
                        linestosend.append('<internal name="kind"><![CDATA[%s]]></internal>' % where)
                        linestosend.append('</user></profile>')
                        
                        # print ''.join(linestosend)
                        
                        dozip = True
                        if dozip:
                                tosend = { 'class' : 'sheet',
                                           'compressed' : 'anything',
                                           'direction' : 'client',
                                           'payload' : base64.b64encode((chr(0) * 4) + zlib.compress(''.join(linestosend))) }
                        else:
                                tosend = { 'class' : 'sheet',
                                           'direction' : 'client',
                                           'payload' : base64.b64encode(''.join(linestosend)) }
                        fulllines = cjson.encode(tosend)
                        
                        # print '---------', where, whoms, fulllines
                        
                        # 4/4
                        # send the payload to the appropriate people
                        for whom in whoms.split(','):
                                if whom == 'dest':
                                        for userinfo in userinfos:
                                                self.__send_msg_to_cti_client__(userinfo, fulllines)
                                elif whom == 'subscribe':
                                        for uinfo in self.ulist_ng.keeplist.itervalues():
                                                if 'subscribe' in uinfo:
                                                        self.__send_msg_to_cti_client__(uinfo, fulllines)
                                elif whom == 'all':
                                        for uinfo in self.ulist_ng.keeplist.itervalues():
                                                if astid == uinfo.get('astid'):
                                                        self.__send_msg_to_cti_client__(uinfo, fulllines)
                                elif whom == 'reallyall':
                                        for uinfo in self.ulist_ng.keeplist.itervalues():
                                                self.__send_msg_to_cti_client__(uinfo, fulllines)
                                else:
                                        log.warning('__sheet_alert__ (%s) : unknown destination <%s> in <%s>'
                                                  % (astid, whom, where))
                return



        def __phoneid_from_channel__(self, astid, channel):
                ret = None
                tech = None
                phoneid = None
                # special cases : AsyncGoto/IAX2/asteriskisdn-13622<ZOMBIE>
                if channel.startswith('SIP/'):
                        tech = 'sip'
                        phoneid = channel[4:].split('-')[0]
                elif channel.startswith('IAX2/'):
                        tech = 'iax2'
                        phoneid = channel[5:].split('-')[0]
                if tech is not None and phoneid is not None:
                        for phoneref, b in self.weblist['phones'][astid].keeplist.iteritems():
                                if b['tech'] == tech and b['phoneid'] == phoneid:
                                        ret = phoneref
                # give also : userid ...
                return ret


        def __userinfo_from_phoneid__(self, astid, phoneid):
                uinfo = None
                for vv in self.ulist_ng.keeplist.itervalues():
                        if phoneid in vv['techlist'] and astid == vv['astid']:
                                uinfo = vv
                                break
                return uinfo
        
        def __userinfo_from_agentphonenum__(self, astid, phoneid):
                uinfo = None
                if phoneid is not None:
                        phonenum = phoneid.split('.')[3]
                        for vv in self.ulist_ng.keeplist.itervalues():
                                if astid == vv['astid'] and 'agentphonenum' in vv and phonenum == vv['agentphonenum']:
                                        uinfo = vv
                                        break
                return uinfo

        def __fill_uniqueids__(self, astid, uid1, uid2, chan1, chan2, where):
                if uid1 in self.uniqueids[astid] and chan1 == self.uniqueids[astid][uid1]['channel']:
                        self.uniqueids[astid][uid1].update({where : chan2,
                                                            'time-%s' % where : time.time()})
                if uid2 in self.uniqueids[astid] and chan2 == self.uniqueids[astid][uid2]['channel']:
                        self.uniqueids[astid][uid2].update({where : chan1,
                                                            'time-%s' % where : time.time()})
                return
        
        # Methods to handle Asterisk AMI events
        def ami_dial(self, astid, event):
                src     = event.get('Source')
                dst     = event.get('Destination')
                clid    = event.get('CallerID')
                clidn   = event.get('CallerIDName')
                uidsrc  = event.get('SrcUniqueID')
                uiddst  = event.get('DestUniqueID')
                phoneidsrc = self.__phoneid_from_channel__(astid, src)
                phoneiddst = self.__phoneid_from_channel__(astid, dst)
                uinfosrc = self.__userinfo_from_phoneid__(astid, phoneidsrc)
                uinfodst = self.__userinfo_from_phoneid__(astid, phoneiddst)
                self.__fill_uniqueids__(astid, uidsrc, uiddst, src, dst, 'dial')
                tosend = { 'class' : 'call',
                           'direction' : 'client',
                           'action' : 'dial' }
                if uinfosrc:
                        tosend.update({ 'caller' : { 'userid' : uinfosrc.get('user'),
                                                     'company' : uinfosrc.get('company'),
                                                     'phone' : phoneidsrc }
                                        })
                if uinfodst:
                        tosend.update({ 'called' : { 'userid' : uinfodst.get('user'),
                                                     'company' : uinfodst.get('company'),
                                                     'phone' : phoneiddst }
                                        })
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                
                
                # update the phones statuses
                self.weblist['phones'][astid].ami_dial(phoneidsrc, phoneiddst,
                                                       uidsrc, uiddst,
                                                       self.uniqueids[astid][uidsrc],
                                                       self.uniqueids[astid][uiddst])
                tosend = self.weblist['phones'][astid].status(phoneidsrc)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                tosend = self.weblist['phones'][astid].status(phoneiddst)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                
                return


        def treatsall(self, args):
                what = args.get('event')
                if what == 'channel-src':
                        pidsrc = self.__phoneid_from_channel__(astid, src)
                        piddst = self.__phoneid_from_channel__(astid, dst)
                        treatsall(self, 'phone-src', pidsrc)
                        treatsall(self, 'phone-dst', piddst)
                        pass
                elif what == 'phone-src':
                        pass
                elif what == 'agent-src':
                        pass
                return

        def read_queuelog(self, astid, url_queuelog):
                if astid not in self.stats_queues:
                        self.stats_queues[astid] = {}
                if url_queuelog is None:
                        return
                qlog = urllib.urlopen(url_queuelog)
                csvreader = csv.reader(qlog, delimiter = '|')
                time_now = int(time.time())
                time_1ha = time_now - 3600
                for line in csvreader:
                        qdate = int(line[0])
                        qaction = line[4]
                        qname = line[2]
                        if qdate > time_1ha and qaction in ['ENTERQUEUE', 'CONNECT', 'ABANDON', 'EXITEMPTY']:
                                if qname not in self.stats_queues[astid]:
                                        self.stats_queues[astid][qname] = {'ENTERQUEUE' : [],
                                                                           'CONNECT' : [],
                                                                           'ABANDON' : []}
                                if qaction == 'EXITEMPTY':
                                        qaction = 'ABANDON'
                                self.stats_queues[astid][qname][qaction].append(qdate)
                qlog.close()
                return

        def __clidlist_from_event__(self, chan1, chan2, clid1, clid2):
                # XXXX backport from autoescape, to be checked/improved/fixed
                LOCALNUMSIZE = 3
                clidlist = []
                if len(clid1) == LOCALNUMSIZE and clid1 not in clidlist:
                        clidlist.append(clid1)
                if len(clid2) == LOCALNUMSIZE and clid2 not in clidlist:
                        clidlist.append(clid2)
                if chan1.startswith('SIP/'):
                        num1 = chan1[4:].split('-')[0]
                        if len(num1) == LOCALNUMSIZE and num1 not in clidlist:
                                clidlist.append(num1)
                if chan2.startswith('SIP/'):
                        num2 = chan2[4:].split('-')[0]
                        if len(num2) == LOCALNUMSIZE and num2 not in clidlist:
                                clidlist.append(num2)
                return clidlist

        def __agentnum__(self, userinfo):
                astid = userinfo.get('astid')
                if 'agentid' in userinfo and userinfo['agentid']:
                        agent_id = userinfo['agentid']
                        if astid in self.weblist['agents'] and agent_id in self.weblist['agents'][astid].keeplist:
                                return self.weblist['agents'][astid].keeplist[agent_id].get('number')
                return ''

        
        def __update_queue_stats__(self, astid, queuename, fname = None):
                if astid not in self.stats_queues:
                        return
                if queuename not in self.stats_queues[astid]:
                        self.stats_queues[astid][queuename] = {'ENTERQUEUE' : [],
                                                               'CONNECT' : [],
                                                               'ABANDON' : []}
                time_now = int(time.time())
                time_1ha = time_now - 3600
                for fieldname in ['ENTERQUEUE', 'CONNECT', 'ABANDON']:
                        toremove = []
                        for t in self.stats_queues[astid][queuename][fieldname]:
                                if t < time_1ha:
                                        toremove.append(t)
                        for t in toremove:
                                self.stats_queues[astid][queuename][fieldname].remove(t)
                if fname:
                        self.stats_queues[astid][queuename][fname].append(time_now)
                        log.info('__update_queue_stats__ for %s %s %s' % (astid, queuename, fname))
                self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Join'] = len(self.stats_queues[astid][queuename]['ENTERQUEUE'])
                self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Link'] = len(self.stats_queues[astid][queuename]['CONNECT'])
                self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Lost'] = len(self.stats_queues[astid][queuename]['ABANDON'])
                nj = self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Join']
                nl = self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Link']
                if nj > 0:
                        self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Rate'] = (nl * 100) / nj
                else:
                        self.weblist['queues'][astid].keeplist[queuename]['stats']['Xivo-Rate'] = -1
                # log.info('__update_queue_stats__ %s %s : %s' % (astid, queuename,
                # self.weblist['queues'][astid].keeplist[queuename]['stats']))
                return
        

        def __ignore_dtmf__(self, astid, uid, where):
                ret = False
                if uid in self.ignore_dtmf[astid]:
                        tnow = time.time()
                        t0 = self.ignore_dtmf[astid][uid]['xivo-timestamp']
                        dt = tnow - t0
                        # allow 1s between each unlink/link event
                        if dt < 1:
                                # print 'ignore', where, dt
                                self.ignore_dtmf[astid][uid]['xivo-timestamp'] = tnow
                                ret = True
                return ret
        
        
        def ami_link(self, astid, event):
                chan1 = event.get('Channel1')
                chan2 = event.get('Channel2')
                clid1 = event.get('CallerID1')
                clid2 = event.get('CallerID2')
                uid1 = event.get('Uniqueid1')
                uid2 = event.get('Uniqueid2')
                if self.__ignore_dtmf__(astid, uid1, 'link'):
                        return
                if self.__ignore_dtmf__(astid, uid2, 'link'):
                        return
                self.__fill_uniqueids__(astid, uid1, uid2, chan1, chan2, 'link')
                uid1info = self.uniqueids[astid][uid1]
                
                if 'time-chanspy' in uid1info:
                        return
                
                if uid1info['link'].startswith('Agent/') and 'join' in uid1info:
                        queuename = uid1info['join'].get('queue')
                        log.info('STAT LINK %s %s %s' % (astid, queuename, uid1info['link']))
                        self.__update_queue_stats__(astid, queuename, 'CONNECT')
                        
                phoneid1 = self.__phoneid_from_channel__(astid, chan1)
                phoneid2 = self.__phoneid_from_channel__(astid, chan2)
                uinfo1 = self.__userinfo_from_phoneid__(astid, phoneid1)
                uinfo2 = self.__userinfo_from_phoneid__(astid, phoneid2)
                uinfo1_ag = self.__userinfo_from_agentphonenum__(astid, phoneid1)
                uinfo2_ag = self.__userinfo_from_agentphonenum__(astid, phoneid2)
                
                log.info('(phone) LINK %s phone=%s callerid=%s user=%s' % (astid, phoneid1, clid1, uinfo1))
                log.info('(phone) LINK %s phone=%s callerid=%s user=%s' % (astid, phoneid2, clid2, uinfo2))
                
                # update the phones statuses
                self.weblist['phones'][astid].ami_link(phoneid1, phoneid2,
                                                       uid1, uid2,
                                                       self.uniqueids[astid][uid1],
                                                       self.uniqueids[astid][uid2])
                tosend = self.weblist['phones'][astid].status(phoneid1)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                tosend = self.weblist['phones'][astid].status(phoneid2)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                
                if 'context' in self.uniqueids[astid][uid1]:
                        self.__sheet_alert__('link', astid, self.uniqueids[astid][uid1]['context'], event, {})

                if chan2.startswith('Agent/'):
                        msg = self.__build_agupdate__(['agentlink', astid, chan2])
                        self.__send_msg_to_cti_clients__(msg)
                        
                        # 'onlineincoming' for the agent
                        agent_number = chan2[6:]
                        status = 'onlineincoming'
                        for uinfo in self.__find_userinfos_by_agentnum__(astid, agent_number):
                                self.__update_availstate__(uinfo, status)
                                self.__presence_action__(astid, agent_number, uinfo.get('capaid'), status)
                        
                        # To identify which queue a call comes from, we match a previous AMI Leave event,
                        # that involved the same channel as the one catched here.
                        # Any less-tricky-method is welcome, though.
                        if chan1 in self.queues_channels_list[astid]:
                                qname = self.queues_channels_list[astid][chan1]
                                ## del self.queues_channels_list[astid][chan1]
                                extraevent = {'xivo_queuename' : qname}
                                self.__sheet_alert__('agentlinked', astid, DEFAULT_CONTEXT, event, extraevent)

                if uinfo1:
                        status = 'onlineoutgoing'
                        self.__update_availstate__(uinfo1, status)
                        ag = self.__agentnum__(uinfo1)
                        if ag:
                                self.__presence_action__(astid, ag, uinfo1.get('capaid'), status)
                                msg = self.__build_agupdate__(['phonelink', astid, 'Agent/%s' % ag])
                                self.__send_msg_to_cti_clients__(msg)

                if uinfo1_ag and uinfo1_ag != uinfo1:
                        status = 'onlineoutgoing'
                        self.__update_availstate__(uinfo1_ag, status)
                        ag = self.__agentnum__(uinfo1_ag)
                        if ag:
                                self.__presence_action__(astid, ag, uinfo1_ag.get('capaid'), status)
                                msg = self.__build_agupdate__(['phonelink', astid, 'Agent/%s' % ag])
                                self.__send_msg_to_cti_clients__(msg)

                if uinfo2:
                        status = 'onlineincoming'
                        self.__update_availstate__(uinfo2, status)
                        # self.__presence_action__(astid, ag, status)
                        # msg = self.__build_agupdate__(['phonelink', astid, 'Agent/%s' % ag])
                        # self.__send_msg_to_cti_clients__(msg)

                try:
                        self.weblist['phones'][astid].handle_ami_event_link(chan1, chan2, clid1, clid2)
                except Exception, exc:
                        pass
                return

        def ami_unlink(self, astid, event):
                chan1 = event.get('Channel1')
                chan2 = event.get('Channel2')
                clid1 = event.get('CallerID1')
                clid2 = event.get('CallerID2')
                uid1 = event.get('Uniqueid1')
                uid2 = event.get('Uniqueid2')
                if self.__ignore_dtmf__(astid, uid1, 'unlink'):
                        return
                if self.__ignore_dtmf__(astid, uid2, 'unlink'):
                        return
                self.__fill_uniqueids__(astid, uid1, uid2, chan1, chan2, 'unlink')
                uid1info = self.uniqueids[astid][uid1]
                phoneid1 = self.__phoneid_from_channel__(astid, chan1)
                phoneid2 = self.__phoneid_from_channel__(astid, chan2)
                uinfo1 = self.__userinfo_from_phoneid__(astid, phoneid1)
                uinfo2 = self.__userinfo_from_phoneid__(astid, phoneid2)
                uinfo1_ag = self.__userinfo_from_agentphonenum__(astid, phoneid1)
                uinfo2_ag = self.__userinfo_from_agentphonenum__(astid, phoneid2)
                
                log.info('(phone) UNLINK %s phone=%s callerid=%s user=%s' % (astid, phoneid1, clid1, uinfo1))
                log.info('(phone) UNLINK %s phone=%s callerid=%s user=%s' % (astid, phoneid2, clid2, uinfo2))
                
                # update the phones statuses
                self.weblist['phones'][astid].ami_unlink(phoneid1, phoneid2,
                                                         uid1, uid2,
                                                         self.uniqueids[astid][uid1],
                                                         self.uniqueids[astid][uid2])
                tosend = self.weblist['phones'][astid].status(phoneid1)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                tosend = self.weblist['phones'][astid].status(phoneid2)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                
                if uid1info['link'].startswith('Agent/') and 'join' in uid1info:
                        queuename = uid1info['join'].get('queue')
                        clength = {'Xivo-Wait' : uid1info['time-link'] - uid1info['time-newchannel'],
                                   'Xivo-Chat' : uid1info['time-unlink'] - uid1info['time-link']
                                   }
                        log.info('STAT UNLINK %s %s %s' % (astid, queuename, clength))
                        if astid in self.stats_queues:
                                if queuename not in self.stats_queues[astid]:
                                        self.stats_queues[astid][queuename] = {}
                                time_now = int(time.time())
                                time_1ha = time_now - 3600

                                for field in ['Xivo-Wait', 'Xivo-Chat']:
                                        if field not in self.stats_queues[astid][queuename]:
                                                self.stats_queues[astid][queuename].update({field : {}})
                                        self.stats_queues[astid][queuename][field][time_now] = clength[field]
                                        toremove = []
                                        for t in self.stats_queues[astid][queuename][field].keys():
                                                if t < time_1ha:
                                                        toremove.append(t)
                                        for t in toremove:
                                                del self.stats_queues[astid][queuename][field][t]
                                        ttotal = 0
                                        for val in self.stats_queues[astid][queuename][field].values():
                                                ttotal += val
                                        nvals = len(self.stats_queues[astid][queuename][field])
                                        if nvals > 0:
                                                self.weblist['queues'][astid].keeplist[queuename]['stats'][field] = int(round(ttotal / nvals))
                                        else:
                                                self.weblist['queues'][astid].keeplist[queuename]['stats'][field] = 0

                                tosend = { 'class' : 'queues',
                                           'function' : 'sendlist',
                                           'direction' : 'client',
                                           'payload' : [ { 'astid' : astid,
                                                           'queuestats' : self.weblist['queues'][astid].get_queuestats(queuename),
                                                           'vqueues' : self.weblist['vqueues'][astid].keeplist
                                                           } ] }
                                self.__send_msg_to_cti_clients__(cjson.encode(tosend))

                if 'context' in self.uniqueids[astid][uid1]:
                        self.__sheet_alert__('unlink', astid, self.uniqueids[astid][uid1]['context'], event, {})

                if chan2.startswith('Agent/'):
                        msg = self.__build_agupdate__(['agentunlink', astid, chan2])
                        self.__send_msg_to_cti_clients__(msg)
                        
                        # 'postcall' for the agent
                        agent_number = chan2[6:]
                        status = 'postcall'
                        for uinfo in self.__find_userinfos_by_agentnum__(astid, agent_number):
                                self.__update_availstate__(uinfo, status)
                                self.__presence_action__(astid, agent_number, uinfo.get('capaid'), status)
                                
                        if chan1 in self.queues_channels_list[astid]:
                                qname = self.queues_channels_list[astid][chan1]
                                del self.queues_channels_list[astid][chan1]
                                extraevent = {'xivo_queuename' : qname}
                                self.__sheet_alert__('agentunlinked', astid, DEFAULT_CONTEXT, event, extraevent)

                lstinfos = [uinfo1, uinfo2]
                if uinfo1_ag not in lstinfos:
                        lstinfos.append(uinfo1_ag)
                for uinfo in lstinfos:
                        if uinfo:
                                status = 'postcall'
                                self.__update_availstate__(uinfo, status)
                                ag = self.__agentnum__(uinfo)
                                if ag:
                                        self.__presence_action__(astid, ag, uinfo.get('capaid'), status)
                                        msg = self.__build_agupdate__(['phoneunlink', astid, 'Agent/%s' % ag])
                                        self.__send_msg_to_cti_clients__(msg)
                                        
                try:
                        self.weblist['phones'][astid].handle_ami_event_unlink(chan1, chan2, clid1, clid2)
                except Exception, exc:
                        pass
                return

        def __presence_action__(self, astid, anum, capaid, status):
                try:
                        if capaid not in self.capas:
                                return
                        presenceid = self.capas[capaid].presenceid
                        if presenceid not in self.presence_sections:
                                # useful in order to avoid internal presence keyword states to occur (postcall, incomingcall, ...)
                                return
                        presenceactions = self.presence_sections[presenceid].actions(status)
                        for paction in presenceactions:
                                params = paction.split('-')
                                if params[0] == 'queueadd' and len(params) > 2 and anum:
                                        self.__ami_execute__(astid, params[0], params[1], 'Agent/%s' % anum, params[2])
                                elif params[0] == 'queueremove' and len(params) > 1 and anum:
                                        self.__ami_execute__(astid, params[0], params[1], 'Agent/%s' % anum)
                                elif params[0] == 'queuepause' and len(params) > 1 and anum:
                                        self.__ami_execute__(astid, 'queuepause', params[1], 'Agent/%s' % anum, 'true')
                                elif params[0] == 'queueunpause' and len(params) > 1 and anum:
                                        self.__ami_execute__(astid, 'queuepause', params[1], 'Agent/%s' % anum, 'false')
                except Exception, exc:
                        log.error('--- exception --- (__presence_action__) %s %s %s %s : %s' % (astid, anum, capaid, status, exc))
                return
        
        def __ami_execute__(self, *args):
                actionid = self.amilist.execute(*args)
                if actionid is not None:
                        self.ami_requests[actionid] = args
                        return actionid
                
        def ami_dtmf(self, astid, event):
                digit = event.get('Digit')
                direction = event.get('Direction')
                channel = event.get('Channel')
                uid = event.get('Uniqueid')
                begin = (event.get('Begin') == 'Yes')
                if direction == 'Received':
                        log.info('ami_dtmf %s <%s> %s %s %s %s' % (astid, digit, channel, uid, begin, time.time()))
                        event['xivo-timestamp'] = time.time()
                        self.ignore_dtmf[astid][uid] = event
                return
        
        def ami_hangup(self, astid, event):
                chan  = event.get('Channel')
                uid = event.get('Uniqueid')
                cause = event.get('Cause-txt')
                phoneid = self.__phoneid_from_channel__(astid, chan)
                if uid in self.ignore_dtmf[astid]:
                        del self.ignore_dtmf[astid][uid]
                if uid in self.uniqueids[astid] and chan == self.uniqueids[astid][uid]['channel']:
                        self.uniqueids[astid][uid].update({'hangup' : chan,
                                                           'time-hangup' : time.time()})
                        # for v, vv in self.uniqueids[astid][uid].iteritems():
                        # print astid, uid, v, vv
                if 'context' in self.uniqueids[astid][uid]:
                        self.__sheet_alert__('hangup', astid, self.uniqueids[astid][uid]['context'], event)

                self.weblist['phones'][astid].ami_hangup(phoneid, uid)
                tosend = self.weblist['phones'][astid].status(phoneid)
                tosend['astid'] = astid
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                self.weblist['phones'][astid].clear(phoneid, uid)

                if chan in self.chans_incomingqueue or chan in self.chans_incomingdid:
                        print 'HANGUP : (%s) %s uid=%s %s' % (time.asctime(), astid, uid, chan)
                        if chan in self.chans_incomingqueue:
                                self.chans_incomingqueue.remove(chan)
                        if chan in self.chans_incomingdid:
                                self.chans_incomingdid.remove(chan)
                if astid in self.uniqueids and uid in self.uniqueids[astid]:
                        del self.uniqueids[astid][uid]
                return

        def amiresponse_success(self, astid, event):
                msg = event.get('Message')
                actionid = event.get('ActionID')
                if msg is None:
                        if actionid is not None:
                                if actionid in self.getvar_requests:
                                        variable = event.get('Variable')
                                        value = event.get('Value')
                                        if variable is not None and value is not None:
                                                log.info('AMI %s Response=Success (%s) : %s = %s (%s)'
                                                         % (astid, actionid, variable, value, self.getvar_requests[actionid]['channel']))
                                        del self.getvar_requests[actionid]
                        else:
                                log.warning('AMI %s Response=Success : event = %s' % (astid, event))
                elif msg == 'Extension Status':
                        # this is the reply to 'ExtensionState'
                        self.amiresponse_extensionstatus(astid, event)
                elif msg == 'Mailbox Message Count':
                        self.amiresponse_mailboxcount(astid, event)
                elif msg == 'Mailbox Status':
                        self.amiresponse_mailboxstatus(astid, event)
                elif msg in ['Channel status will follow',
                             'Parked calls will follow',
                             'Agents will follow',
                             'Queue status will follow',
                             'Authentication accepted',
                             'Variable Set',
                             'Attended transfer started',
                             'Channel Hungup',
                             'Originate successfully queued',
                             'Redirect successful',
                             'Started monitoring channel',
                             'Stopped monitoring channel',
                             'Added interface to queue',
                             'Removed interface from queue',
                             'Interface paused successfully',
                             'Interface unpaused successfully',
                             'Agent logged out',
                             'Agent logged in']:
                        if actionid in self.ami_requests:
                                # log.info('AMI %s Response=Success : (tracked) %s %s' % (astid, event, self.ami_requests[actionid]))
                                del self.ami_requests[actionid]
                        else:
                                log.info('AMI %s Response=Success : (tracked) %s' % (astid, event))
                else:
                        log.warning('AMI %s Response=Success : untracked message (%s) <%s>' % (astid, actionid, msg))
                return

        def amiresponse_error(self, astid, event):
                msg = event.get('Message')
                actionid = event.get('ActionID')
                if msg == 'Originate failed':
                        if actionid in self.faxes:
                                faxid = self.faxes[actionid]
                                log.warning('AMI %s : fax not sent %s %s %s %s'
                                            % (astid, faxid.size, faxid.number, faxid.hide, faxid.uinfo))
                                tosend = { 'class' : 'faxprogress',
                                           'direction' : 'client',
                                           'status' : 'ko',
                                           'reason' : 'orig' }
                                self.__send_msg_to_cti_client__(faxid.uinfo, cjson.encode(tosend))
                                del self.faxes[actionid]
                        else:
                                log.warning('AMI %s Response=Error : (%s) <%s>' % (astid, actionid, msg))
                elif msg == 'Authentication failed':
                        log.warning('AMI %s : Not allowed to connect to this Manager Interface' % astid)
                elif msg in ['No such channel',
                             'No such agent',
                             'Member not dynamic',
                             'Interface not found',
                             'Unable to add interface: Already there',
                             'Unable to remove interface from queue: No such queue',
                             'Unable to remove interface: Not there'] :
                        if actionid in self.ami_requests:
                                log.warning('AMI %s Response=Error : %s %s' % (astid, event, self.ami_requests[actionid]))
                                del self.ami_requests[actionid]
                        else:
                                log.warning('AMI %s Response=Error : %s' % (astid, event))
                else:
                        if actionid in self.ami_requests:
                                log.warning('AMI %s Response=Error : (unknown message) %s %s' % (astid, event, self.ami_requests[actionid]))
                                del self.ami_requests[actionid]
                        else:
                                log.warning('AMI %s Response=Error : (unknown message) %s' % (astid, event))
                return

        def amiresponse_mailboxcount(self, astid, event):
                [exten, context] = event.get('Mailbox').split('@')
                for userinfo in self.ulist_ng.keeplist.itervalues():
                        if 'phonenum' in userinfo and userinfo.get('phonenum') == exten and userinfo.get('astid') == astid:
                                if userinfo['mwi']:
                                        userinfo['mwi'][1] = event.get('OldMessages')
                                        userinfo['mwi'][2] = event.get('NewMessages')
                return

        def amiresponse_mailboxstatus(self, astid, event):
                [exten, context] = event.get('Mailbox').split('@')
                for userinfo in self.ulist_ng.keeplist.itervalues():
                        if 'phonenum' in userinfo and userinfo.get('phonenum') == exten and userinfo.get('astid') == astid:
                                if userinfo['mwi']:
                                        userinfo['mwi'][0] = event.get('Waiting')
                                        tosend = { 'class' : 'users',
                                                   'function' : 'update',
                                                   'direction' : 'client',
                                                   'user' : [userinfo.get('astid'),
                                                             userinfo.get('xivo_userid')],
                                                   'subclass' : 'mwi',
                                                   'payload' : userinfo.get('mwi')
                                                   }
                                        self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return


        sippresence = {
                '-2' : 'Removed',
                '-1' : 'Deactivated',
                '0'  : 'Ready',
                '1'  : 'InUse', # Calling OR Online
                '2'  : 'Busy',
                '4'  : 'Unavailable',
                '8'  : 'Ringing',
                '16' : 'OnHold' }
        
        def amiresponse_extensionstatus(self, astid, event):
                # 90 seconds are needed to retrieve ~ 9000 phone statuses from an asterisk (on daemon startup)
                status  = event.get('Status')
                hint    = event.get('Hint')
                context = event.get('Context')
                exten   = event.get('Exten')
                if hint:
                        phoneref = '.'.join([hint.split('/')[0].lower(), context,
                                             hint.split('/')[1], exten])
                        if phoneref in self.weblist['phones'][astid].keeplist:
                                self.weblist['phones'][astid].ami_extstatus(phoneref, status)
                                tosend = self.weblist['phones'][astid].status(phoneref)
                                tosend['astid'] = astid
                                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                else:
                        log.warning('%s : undefined hint for %s@%s' % (astid, exten, context))
                return

        def ami_extensionstatus(self, astid, event):
                """
                New status for a phone (SIP only ?) (depends on hint ?),
                emitted by asterisk without request.
                """
                exten   = event.get('Exten')
                context = event.get('Context')
                status  = event.get('Status')
                
                for phoneref, b in self.weblist['phones'][astid].keeplist.iteritems():
                        if b['number'] == exten and b['context'] == context:
                                self.weblist['phones'][astid].ami_extstatus(phoneref, status)
                                tosend = self.weblist['phones'][astid].status(phoneref)
                                tosend['astid'] = astid
                                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return


        def ami_channelreload(self, astid, event):
                """
                This is an Asterisk 1.4 event.
                A few variables are available, however most of them seem useless in the realtime case.
                """
                log.info('ami_channelreload %s : %s' % (astid, event))
                return

        def ami_aoriginatesuccess(self, astid, event):
                return
        def ami_originatesuccess(self, astid, event):
                return
        def ami_aoriginatefailure(self, astid, event):
                return
        def ami_originatefailure(self, astid, event):
                return
        
        def ami_originateresponse(self, astid, event):
                log.info('ami_originateresponse %s : %s' % (astid, event))
                uniqueid = event.get('Uniqueid')
                actionid = event.get('ActionID')
                if uniqueid in self.uniqueids[astid]:
                        self.uniqueids[astid][uniqueid].update({'time-originateresponse' : time.time(),
                                                                'actionid' : actionid})
                # {'Uniqueid': '1213955764.88', 'CallerID': '6101', 'Exten': '6101', 'CallerIDNum': '6101', 'Response': 'Success', 'Reason': '4', 'Context': 'ctx-callbooster-agentlogin', 'CallerIDName': 'operateur', 'Privilege': 'call,all', 'Event': 'OriginateResponse', 'Channel': 'SIP/102-081f6730'}
                return
        
        def ami_messagewaiting(self, astid, event):
                """
                Instead of updating the mwi fields here, we request the current status,
                since the event returned when someone has erased one's mailbox seems to be
                incomplete.
                """
                [exten, context] = event.get('Mailbox').split('@')
                self.__ami_execute__(astid, 'mailbox', exten, context)
                return

        def ami_newstate(self, astid, event):
                # print '(phone)', astid, self.__phoneid_from_channel__(astid, event.get('Channel')), event.get('State')
                # uniqueid = event.get('Uniqueid')
                # log.info('STAT NEWS %s %s %s %s %s' % (astid, time.time(), uniqueid, event.get('Channel'), event.get('State')))
                return

        def ami_newcallerid(self, astid, event):
                uniqueid = event.get('Uniqueid')
                if astid in self.uniqueids and uniqueid in self.uniqueids[astid]:
                        self.uniqueids[astid][uniqueid].update({'calleridname' : event.get('CallerIDName'),
                                                                'calleridnum'  : event.get('CallerID')})
                return

        def ami_newexten(self, astid, event):
                application = event.get('Application')
                uniqueid = event.get('Uniqueid')
                if application == 'Dial' and uniqueid in self.uniqueids[astid]:
                        self.uniqueids[astid][uniqueid]['context'] = event.get('Context')
                        self.uniqueids[astid][uniqueid]['time-newexten-dial'] = time.time()
                elif application == 'Macro' and uniqueid in self.uniqueids[astid]:
                        log.info('newexten Macro : %s %s %s %s %s' % (astid, uniqueid, event.get('Context'), event.get('AppData'), event.get('Extension')))
                self.__sheet_alert__('outgoing', astid, event.get('Context'), event)
                return

        def ami_newchannel(self, astid, event):
                # print event
                channel = event.get('Channel')
                uniqueid = event.get('Uniqueid')
                self.uniqueids[astid][uniqueid] = {'channel' : channel,
                                                   'time-newchannel' : time.time()}
                return

        def ami_parkedcallscomplete(self, astid, event):
                return
        
        def ami_parkedcall(self, astid, event):
                channel = event.get('Channel')
                cfrom   = event.get('From')
                exten   = event.get('Exten')
                timeout = event.get('Timeout')
                self.parkedcalls[astid][channel] = event
                tosend = { 'class' : 'parkcall',
                           'direction' : 'client',
                           'payload' : { 'status' : 'parkedcall',
                                         'args' : [astid, channel, cfrom, exten, timeout]}}
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return
        
        def ami_unparkedcall(self, astid, event):
                channel = event.get('Channel')
                cfrom   = event.get('From')
                exten   = event.get('Exten')
                tosend = { 'class' : 'parkcall',
                           'direction' : 'client',
                           'payload' : { 'status' : 'unparkedcall',
                                         'args' : [astid, channel, cfrom, exten]}}
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return
        
        def ami_parkedcallgiveup(self, astid, event):
                channel = event.get('Channel')
                exten   = event.get('Exten')
                tosend = { 'class' : 'parkcall',
                           'direction' : 'client',
                           'payload' : { 'status' : 'parkedcallgiveup',
                                         'args' : [astid, channel, exten]}}
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return
        
        def ami_parkedcalltimeout(self, astid, event):
                channel = event.get('Channel')
                exten   = event.get('Exten')
                tosend = { 'class' : 'parkcall',
                           'direction' : 'client',
                           'payload' : { 'status' : 'parkedcalltimeout',
                                         'args' : [astid, channel, exten]}}
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return
        
        def ami_agentlogin(self, astid, event):
                print 'AMI AgentLogin', astid, event
                return
        def ami_agentlogoff(self, astid, event):
                print 'AMI AgentLogoff', astid, event
                return

        def ami_agentcallbacklogin(self, astid, event):
                agent = event.get('Agent')
                loginchan_split = event.get('Loginchan').split('@')
                phonenum = loginchan_split[0]
                if len(loginchan_split) > 1:
                        context = loginchan_split[1]
                else:
                        context = DEFAULT_CONTEXT
                
                if astid in self.weblist['agents']:
                        agent_id = self.weblist['agents'][astid].reverse_index.get(agent)
                        self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'status': 'AGENT_IDLE',
                                                                                          'phonenum' : phonenum,
                                                                                          'context' : context})
                msg = self.__build_agupdate__(['agentlogin', astid, 'Agent/%s' % agent, phonenum])
                print 'ami_agentcallbacklogin', msg
                self.__send_msg_to_cti_clients__(msg)
                return

        def ami_agentcallbacklogoff(self, astid, event):
                agent = event.get('Agent')
                loginchan_split = event.get('Loginchan').split('@')
                phonenum = loginchan_split[0]
                if len(loginchan_split) > 1:
                        context = loginchan_split[1]
                else:
                        context = DEFAULT_CONTEXT
                if phonenum == 'n/a':
                        phonenum = AGENT_NO_PHONENUM
                
                if astid in self.weblist['agents']:
                        agent_id = self.weblist['agents'][astid].reverse_index.get(agent)
                        self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'status': 'AGENT_LOGGEDOFF',
                                                                                          'phonenum' : phonenum,
                                                                                          'context' : context})
                msg = self.__build_agupdate__(['agentlogout', astid, 'Agent/%s' % agent, phonenum])
                print 'ami_agentcallbacklogoff', msg
                self.__send_msg_to_cti_clients__(msg)
                return

        def ami_agentcalled(self, astid, event):
                log.info('ami_agentcalled %s : %s' % (astid, event))
                # {'Extension': 's', 'CallerID': 'unknown', 'Priority': '2', 'ChannelCalling': 'IAX2/test-13', 'Context': 'macro-incoming_queue_call', 'CallerIDName': 'Comm. ', 'AgentCalled': 'iax2/192.168.0.120/101'}
                return
        
        def ami_agentcomplete(self, astid, event):
                log.info('ami_agentcomplete %s : %s' % (astid, event))
                return
        
        def ami_agentdump(self, astid, event):
                log.info('ami_agentdump %s : %s' % (astid, event))
                return
        
        def ami_agentconnect(self, astid, event):
                log.info('ami_agentconnect %s : %s' % (astid, event))
                # {'Member': 'SIP/108', 'Queue': 'commercial', 'Uniqueid': '1215006134.1166', 'Privilege': 'agent,all', 'Holdtime': '9', 'Event': 'AgentConnect', 'Channel': 'SIP/108-08190098'}
                return
        
        def ami_agents(self, astid, event):
                agent = event.get('Agent')
                if astid in self.weblist['agents']:
                        loginchan_split = event.get('LoggedInChan').split('@')
                        phonenum = loginchan_split[0]
                        if len(loginchan_split) > 1:
                                context = loginchan_split[1]
                        else:
                                context = DEFAULT_CONTEXT
                        if phonenum == 'n/a':
                                phonenum = AGENT_NO_PHONENUM
                        agent_id = self.weblist['agents'][astid].reverse_index.get(agent)
                        if agent_id:
                                self.weblist['agents'][astid].keeplist[agent_id]['stats'].update( { 'status' : event.get('Status'),
                                                                                                    'phonenum' : phonenum,
                                                                                                    'context' : context,
                                                                                                    'name' : event.get('Name'),
                                                                                                    'loggedintime' : event.get('LoggedInTime'),
                                                                                                    'talkingto' : event.get('TalkingTo'),
                                                                                                    'recorded' : False,
                                                                                                    'link' : False
                                                                                                    } )
                        else:
                                log.warning('I received some statuses for an agent <%s> while the XIVO config does not seem to know it' % agent)
                return

        def ami_agentscomplete(self, astid, event):
                print 'AMI AgentsComplete', astid, event
                return

        # XIVO-WEBI: beg-data
        # "category"|"name"|"number"|"context"|"commented"
        # "queue"|"callcenter"|"330"|"proformatique"|"0"
        # ""|""|""|""|"0"
        # XIVO-WEBI: end-data
        def ami_queuecallerabandon(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuecallerabandon : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                uniqueid = event.get('Uniqueid')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuecallerabandon : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                log.info('STAT ABANDON %s %s %s' % (astid, queue, uniqueid))
                self.__update_queue_stats__(astid, queue, 'ABANDON')
                # Asterisk 1.4 event
                # {'Queue': 'qcb_00000', 'OriginalPosition': '1', 'Uniqueid': '1213891256.41', 'Privilege': 'agent,all', 'Position': '1', 'HoldTime': '2', 'Event': 'QueueCallerAbandon'}
                # it should then go to AMI leave and send the update
                return
        
        def ami_queueentry(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queueentry : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                position = event.get('Position')
                wait = int(event.get('Wait'))
                channel = event.get('Channel')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queueentry : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                calleridnum = None
                calleridname = None
                for v, vv in self.uniqueids[astid].iteritems():
                        if 'channel' in vv and vv['channel'] == channel:
                                calleridnum = vv.get('calleridnum')
                                calleridname = vv.get('calleridname')
                # print 'AMI QueueEntry', astid, queue, position, wait, channel, event
                self.weblist['queues'][astid].queueentry_update(queue, channel, position, wait, calleridnum, calleridname)
                self.__send_msg_to_cti_clients__(self.__build_queue_status__(astid, queue))
                return
        
        def ami_queuememberadded(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuememberadded : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                location = event.get('Location')
                paused = event.get('Paused')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuememberadded : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                self.weblist['queues'][astid].queuememberupdate(queue, location, event)
                msg = self.__build_agupdate__(['joinqueue', astid, location, queue, paused])
                self.__send_msg_to_cti_clients__(msg)
                return
        
        def ami_queuememberremoved(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuememberremoved : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                location = event.get('Location')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuememberremoved : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                self.weblist['queues'][astid].queuememberremove(queue, location)
                msg = self.__build_agupdate__(['leavequeue', astid, location, queue])
                self.__send_msg_to_cti_clients__(msg)
                return
        
        def __build_agupdate__(self, arrgs):
                if len(arrgs) < 3:
                        return
                action = arrgs[0]
                astid = arrgs[1]
                agent_channel = arrgs[2]
                
                if agent_channel.startswith('Agent/'):
                        agent = agent_channel[6:]
                        agent_id = self.weblist['agents'][astid].reverse_index.get(agent)
                        if action == 'phonelink' or action == 'agentlink':
                                self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'link' : True})
                        elif action == 'phoneunlink' or action == 'agentunlink':
                                self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'link' : False})
                
                tosend = { 'class' : 'agents',
                           'function' : 'update',
                           'direction' : 'client',
                           'action' : action,
                           'astid' : astid,
                           'agent_channel' : agent_channel,
                           'payload' : arrgs }
                return cjson.encode(tosend)
        
        def ami_queuememberstatus(self, astid, event):
                # print 'AMI_QUEUEMEMBERSTATUS', event
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuememberstatus : no queue list has been defined for %s' % astid)
                        return
                status = event.get('Status')
                queue = event.get('Queue')
                location = event.get('Location')
                paused = event.get('Paused')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuememberstatus : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                self.weblist['queues'][astid].queuememberupdate(queue, location, event)
                msg = self.__build_agupdate__(['queuememberstatus', astid, location, queue, status, paused])
                self.__send_msg_to_cti_clients__(msg)
                
                # status = 3 => ringing
                # status = 1 => do not ring anymore => the one who has not gone to '1' among the '3's is the one who answered ...
                # 5 is received when unavailable members of a queue are attempted to be joined ... use agentcallbacklogoff to detect exit instead
                # + Link
                return

        def ami_queuememberpaused(self, astid, event):
                print 'AMI_QUEUEMEMBERPAUSED', event
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuememberpaused : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                paused = event.get('Paused')
                location = event.get('Location')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuememberpaused : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                self.weblist['queues'][astid].queuememberupdate(queue, location, event)
                if location.startswith('Agent/'):
                        if paused == '0':
                                msg = self.__build_agupdate__(['unpaused', astid, location, queue])
                                self.__send_msg_to_cti_clients__(msg)
                        else:
                                msg = self.__build_agupdate__(['paused', astid, location, queue])
                                self.__send_msg_to_cti_clients__(msg)
                return

        def ami_queueparams(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queueparams : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                self.weblist['queues'][astid].update_queuestats(queue, event)
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queueparams : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                tosend = { 'class' : 'queues',
                           'function' : 'sendlist',
                           'direction' : 'client',
                           'payload' : [ { 'astid' : astid,
                                           'queuestats' : self.weblist['queues'][astid].get_queuestats(queue),
                                           'vqueues' : self.weblist['vqueues'][astid].keeplist
                                           } ] }
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                return

        def ami_queuemember(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuemember : no queue list has been defined for %s' % astid)
                        return
                queue = event.get('Queue')
                location = event.get('Location')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_queuemember : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                self.weblist['queues'][astid].queuememberupdate(queue, location, event)
                return

        def ami_queuestatuscomplete(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_queuestatuscomplete : no queue list has been defined for %s' % astid)
                        return
                print 'AMI QueueStatusComplete', astid
                for qname in self.weblist['queues'][astid].get_queues():
                        self.__ami_execute__(astid, 'sendcommand', 'Command', [('Command', 'show queue %s' % qname)])
                return

        def ami_userevent(self, astid, event):
                eventname = event.get('UserEvent')
                if eventname == 'DID':
                        self.__sheet_alert__('incomingdid', astid,
                                             event.get('XIVO_CONTEXT', DEFAULT_CONTEXT),
                                             event)
                elif eventname == 'Feature':
                        log.info('AMI %s UserEvent %s %s' % (astid, eventname, event))
                        repstr = { event.get('Function') : { 'enabled' : bool(int(event.get('Status'))),
                                                             'number' : event.get('Value') } }
                        userid = '%s/%s' % (astid, event.get('XIVO_USERID'))
                        tosend = { 'class' : 'features',
                                   'function' : 'update',
                                   'direction' : 'client',
                                   'userid' : userid,
                                   'payload' : repstr }
                        self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                        
                elif eventname == 'LocalCall':
                        log.info('AMI %s UserEvent %s %s' % (astid, eventname, event))
                        uniqueid = event.get('UNIQUEID')
                        appli = event.get('XIVO_ORIGAPPLI')
                        actionid = event.get('XIVO_ORIGACTIONID')
                        if uniqueid in self.uniqueids[astid]:
                                if appli == 'ChanSpy':
                                        self.uniqueids[astid][uniqueid].update({'time-chanspy' : time.time(),
                                                                                'actionid' : actionid})
                else:
                        log.info('AMI %s untracked UserEvent %s' % (astid, event))
                return

        def ami_faxsent(self, astid, event):
                filename = event.get('FileName')
                if filename and os.path.isfile(filename):
                        os.unlink(filename)
                        log.info('faxsent event handler : removed %s' % filename)

                if event.get('PhaseEStatus') == '0':
                        tosend = { 'class' : 'faxprogress',
                                   'direction' : 'client',
                                   'status' : 'ok' }
                else:
                        tosend = { 'class' : 'faxprogress',
                                   'direction' : 'client',
                                   'status' : 'ko',
                                   'reason' : event.get('PhaseEStatus') }
                repstr = cjson.encode(tosend)

                # 'FileName': '/var/spool/asterisk/fax/astfaxsend-q6yZAKTJvU-0x48be7930.tif'
                faxid = filename[len('/var/spool/asterisk/fax/astfaxsend-'):-4]
                if faxid in self.faxes:
                        self.__send_msg_to_cti_client__(self.faxes[faxid].uinfo, cjson.encode(tosend))
                        del self.faxes[faxid]
                return

        def ami_faxreceived(self, astid, event):
                log.info('%s : %s' % (astid, event))
                self.__sheet_alert__('faxreceived', astid, DEFAULT_CONTEXT, event)
                return

        def ami_meetmejoin(self, astid, event):
                meetmenum = event.get('Meetme')
                channel = event.get('Channel')
                num = event.get('Usernum')
                
                if astid not in self.meetme:
                        self.meetme[astid] = {}
                if meetmenum not in self.meetme[astid]:
                        self.meetme[astid][meetmenum] = []
                if channel not in self.meetme[astid][meetmenum]:
                        self.meetme[astid][meetmenum].append(channel)
                        tosend = { 'class' : 'meetme',
                                   'direction' : 'client',
                                   'payload' : [ 'join', astid, meetmenum, num, channel,
                                                 str(len(self.meetme[astid][meetmenum])) ]
                                   }
                        self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                else:
                        log.warning('%s : channel %s already in meetme %s' % (astid, channel, meetmenum))
                return

        def ami_meetmeleave(self, astid, event):
                meetmenum = event.get('Meetme')
                channel = event.get('Channel')
                num = event.get('Usernum')
                
                if astid not in self.meetme:
                        self.meetme[astid] = {}
                if meetmenum not in self.meetme[astid]:
                        self.meetme[astid][meetmenum] = []
                if channel in self.meetme[astid][meetmenum]:
                        self.meetme[astid][meetmenum].remove(channel)
                        tosend = { 'class' : 'meetme',
                                   'direction' : 'client',
                                   'payload' : [ 'leave', astid, meetmenum, num, channel,
                                                 str(len(self.meetme[astid][meetmenum])) ]
                                   }
                        self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                else:
                        log.warning('%s : channel %s not in meetme %s' % (astid, channel, meetmenum))
                return

        def ami_status(self, astid, event):
                state = event.get('State')
                if state == 'Up':
                        link = event.get('Link')
                        channel = event.get('Channel')
                        uniqueid = event.get('Uniqueid')
                        
                        seconds = event.get('Seconds')
                        priority = event.get('Priority')
                        context = event.get('Context')
                        extension = event.get('Extension')
                        print 'ami_status', astid, state, uniqueid, channel, link, '/', priority, context, extension, seconds
                        self.uniqueids[astid][uniqueid] = {'channel' : channel}
                        if link is None:
                                if channel in self.parkedcalls[astid]:
                                        print '-- this is a parked call', self.parkedcalls[astid][channel]
                        if context is not None:
                                if context == 'macro-meetme':
                                        actionid = self.amilist.execute(astid, 'getvar', channel, 'XIVO_MEETMENUMBER')
                                        self.getvar_requests[actionid] = {'channel' : channel, 'variable' : 'XIVO_MEETMENUMBER'}
                                elif context.startswith('macro-phonestatus'):
                                        # *10
                                        pass
                                elif context == 'macro-voicemsg':
                                        # *98
                                        pass
                                elif context == 'macro-user':
                                        # ami_status xivo-obelisk Up 1222872105.4001 SIP/fpotiquet-085d8238 IAX2/asteriskisdn-11652 / None None None None
                                        # ami_status xivo-obelisk Up 1222872013.3986 IAX2/asteriskisdn-11652 SIP/fpotiquet-085d8238 / 31 macro-user s 178
                                        pass
                                elif context == 'macro-outcall':
                                        # ami_status xivo-obelisk Up 1222872070.3997 SIP/115-085dd7b8 IAX2/asteriskisdn-9917 / 4 macro-outcall dial 121
                                        # ami_status xivo-obelisk Up 1222872070.3998 IAX2/asteriskisdn-9917 SIP/115-085dd7b8 / None None None None
                                        pass
                                elif context == 'macro-pickup':
                                        # *21
                                        pass
                                elif context == 'macro-recsnd':
                                        pass
                                elif context == 'macro-did':
                                        pass
                                elif context == 'macro-forward':
                                        pass
                                elif context == 'macro-voicemenu':
                                        pass
                elif state == 'Ring': # Caller
                        uniqueid = event.get('Uniqueid')
                        channel = event.get('Channel')
                        
                        seconds = event.get('Seconds')
                        context = event.get('Context')
                        extension = event.get('Extension')
                        print 'ami_status', astid, state, uniqueid, channel, seconds, context, extension
                        self.uniqueids[astid][uniqueid] = {'channel' : channel}
                elif state == 'Ringing': # Callee
                        uniqueid = event.get('Uniqueid')
                        channel = event.get('Channel')
                        print 'ami_status', astid, state, uniqueid, channel
                        self.uniqueids[astid][uniqueid] = {'channel' : channel}
                elif state == 'Rsrvd':
                        uniqueid = event.get('Uniqueid')
                        channel = event.get('Channel')
                        # Zap/pseudo-xxx is here when there is a meetme going on
                        print 'ami_status', astid, state, uniqueid, channel
                else:
                        print 'ami_status', astid, event
                return

        def ami_join(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_join : %s : no queue list has been defined' % astid)
                        return
                # print 'AMI Join (Queue)', event
                chan  = event.get('Channel')
                clid  = event.get('CallerID')
                clidname = event.get('CallerIDName')
                queue = event.get('Queue')
                count = event.get('Count')
                position = event.get('Position')
                uniqueid = event.get('Uniqueid')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_join : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                if uniqueid in self.uniqueids[astid]:
                        self.uniqueids[astid][uniqueid]['join'] = {'queue' : queue,
                                                                   'time' : time.time()}
                log.info('STAT JOIN %s %s %s %s' % (astid, queue, chan, uniqueid))
                self.__update_queue_stats__(astid, queue, 'ENTERQUEUE')
                self.__sheet_alert__('incomingqueue', astid, DEFAULT_CONTEXT, event)
                log.info('AMI Join (Queue) %s %s %s %s' % (astid, queue, chan, count))
                self.weblist['queues'][astid].queueentry_update(queue, chan, position, 0,
                                                                clid, clidname)
                event['Calls'] = count
                self.weblist['queues'][astid].update_queuestats(queue, event)
                tosend = { 'class' : 'queues',
                           'function' : 'update',
                           'direction' : 'client',
                           'payload' : [astid, queue, count] }
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                self.__ami_execute__(astid, 'sendqueuestatus', queue)
                self.__send_msg_to_cti_clients__(self.__build_queue_status__(astid, queue))
                return

        def ami_leave(self, astid, event):
                if astid not in self.weblist['queues']:
                        log.warning('ami_leave : no queue list has been defined for %s' % astid)
                        return
                # print 'AMI Leave (Queue)', event
                chan  = event.get('Channel')
                queue = event.get('Queue')
                count = event.get('Count')
                if queue not in self.weblist['queues'][astid].keeplist:
                        log.warning('ami_leave : %s : no such queue %s (probably mismatch asterisk/xivo)' % (astid, queue))
                        return
                
                log.info('AMI Leave (Queue) %s %s %s' % (queue, chan, count))
                
                self.weblist['queues'][astid].queueentry_remove(queue, chan)
                event['Calls'] = count
                self.weblist['queues'][astid].update_queuestats(queue, event)
                tosend = { 'class' : 'queues',
                           'function' : 'update',
                           'direction' : 'client',
                           'payload' : [astid, queue, count] }
                self.__send_msg_to_cti_clients__(cjson.encode(tosend))
                
                if astid not in self.queues_channels_list:
                        self.queues_channels_list[astid] = {}
                # always sets the queue information since it might not have been deleted
                self.queues_channels_list[astid][chan] = queue
                self.__ami_execute__(astid, 'sendqueuestatus', queue)
                self.__send_msg_to_cti_clients__(self.__build_queue_status__(astid, queue))
                return

        def ami_rename(self, astid, event):
                oldname = event.get('Oldname')
                newname = event.get('Newname')
                uid = event.get('Uniqueid')
                # when 103 intercepts the call from 101 to 102 :
                # INFO:xivocti:AMI Rename asterisk-clg 1222936526.527 SIP/103-081fce98 SIP/103-081fce98<MASQ> (success)
                # INFO:xivocti:AMI Rename asterisk-clg 1222936525.526 SIP/102-081d0ff8 SIP/103-081fce98 (success)
                # INFO:xivocti:AMI Rename asterisk-clg 1222936526.527 SIP/103-081fce98<MASQ> SIP/102-081d0ff8<ZOMBIE> (success)
                if uid in self.uniqueids[astid] and oldname == self.uniqueids[astid][uid]['channel']:
                        self.uniqueids[astid][uid] = {'channel' : newname}
                        log.info('AMI Rename %s %s %s %s (success)' % (astid, uid, oldname, newname))
                else:
                        log.info('AMI Rename %s %s %s %s (failure)' % (astid, uid, oldname, newname))
                return
        # END of AMI events

        def message_srv2clt(self, sender, message):
                tosend = { 'class' : 'message',
                           'direction' : 'client',
                           'payload' : [sender, message] }
                return cjson.encode(tosend)

        def dmessage_srv2clt(self, message):
                return self.message_srv2clt('daemon-announce', message)
        
        
        def phones_update(self, function, statusbase, statusextended):
                strupdate = ''
                if function in ['update', 'noupdate']:
                        tosend = { 'class' : 'phones',
                                   'function' : function,
                                   'direction' : 'client',
                                   'statusbase' : statusbase,
                                   'statusextended' : statusextended }
                        strupdate = cjson.encode(tosend)
                        self.__send_msg_to_cti_clients__(strupdate)
                return


        def manage_cticommand(self, userinfo, icommand):
                ret = None
                repstr = None
                astid    = userinfo.get('astid')
                username = userinfo.get('user')
                context  = userinfo.get('context')
                capaid   = userinfo.get('capaid')
                ucapa    = self.capas[capaid].all() # userinfo.get('capas')
                
                try:
                        if icommand.name == 'database':
                                if self.capas[capaid].match_funcs(ucapa, 'database'):
                                        repstr = database_update(me, icommand.args)
                        elif icommand.name == 'json':
                                classcomm = icommand.struct.get('class')
                                dircomm = icommand.struct.get('direction')
                                
                                if dircomm is not None and dircomm == 'xivoserver' and classcomm in self.commnames:
                                        log.info('command attempt %s from %s' % (classcomm, username))
                                        if classcomm not in ['keepalive', 'availstate', 'actionfiche']:
                                                self.__fill_user_ctilog__(userinfo, 'cticommand:%s' % classcomm)
                                        if classcomm == 'meetme':
                                                argums = icommand.struct.get('command')
                                                if self.capas[capaid].match_funcs(ucapa, 'conference'):
                                                        if argums[0] == 'kick':
                                                                astid = argums[1]
                                                                room = argums[2]
                                                                num = argums[3]
                                                                self.__ami_execute__(astid, 'sendcommand',
                                                                                     'Command', [('Command', 'meetme kick %s %s' % (room, num))])
                                        elif classcomm == 'history':
                                                if self.capas[capaid].match_funcs(ucapa, 'history'):
                                                        repstr = self.__build_history_string__(icommand.struct.get('peer'),
                                                                                               icommand.struct.get('size'),
                                                                                               icommand.struct.get('mode'))
                                        elif classcomm == 'directory-search':
                                                if self.capas[capaid].match_funcs(ucapa, 'directory'):
                                                        repstr = self.__build_customers__(context, icommand.struct.get('pattern'))

                                        elif classcomm == 'faxsend':
                                                if self.capas[capaid].match_funcs(ucapa, 'fax'):
                                                        newfax = cti_fax.Fax(userinfo,
                                                                             icommand.struct.get('size'),
                                                                             icommand.struct.get('number'),
                                                                             icommand.struct.get('hide'))
                                                        self.faxes[newfax.reference] = newfax
                                                        tosend = { 'class' : 'faxsend',
                                                                   'direction' : 'client',
                                                                   'tdirection' : 'upload',
                                                                   'fileid' : newfax.reference }
                                                        repstr = cjson.encode(tosend)
                                                        
                                        elif classcomm == 'availstate':
                                                if self.capas[capaid].match_funcs(ucapa, 'presence'):
                                                        # updates the new status and sends it to other people
                                                        repstr = self.__update_availstate__(userinfo, icommand.struct.get('availstate'))
                                                        self.__presence_action__(astid, self.__agentnum__(userinfo),
                                                                                 capaid,
                                                                                 icommand.struct.get('availstate'))
                                                        self.__fill_user_ctilog__(userinfo, 'cticommand:%s' % classcomm)

                                        elif classcomm == 'getguisettings':
                                                tosend = { 'class' : 'getguisettings',
                                                           'direction' : 'client',
                                                           'payload' : self.capas[capaid].guisettings
                                                           }
                                                repstr = cjson.encode(tosend)
                                                
                                        elif classcomm == 'message':
                                                if self.capas[capaid].match_funcs(ucapa, 'messages'):
                                                        self.__send_msg_to_cti_clients__(self.message_srv2clt('%s/%s' % (astid, username),
                                                                                                              '<%s>' % icommand.struct.get('message')))

                                        elif classcomm == 'featuresget':
                                                if self.capas[capaid].match_funcs(ucapa, 'features'):
##                                        if username in userlist[astid]:
##                                                userlist[astid][username]['monit'] = icommand.args
                                                        repstr = self.__build_features_get__(icommand.struct.get('userid'))

                                        elif classcomm == 'featuresput':
                                                if self.capas[capaid].match_funcs(ucapa, 'features'):
                                                        rep = self.__build_features_put__(icommand.struct.get('userid'),
                                                                                          icommand.struct.get('function'),
                                                                                          icommand.struct.get('value'))
                                                        self.__send_msg_to_cti_client__(userinfo, rep)
                                                        if 'destination' in icommand.struct:
                                                                rep = self.__build_features_put__(icommand.struct.get('userid'),
                                                                                                  'dest' + icommand.struct.get('function')[6:],
                                                                                                  icommand.struct.get('destination'))
                                                                self.__send_msg_to_cti_client__(userinfo, rep)

                                        elif classcomm == 'callcampaign':
                                                argums = icommand.struct.get('command')
                                                if argums[0] == 'fetchlist':
                                                        tosend = { 'class' : 'callcampaign',
                                                                   'direction' : 'client',
                                                                   'payload' : { 'command' : 'fetchlist',
                                                                                 'list' : [ '101', '102', '103' ] } }
                                                        repstr = cjson.encode(tosend)
                                                elif argums[0] == 'startcall':
                                                        exten = argums[1]
                                                        self.__originate_or_transfer__(userinfo,
                                                                                       [AMI_ORIGINATE, 'user:special:me', 'ext:%s' % exten])
                                                        tosend = { 'class' : 'callcampaign',
                                                                   'direction' : 'client',
                                                                   'payload' : { 'command' : 'callstarted',
                                                                                 'number' : exten } }
                                                        repstr = cjson.encode(tosend)
                                                elif argums[0] == 'stopcall':
                                                        tosend = { 'class' : 'callcampaign',
                                                                   'direction' : 'client',
                                                                   'payload' : { 'command' : 'callstopped',
                                                                                 'number' : argums[1] } }
                                                        repstr = cjson.encode(tosend)
                                                        # self.__send_msg_to_cti_client__(userinfo,
                                                        # '{'class':"callcampaign","direction":"client","command":"callnext","list":["%s"]}' % icommand.args[1])
                                        elif classcomm in ['originate', 'transfer', 'atxfer']:
                                                if self.capas[capaid].match_funcs(ucapa, 'dial'):
                                                        repstr = self.__originate_or_transfer__(userinfo,
                                                                                                [classcomm,
                                                                                                 icommand.struct.get('source'),
                                                                                                 icommand.struct.get('destination')])

                                        elif classcomm == 'hangup':
                                                if self.capas[capaid].match_funcs(ucapa, 'dial'):
                                                        repstr = self.__hangup__(userinfo,
                                                                                 icommand.struct.get('astid'),
                                                                                 icommand.struct.get('channel'),
                                                                                 True)

                                        elif classcomm == 'simplehangup':
                                                if self.capas[capaid].match_funcs(ucapa, 'dial'):
                                                        repstr = self.__hangup__(userinfo,
                                                                                 icommand.struct.get('astid'),
                                                                                 icommand.struct.get('channel'),
                                                                                 False)

                                        elif classcomm == 'pickup':
                                                if self.capas[capaid].match_funcs(ucapa, 'dial'):
                                                        # on Thomson, it picks up the last received call
                                                        self.__ami_execute__(icommand.struct.get('astid'),
                                                                             'sendcommand',
                                                                             'Command',
                                                                             [('Command',
                                                                               'sip notify event-talk %s' % icommand.struct.get('phonenum'))])

                                        elif classcomm == 'actionfiche':
                                                log.info('%s : %s' % (classcomm, icommand.struct))
                                                actionid = icommand.struct.get('buttonaction')[1]
                                                self.__fill_user_ctilog__(userinfo, 'cticommand:%s' % classcomm, icommand.struct.get('buttonaction')[0])
                                                if actionid in self.uniqueids[astid]:
                                                        log.info('%s : %s' % (classcomm, self.uniqueids[astid][actionid]))

                                        elif classcomm in ['phones', 'users', 'agents', 'queues']:
                                                function = icommand.struct.get('function')
                                                if function == 'getlist':
                                                        repstr = self.__getlist__(userinfo, classcomm)

                                        elif classcomm == 'agent':
                                                argums = icommand.struct.get('command')
                                                if self.capas[capaid].match_funcs(ucapa, 'agents'):
                                                        repstr = self.__agent__(userinfo, argums)

                                        elif classcomm == 'queue-status':
                                                # issued towards a user when he wants to monitor a new queue
                                                if self.capas[capaid].match_funcs(ucapa, 'agents'):
                                                        astid = icommand.struct.get('astid')
                                                        qname = icommand.struct.get('queuename')
                                                        repstr = self.__build_queue_status__(astid, qname)

                                        elif classcomm == 'agent-status':
                                                # issued by one user when he requests the status for one given agent
                                                if self.capas[capaid].match_funcs(ucapa, 'agents'):
                                                        astid = icommand.struct.get('astid')
                                                        agent_number = icommand.struct.get('agentid') # agent_number = userinfo.get('agentnum')
                                                        agent_channel = 'Agent/%s' % agent_number
                                                        if astid in self.weblist['queues'] and astid in self.weblist['agents']:
                                                                # lookup the logged in/out status of agent agent_number and sends it back to the requester
                                                                agent_id = self.weblist['agents'][astid].reverse_index.get(agent_number)
                                                                if agent_id in self.weblist['agents'][astid].keeplist:
                                                                        agprop = self.weblist['agents'][astid].keeplist[agent_id]['stats']
                                                                        tosend = { 'class' : 'agent-status',
                                                                                   'direction' : 'client',
                                                                                   'astid' : astid,
                                                                                   'agentnum' : agent_number,
                                                                                   'payload' : {'properties' : agprop,
                                                                                                'queues' : self.weblist['queues'][astid].get_queues_byagent(agent_channel)}
                                                                                   }
                                                                        repstr = cjson.encode(tosend)
                                                                else:
                                                                        log.warning('%s : agent_id <%s> not in agent list' % (astid, agent_id))
                                else:
                                        log.warning('unallowed json event %s' % icommand.struct)

                except Exception:
                        log.exception('--- exception --- (manage_cticommand) %s %s %s /'
                                      % (icommand.name, icommand.args, userinfo.get('login').get('connection')))
                        
                if repstr is not None: # might be useful to reply sth different if there is a capa problem for instance, a bad syntaxed command
                        try:
                                userinfo.get('login').get('connection').sendall(repstr + '\n')
                        except Exception, exc:
                                log.error('--- exception --- (sendall) attempt to send <%s ...> (%d chars) failed : %s'
                                          % (repstr[:40], len(repstr), exc))
                return ret


        def __build_history_string__(self, requester_id, nlines, kind):
                [company, userid] = requester_id.split('/')
                userinfo = self.ulist_ng.finduser(userid, company)
                astid = userinfo.get('astid')
                termlist = userinfo.get('techlist')
                reply = []
                for termin in termlist:
                        [techno, ctx, phoneid, exten] = termin.split('.')
                        print '__build_history_string__', requester_id, nlines, kind, techno, phoneid
                        try:
                                hist = self.__update_history_call__(self.configs[astid], techno, phoneid, nlines, kind)
                                for x in hist:
                                        try:
                                                ry1 = HISTSEPAR.join([x[0].isoformat(),
                                                                      x[1].replace('"', ''),
                                                                      str(x[10]),
                                                                      x[11]])
                                        except:
                                                ry1 = HISTSEPAR.join([x[0],
                                                                      x[1].replace('"', ''),
                                                                      str(x[10]),
                                                                      x[11]])
                                        if kind == '0':
                                                num = x[3].replace('"', '')
                                                cidname = num
                                                ry2 = HISTSEPAR.join([cidname, 'OUT', termin])
                                        else:   # display callerid for incoming calls
                                                ry2 = HISTSEPAR.join([x[1].replace('"', ''), 'IN', termin])
                                                
                                        reply.append(HISTSEPAR.join([ry1, ry2]))
                        except Exception, exc:
                                log.error('--- exception --- error : history : (client %s, termin %s) : %s'
                                          % (requester_id, termin, exc))

                if len(reply) > 0:
                        # sha1sum = sha.sha(''.join(reply)).hexdigest()
                        tosend = { 'class' : 'history',
                                   'direction' : 'client',
                                   'payload' : reply}
                        return cjson.encode(tosend)
                else:
                        return


        def __build_queue_status__(self, astid, qname):
                if astid in self.weblist['queues'] and qname in self.weblist['queues'][astid].keeplist:
                        tosend = { 'class' : 'queue-status',
                                   'direction' : 'client',
                                   'astid' : astid,
                                   'queuename' : qname,
                                   'payload' : { 'agents' : self.weblist['queues'][astid].keeplist[qname]['agents'],
                                                 'entries' : self.weblist['queues'][astid].keeplist[qname]['channels'] } }
                        cjsonenc = cjson.encode(tosend)
                        log.info('__build_queue_status__ : %s' % cjsonenc)
                        return cjsonenc
                else:
                        return None
                

        
        def __find_agentid_by_agentnum__(self, astid, agent_number):
                agent_id = None
                if astid in self.weblist['agents']:
                        agent_id = self.weblist['agents'][astid].reverse_index.get(agent_number)
                return agent_id
        
        def __find_channel_by_agentnum__(self, astid, agent_number):
                chans = []
                for uinfo in self.__find_userinfos_by_agentnum__(astid, agent_number):
                        techref = uinfo.get('techlist')[0]
                        for v, vv in self.uniqueids[astid].iteritems():
                                if 'channel' in vv:
                                        if techref == self.__phoneid_from_channel__(astid, vv['channel']):
                                                chans.append(vv['channel'])
                return chans
        
        def __find_userinfos_by_agentnum__(self, astid, agent_number):
                userinfos = []
                agent_id = self.__find_agentid_by_agentnum__(astid, agent_number)
                for uinfo in self.ulist_ng.keeplist.itervalues():
                        if 'agentid' in uinfo and uinfo.get('agentid') == agent_id and uinfo.get('astid') == astid:
                                userinfos.append(uinfo)
                return userinfos
        
        
        
        def __agent__(self, userinfo, commandargs):
                myastid = None
                myagentnum = None
                if 'agentid' in userinfo:
                        myastid = userinfo['astid']
                        myagentnum = self.__agentnum__(userinfo)

                subcommand = commandargs[0]
                if subcommand == 'leave':
                        if len(commandargs) > 1:
                                queuenames = commandargs[1].split(',')
                                if len(commandargs) > 3:
                                        astid = commandargs[2]
                                        anum = commandargs[3]
                                else:
                                        astid = myastid
                                        anum = myagentnum
                                if astid is not None and anum:
                                        for queuename in queuenames:
                                                self.__ami_execute__(astid, 'queueremove', queuename, 'Agent/%s' % anum)
                elif subcommand == 'join':
                        if len(commandargs) > 1:
                                queuenames = commandargs[1].split(',')
                                if len(commandargs) > 4:
                                        astid = commandargs[2]
                                        anum = commandargs[3]
                                        if commandargs[4] == 'pause':
                                                spause = 'true'
                                        else:
                                                spause = 'false'
                                else:
                                        astid = myastid
                                        anum = myagentnum
                                        spause = 'false' # unpauses by default for user-requests
                                if astid is not None and anum:
                                        for queuename in queuenames:
                                                self.__ami_execute__(astid, 'queueadd', queuename, 'Agent/%s' % anum, spause)
                elif subcommand == 'pause':
                        if len(commandargs) > 1:
                                queuenames = commandargs[1].split(',')
                                if len(commandargs) > 3:
                                        astid = commandargs[2]
                                        anum = commandargs[3]
                                else:
                                        astid = myastid
                                        anum = myagentnum
                                if astid is not None and anum:
                                        for queuename in queuenames:
                                                self.__ami_execute__(astid, 'queuepause', queuename, 'Agent/%s' % anum, 'true')
                elif subcommand == 'unpause':
                        if len(commandargs) > 1:
                                queuenames = commandargs[1].split(',')
                                if len(commandargs) > 3:
                                        astid = commandargs[2]
                                        anum = commandargs[3]
                                else:
                                        astid = myastid
                                        anum = myagentnum
                                if astid is not None and anum:
                                        for queuename in queuenames:
                                                self.__ami_execute__(astid, 'queuepause', queuename, 'Agent/%s' % anum, 'false')
                elif subcommand in ['login', 'logout', 'record', 'stoprecord', 'getfile', 'getfilelist', 'listen']:
                        if len(commandargs) > 2:
                                astid = commandargs[1]
                                anum = commandargs[2]
                                uinfo = None
                                agent_id = self.__find_agentid_by_agentnum__(astid, anum)
                                for uinfo_iter in self.ulist_ng.keeplist.itervalues():
                                        if 'agentid' in uinfo_iter and uinfo_iter.get('agentid') == agent_id and uinfo_iter.get('astid') == astid:
                                                uinfo = uinfo_iter
                                                break
                        else:
                                uinfo = userinfo

                        if subcommand == 'login':
                                self.__login_agent__(uinfo)
                        elif subcommand == 'logout':
                                self.__logout_agent__(uinfo)
                        elif subcommand == 'record':
                                datestring = time.strftime('%Y%m%d%H%M%S', time.localtime())
                                channels = self.__find_channel_by_agentnum__(astid, anum)
                                for channel in channels:
                                        self.__ami_execute__(astid, 'monitor', channel, 'cti-%s-%s' % (datestring, anum))
                                        agent_id = self.weblist['agents'][astid].reverse_index.get(anum)
                                        self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'recorded' : True})
                                        log.info('started monitor on %s %s (agent %s)' % (astid, channel, anum))
                                        tosend = { 'class' : 'agentrecord',
                                                   'direction' : 'client',
                                                   'agentnum' : anum,
                                                   'status' : 'started' }
                                        return cjson.encode(tosend)

                        elif subcommand == 'stoprecord':
                                channels = self.__find_channel_by_agentnum__(astid, anum)
                                for channel in channels:
                                        self.__ami_execute__(astid, 'stopmonitor', channel)
                                        agent_id = self.weblist['agents'][astid].reverse_index.get(anum)
                                        self.weblist['agents'][astid].keeplist[agent_id]['stats'].update({'recorded' : False})
                                        log.info('stopped monitor on %s %s (agent %s)' % (astid, channel, anum))
                                        tosend = { 'class' : 'agentrecord',
                                                   'direction' : 'client',
                                                   'agentnum' : anum,
                                                   'status' : 'stopped' }
                                        return cjson.encode(tosend)

                        elif subcommand == 'listen':
                                channels = self.__find_channel_by_agentnum__(astid, anum)
                                for channel in channels:
                                        aid = self.__ami_execute__(astid,
                                                                   'origapplication',
                                                                   'ChanSpy',
                                                                   '%s|q' % channel,
                                                                   'Local',
                                                                   userinfo.get('phonenum'),
                                                                   userinfo.get('context'))
                                        log.info('started listening on %s %s (agent %s) aid = %s' % (astid, channel, anum, aid))

                        elif subcommand == 'getfilelist':
                                lst = os.listdir(MONITORDIR)
                                monitoredfiles = []
                                for monitoredfile in lst:
                                        if monitoredfile.startswith('cti-') and monitoredfile.endswith('%s.wav' % anum):
                                                monitoredfiles.append(monitoredfile)
                                tosend = { 'class' : 'filelist',
                                           'direction' : 'client',
                                           'filelist' : monitoredfiles }
                                return cjson.encode(tosend)
                        
                        elif subcommand == 'getfile':
                                filename = '%s/%s' % (MONITORDIR, commandargs[3])
                                fileid = ''.join(random.sample(__alphanums__, 10))
                                self.filestodownload[fileid] = filename
                                tosend = { 'class' : 'faxsend',
                                           'direction' : 'client',
                                           'tdirection' : 'download',
                                           'fileid' : fileid }
                                return cjson.encode(tosend)

                elif subcommand == 'lists':
                        pass
                else:
                        log.warning('__agent__ : unknown subcommand %s' % subcommand)
                return


        def __login_agent__(self, uinfo):
                if uinfo is None:
                        return
                astid = uinfo.get('astid')
                # if phonenum is None:
                # phonenum = userinfo.get('phonenum')
                if 'agentid' in uinfo and 'agentphonenum' in uinfo and astid is not None:
                        agentnum = self.__agentnum__(uinfo)
                        agentphonenum = uinfo['agentphonenum']
                        if agentnum and agentphonenum:
                                agprops = self.weblist['agents'][astid].keeplist[uinfo['agentid']]
                                wrapuptime = agprops.get('wrapuptime')
                                self.__ami_execute__(astid, 'agentcallbacklogin',
                                                     agentnum, agentphonenum,
                                                     agprops.get('context'), agprops.get('ackcall'))
                                # chan_agent.c:2318 callback_deprecated: AgentCallbackLogin is deprecated and will be removed in a future release.
                                # chan_agent.c:2319 callback_deprecated: See doc/queues-with-callback-members.txt for an example of how to achieve
                                # chan_agent.c:2320 callback_deprecated: the same functionality using only dialplan logic.
                                self.__ami_execute__(astid, 'setvar', 'AGENTBYCALLERID_%s' % agentphonenum, agentnum)
                                self.__fill_user_ctilog__(uinfo, 'agent_login')
                return

        def __logout_agent__(self, uinfo):
                if uinfo is None:
                        return
                astid = uinfo.get('astid')
                if 'agentid' in uinfo and astid is not None:
                        agentnum = self.__agentnum__(uinfo)
                        if 'agentphonenum' in uinfo:
                                agentphonenum = uinfo['agentphonenum']
                                if agentphonenum:
                                        self.__ami_execute__(astid, 'setvar', 'AGENTBYCALLERID_%s' % agentphonenum, '')
                                        # del uinfo['agentphonenum']
                        if agentnum:
                                self.__ami_execute__(astid, 'agentlogoff', agentnum)
                                self.__fill_user_ctilog__(uinfo, 'agent_logout')
                return


        def logout_all_agents(self):
                for userinfo in self.ulist_ng.keeplist.itervalues():
                        self.__logout_agent__(userinfo)
                return


        def regular_update(self):
                """
                Define here the tasks one would like to complete on a regular basis.
                """
                try:
                        ntime = time.localtime()
                        thour = ntime[3]
                        tmin = ntime[4]
                        if 'regupdate' in self.xivoconf:
                                regactions = self.xivoconf['regupdate'].split(';')
                                if len(regactions) == 3 and thour == int(regactions[0]) and tmin < int(regactions[1]):
                                        log.info('(%2d h %2d min) => %s' % (thour, tmin, regactions[2]))
                                        if regactions[2] == 'logoutagents':
                                                self.logout_all_agents()
                                else:
                                        log.info('(%2d h %2d min) => no action' % (thour, tmin))
                except Exception, exc:
                        log.error('--- exception --- (regular update) : %s' % exc)


        def __getlist__(self, userinfo, ccomm):
                capaid = userinfo.get('capaid')
                ucapa = self.capas[capaid].all()
                if ccomm == 'users':
                        # XXX define capas ?
                        fullstat = []
                        for uinfo in self.ulist_ng.keeplist.itervalues():
                                icapaid = uinfo.get('capaid')
                                statedetails = {'color' : 'grey',
                                                'longname' : PRESENCE_UNKNOWN,
                                                'stateid' : 'xivo_unknown'}
                                if icapaid and icapaid in self.capas:
                                        presenceid = self.capas[uinfo.get('capaid')].presenceid
                                        if presenceid in self.presence_sections:
                                                if uinfo.get('state') in self.presence_sections[presenceid].displaydetails:
                                                        statedetails = self.presence_sections[presenceid].displaydetails[uinfo.get('state')]
                                                else:
                                                        log.warning('%s not in details for %s' % (uinfo.get('state'), presenceid))
                                        else:
                                                log.warning('%s not in presence_sections' % presenceid)
                                else:
                                        log.warning('%s not in capas' % icapaid)
                                fullstat.append([uinfo.get('user'),
                                                 uinfo.get('company'),
                                                 uinfo.get('fullname'),
                                                 statedetails,
                                                 uinfo.get('astid'),
                                                 uinfo.get('context'),
                                                 uinfo.get('phonenum'),
                                                 uinfo.get('techlist'),
                                                 self.__agentnum__(uinfo),
                                                 uinfo.get('mwi'),
                                                 uinfo.get('xivo_userid')
                                                 ])
                elif ccomm == 'phones':
                        # XXX define capas ?
                        fullstat = {}
                        try:
                                for astid, iplist in self.weblist['phones'].iteritems():
                                        fullstat[astid] = iplist.keeplist
                        except Exception, exc:
                                log.error('--- exception --- (phones) %s' % exc)
                elif ccomm == 'agents':
                        fullstat = []
                        if self.capas[capaid].match_funcs(ucapa, 'agents'):
                                for astid, aglist in self.weblist['agents'].iteritems():
                                        if astid in self.weblist['queues']:
                                                newlst = {}
                                                for agent_id, agprop in aglist.keeplist.iteritems():
                                                        agent_number = agprop['number']
                                                        agent_channel = 'Agent/%s' % agent_number
                                                        newlst[agent_number] = { 'properties' : agprop['stats'],
                                                                                 'queues' : self.weblist['queues'][astid].get_queues_byagent(agent_channel)
                                                                                 }
                                                fullstat.append({ 'astid' : astid,
                                                                  'newlist' : newlst })
                elif ccomm == 'queues':
                        fullstat = []
                        if self.capas[capaid].match_funcs(ucapa, 'agents'):
                                for astid, qlist in self.weblist['queues'].iteritems():
                                        fullstat.append({ 'astid' : astid,
                                                          'queuestats' : qlist.get_queuestats_long(),
                                                          'vqueues' : self.weblist['vqueues'][astid].keeplist
                                                          })
                tosend = { 'class' : ccomm,
                           'function' : 'sendlist',
                           'direction' : 'client',
                           'payload' : fullstat }
                return cjson.encode(tosend)


        # \brief Builds the features_get reply.
        def __build_features_get__(self, userid):
                userinfo = self.ulist_ng.keeplist[userid]
                user = userinfo.get('user')
                astid = userinfo.get('astid')
                company = userinfo.get('company')
                context = userinfo.get('context')
                srcnum = userinfo.get('phonenum')
                phoneid = userinfo.get('phoneid')
                repstr = {}
                
                cursor = self.configs[astid].userfeatures_db_conn.cursor()
                params = [srcnum, phoneid, context]
                query = 'SELECT ${columns} FROM userfeatures WHERE number = %s AND name = %s AND context = %s'

                for key in ['enablevoicemail', 'callrecord', 'callfilter', 'enablednd']:
                        try:
                                columns = (key,)
                                cursor.query(query, columns, params)
                                results = cursor.fetchall()
                                if len(results) > 0:
                                        repstr[key] = {'enabled' : bool(results[0][0])}
                        except Exception:
                                log.exception('--- exception --- features_get(bool) id=%s key=%s' % (userid, key))
                for key in ['unc', 'busy', 'rna']:
                        try:
                                columns = ('enable' + key,)
                                cursor.query(query, columns, params)
                                resenable = cursor.fetchall()

                                columns = ('dest' + key,)
                                cursor.query(query, columns, params)
                                resdest = cursor.fetchall()

                                if len(resenable) > 0 and len(resdest) > 0:
                                        repstr[key] = { 'enabled' : bool(resenable[0][0]),
                                                        'number' : resdest[0][0] }
                        except Exception:
                                log.exception('--- exception --- features_get(str) id=%s key=%s' % (userid, key))
                tosend = { 'class' : 'features',
                           'function' : 'get',
                           'direction' : 'client',
                           'userid' : userid,
                           'payload' : repstr }
                return cjson.encode(tosend)
        
        
        # \brief Builds the features_put reply.
        def __build_features_put__(self, userid, key, value):
                userinfo = self.ulist_ng.keeplist[userid]
                user = userinfo.get('user')
                astid = userinfo.get('astid')
                company = userinfo.get('company')
                context = userinfo.get('context')
                srcnum = userinfo.get('phonenum')
                phoneid = userinfo.get('phoneid')
                try:
                        query = 'UPDATE userfeatures SET ' + key + ' = %s WHERE number = %s AND name = %s AND context = %s'
                        params = [value, srcnum, phoneid, context]
                        cursor = self.configs[astid].userfeatures_db_conn.cursor()
                        cursor.query(query, parameters = params)
                        self.configs[astid].userfeatures_db_conn.commit()
                        tosend = { 'class' : 'features',
                                   'function' : 'put',
                                   'direction' : 'client',
                                   'payload' : [ userid, 'OK', key, value ] }
                except Exception, exc:
                        log.error('--- exception --- features_put id=%s %s %s : %s'
                                  % (userid, key, value, exc))
                        tosend = { 'class' : 'features',
                                   'function' : 'put',
                                   'direction' : 'client',
                                   'payload' : [ userid, 'KO' ] }
                return cjson.encode(tosend)


        # \brief Originates / transfers.
        def __originate_or_transfer__(self, userinfo, commargs):
                log.info('%s %s' % (userinfo, commargs))
                if len(commargs) != 3:
                        return
                [commname, src, dst] = commargs
                srcsplit = src.split(':', 1)
                dstsplit = dst.split(':', 1)

                if commname == 'originate' and len(srcsplit) == 2 and len(dstsplit) == 2:
                        [typesrc, whosrc] = srcsplit
                        [typedst, whodst] = dstsplit

                        # others than 'user:special:me' should only be allowed to switchboard-like users
                        if typesrc == 'user':
                                if whosrc == 'special:me':
                                        srcuinfo = userinfo
                                else:
                                        srcuinfo = self.ulist_ng.finduser(whosrc.split('/')[1], whosrc.split('/')[0])
                                if srcuinfo is not None:
                                        astid_src = srcuinfo.get('astid')
                                        context_src = srcuinfo.get('context')
                                        proto_src = 'SIP'
                                        # 'local' might break the XIVO_ORIGSRCNUM mechanism (trick for thomson)
                                        # XXX (+ dialplan) since 'SIP' is not the solution either
                                        
                                        phonenum_src = srcuinfo.get('phonenum')
                                        # if termlist empty + agentphonenum not empty => call this one
                                        cidname_src = srcuinfo.get('fullname')
                                        
                        elif typesrc == 'term':
                                pass
                        else:
                                log.warning('unknown typesrc <%s>' % typesrc)

                        # dst
                        if typedst == 'ext':
                                context_dst = context_src
                                # this string will appear on the caller's phone, before he calls someone
                                # for internal calls, one could solve the name of the called person,
                                # but it could be misleading with an incoming call from the given person
                                cidname_dst = 'direct number'
                                if whodst == 'special:parkthecall':
                                        exten_dst = self.configs[astid_src].parkingnumber
                                else:
                                        exten_dst = whodst
                        elif typedst == 'user':
                                if whodst == 'special:me':
                                        dstuinfo = userinfo
                                elif whodst == 'special:intercept':
                                        dstuinfo = {'phonenum' : '*8',
                                                    'fullname' : 'intercept',
                                                    'context' : 'parkedcalls'}
                                else:
                                        dstuinfo = self.ulist_ng.finduser(whodst.split('/')[1], whodst.split('/')[0])
                                if dstuinfo is not None:
                                        exten_dst = dstuinfo.get('phonenum')
                                        cidname_dst = dstuinfo.get('fullname')
                                        context_dst = dstuinfo.get('context')
                        elif typedst == 'term':
                                pass
                        else:
                                log.warning('unknown typedst <%s>' % typedst)


                        ret = False
                        try:
                                if len(exten_dst) > 0:
                                        ret = self.__ami_execute__(astid_src, AMI_ORIGINATE,
                                                                   proto_src, phonenum_src, cidname_src,
                                                                   exten_dst, cidname_dst,  context_dst)
                        except Exception, exc:
                                log.error('--- exception --- unable to originate ... %s' % exc)
                        if ret:
                                ret_message = 'originate OK'
                        else:
                                ret_message = 'originate KO'
                elif commname in ['transfer', 'atxfer']:
                        [typesrc, whosrc] = srcsplit
                        [typedst, whodst] = dstsplit

                        if typesrc == 'chan':
                                if whosrc.startswith('special:me:'):
                                        srcuinfo = userinfo
                                        chan_src = whosrc[len('special:me:'):]
                                if srcuinfo is not None:
                                        astid_src = srcuinfo.get('astid')
                                        context_src = srcuinfo.get('context')
                                        proto_src = 'local'
                                        phonenum_src = srcuinfo.get('phonenum')
                                        # if termlist empty + agentphonenum not empty => call this one
                                        cidname_src = srcuinfo.get('fullname')

                        if typedst == 'ext':
                                if whodst == 'special:parkthecall':
                                        exten_dst = self.configs[astid_src].parkingnumber
                                else:
                                        exten_dst = whodst

                        print astid_src, commname, chan_src, exten_dst, context_src
                        ret = False
                        try:
                                if len(exten_dst) > 0:
                                        ret = self.__ami_execute__(astid_src, commname,
                                                                   chan_src,
                                                                   exten_dst, context_src)
                        except Exception, exc:
                                log.error('--- exception --- unable to %s ... %s' % (commname, exc))
                        if ret:
                                ret_message = '%s OK' % commname
                        else:
                                ret_message = '%s KO' % commname
                else:
                        log.warning('unallowed command %s' % commargs)


        # \brief Originates / transfers.
        def __originate_or_transfer_old__(self, requester, l):
         src_split = l[1].split("/")
         dst_split = l[2].split("/")
         ret_message = 'originate_or_transfer KO from %s' % requester

         if len(src_split) == 5:
                [dummyp, astid_src, context_src, proto_src, userid_src] = src_split
         elif len(src_split) == 6:
                [dummyp, astid_src, context_src, proto_src, userid_src, dummy_exten_src] = src_split

         if len(dst_split) == 6:
                [p_or_a, astid_dst, context_dst, proto_dst, userid_dst, exten_dst] = dst_split
##                if p_or_a == 'a': find userid_dst => agentnum => phone num
         else:
                [dummyp, astid_dst, context_dst, proto_dst, userid_dst, exten_dst] = src_split
                exten_dst = l[2]
         if astid_src in self.configs and astid_src == astid_dst:
                if exten_dst == 'special:parkthecall':
                        exten_dst = self.configs[astid_dst].parkingnumber
                if astid_src in self.weblist['phones']:
                        if l[0] == 'originate':
                                log.info("%s is attempting an ORIGINATE : %s" % (requester, str(l)))
                                if astid_dst != '':
                                        sipcid_src = "SIP/%s" % userid_src
                                        sipcid_dst = "SIP/%s" % userid_dst
                                        cidname_src = userid_src
                                        cidname_dst = userid_dst
                                        if sipcid_src in self.weblist['phones'][astid_src].normal:
                                                cidname_src = '%s %s' %(self.weblist['phones'][astid_src].normal[sipcid_src].calleridfirst,
                                                                        self.weblist['phones'][astid_src].normal[sipcid_src].calleridlast)
                                        if sipcid_dst in self.weblist['phones'][astid_dst].normal:
                                                cidname_dst = '%s %s' %(self.weblist['phones'][astid_dst].normal[sipcid_dst].calleridfirst,
                                                                        self.weblist['phones'][astid_dst].normal[sipcid_dst].calleridlast)
                                        ret = self.__ami_execute__(astid_src, 'originate',
                                                                   proto_src, userid_src, cidname_src,
                                                                   exten_dst, cidname_dst, context_dst)
                                else:
                                        ret = False
                                if ret:
                                        ret_message = 'originate OK (%s) %s %s' %(astid_src, l[1], l[2])
                                else:
                                        ret_message = 'originate KO (%s) %s %s' %(astid_src, l[1], l[2])
                        elif l[0] == 'transfer':
                                log.info("%s is attempting a TRANSFER : %s" %(requester, str(l)))
                                phonesrc, phonesrcchan = split_from_ui(l[1])
                                if phonesrc == phonesrcchan:
                                        ret_message = 'transfer KO : %s not a channel' % phonesrcchan
                                else:
                                        if phonesrc in self.weblist['phones'][astid_src].normal:
                                                channellist = self.weblist['phones'][astid_src].normal[phonesrc].chann
                                                nopens = len(channellist)
                                                if nopens == 0:
                                                        ret_message = 'transfer KO : no channel opened on %s' % phonesrc
                                                else:
                                                        tchan = channellist[phonesrcchan].channel_peer
                                                        ret = self.__ami_execute__(astid_src, 'transfer',
                                                                                   tchan, exten_dst, context_dst)
                                                        if ret:
                                                                ret_message = 'transfer OK (%s) %s %s' %(astid_src, l[1], l[2])
                                                        else:
                                                                ret_message = 'transfer KO (%s) %s %s' %(astid_src, l[1], l[2])
                        elif l[0] == 'atxfer':
                                log.info("%s is attempting an ATXFER : %s" %(requester, str(l)))
                                phonesrc, phonesrcchan = split_from_ui(l[1])
                                if phonesrc == phonesrcchan:
                                        ret_message = 'atxfer KO : %s not a channel' % phonesrcchan
                                else:
                                        if phonesrc in self.weblist['phones'][astid_src].normal:
                                                channellist = self.weblist['phones'][astid_src].normal[phonesrc].chann
                                                nopens = len(channellist)
                                                if nopens == 0:
                                                        ret_message = 'atxfer KO : no channel opened on %s' % phonesrc
                                                else:
                                                        tchan = channellist[phonesrcchan].channel_peer
                                                        ret = self.__ami_execute__(astid_src, 'atxfer',
                                                                                   tchan, exten_dst, context_dst)
                                                        if ret:
                                                                ret_message = 'atxfer OK (%s) %s %s' %(astid_src, l[1], l[2])
                                                        else:
                                                                ret_message = 'atxfer KO (%s) %s %s' %(astid_src, l[1], l[2])
                                        else:
                                                log.warning("%s not in my phone list" % phonesrc)
         else:
                ret_message = 'originate or transfer KO : asterisk id mismatch (%s %s)' %(astid_src, astid_dst)
         return self.dmessage_srv2clt(ret_message)



        # \brief Hangs up.
        def __hangup__(self, uinfo, astid, chan, peer_hangup):
                print '__hangup__', uinfo, astid, chan, peer_hangup
                username = uinfo.get('fullname')
                ret_message = 'hangup KO from %s' % username
                if astid in self.configs:
                        log.info("%s is attempting a HANGUP : %s" % (username, chan))
                        channel = chan
                        phone = chan.split('-')[0]
                        if phone in self.weblist['phones'][astid].normal:
                                if channel in self.weblist['phones'][astid].normal[phone].chann:
                                        if peer_hangup:
                                                channel_peer = self.weblist['phones'][astid].normal[phone].chann[channel].channel_peer
                                                log.info("UI action : %s : hanging up <%s> and <%s>"
                                                          %(astid , channel, channel_peer))
                                        else:
                                                channel_peer = ''
                                                log.info("UI action : %s : hanging up <%s>"
                                                          %(astid , channel))
                                        ret = self.__ami_execute__(astid, 'hangup', channel, channel_peer)
                                        if ret > 0:
                                                ret_message = 'hangup OK (%d) <%s>' %(ret, chan)
                                        else:
                                                ret_message = 'hangup KO : socket request failed'
                                else:
                                        ret_message = 'hangup KO : no such channel <%s>' % channel
                        else:
                                ret_message = 'hangup KO : no such phone <%s>' % phone
                else:
                        ret_message = 'hangup KO : no such asterisk id <%s>' % astid
                return self.dmessage_srv2clt(ret_message)


        # \brief Function that fetches the call history from a database
        # \param cfg the asterisk's config
        # \param techno technology (SIP/IAX/ZAP/etc...)
        # \param phoneid phone id
        # \param nlines the number of lines to fetch for the given phone
        # \param kind kind of list (ingoing, outgoing, missed calls)
        def __update_history_call__(self, cfg, techno, phoneid, nlines, kind):
                results = []
                if cfg.cdr_db_conn is None:
                        log.warning('%s : no CDR uri defined for this asterisk - see cdr_db_uri parameter' % cfg.astid)
                else:
                        try:
                                cursor = cfg.cdr_db_conn.cursor()
                                columns = ('calldate', 'clid', 'src', 'dst', 'dcontext', 'channel', 'dstchannel',
                                           'lastapp', 'lastdata', 'duration', 'billsec', 'disposition', 'amaflags',
                                           'accountcode', 'uniqueid', 'userfield')
                                likestring = '%s/%s-%%' %(techno, phoneid)
                                orderbycalldate = "ORDER BY calldate DESC LIMIT %s" % nlines
                                
                                if kind == "0": # outgoing calls (all)
                                        cursor.query("SELECT ${columns} FROM cdr WHERE channel LIKE %s " + orderbycalldate,
                                                     columns,
                                                     (likestring,))
                                elif kind == "1": # incoming calls (answered)
                                        cursor.query("SELECT ${columns} FROM cdr WHERE disposition='ANSWERED' AND dstchannel LIKE %s " + orderbycalldate,
                                                     columns,
                                                     (likestring,))
                                else: # missed calls (received but not answered)
                                        cursor.query("SELECT ${columns} FROM cdr WHERE disposition!='ANSWERED' AND dstchannel LIKE %s " + orderbycalldate,
                                                     columns,
                                                     (likestring,))
                                results = cursor.fetchall()
                        except Exception, exc:
                                log.error('--- exception --- %s : Connection to DataBase failed in History request : %s'
                                          % (cfg.astid, exc))
                return results


        def __update_availstate__(self, userinfo, state):
                company = userinfo['company']
                username = userinfo['user']
                xivo_userid = userinfo['xivo_userid']
                astid = userinfo['astid']
                capaid = userinfo.get('capaid')
                
                if userinfo['state'] == 'xivo_unknown' and state in ['onlineincoming', 'onlineoutgoing', 'postcall']:
                        # we forbid 'asterisk-related' presence events to change the status of unlogged users
                        return None
                
                if 'login' in userinfo and 'sessiontimestamp' in userinfo.get('login'):
                        userinfo['login']['sessiontimestamp'] = time.time()
                        
                if capaid:
                        if state == 'xivo_unknown' or state in self.presence_sections[self.capas[capaid].presenceid].getstates():
                                userinfo['state'] = state
                        else:
                                log.warning('(user %s) : state <%s> is not an allowed one => keeping current <%s>'
                                            % (username, state, userinfo['state']))
                else:
                        userinfo['state'] = 'xivo_unknown'

                cstatus = {}
                statedetails = {'color' : 'grey',
                                'longname' : PRESENCE_UNKNOWN,
                                'stateid' : 'xivo_unknown'}
                if capaid and capaid in self.capas:
                        allowed = {}
                        presenceid = self.capas[capaid].presenceid
                        if presenceid in self.presence_sections:
                                allowed = self.presence_sections[presenceid].allowed(userinfo['state'])
                                if userinfo['state'] in self.presence_sections[presenceid].displaydetails:
                                        statedetails = self.presence_sections[presenceid].displaydetails[userinfo['state']]
                        wpid = self.capas[capaid].watchedpresenceid
                        if wpid in self.presence_sections:
                                cstatus = self.presence_sections[wpid].countstatus(self.__counts__(wpid))
                                
                        tosend = { 'class' : 'presence',
                                   'direction' : 'client',
                                   'company' : company,
                                   'userid' : username,
                                   'xivo_userid' : xivo_userid,
                                   'astid' : astid,
                                   'capapresence' : { 'state' : statedetails,
                                                      'allowed' : allowed },
                                   'presencecounter' : cstatus
                                   }
                        self.__send_msg_to_cti_client__(userinfo, cjson.encode(tosend))
                
                tosend = { 'class' : 'presence',
                           'direction' : 'client',
                           'company' : company,
                           'userid' : username,
                           'xivo_userid' : xivo_userid,
                           'astid' : astid,
                           'capapresence' : { 'state' : statedetails },
                           'presencecounter' : cstatus
                           }
                self.__send_msg_to_cti_clients_except__(userinfo, cjson.encode(tosend))
                return None


        # \brief Builds the full list of customers in order to send them to the requesting client.
        # This should be done after a command called "customers".
        # \return a string containing the full customers list
        # \sa manage_tcp_connection
        def __build_customers__(self, ctx, searchpattern):
                fulllist = []
                if ctx in self.ctxlist.ctxlist:
                        for dirsec, dirdef in self.ctxlist.ctxlist[ctx].iteritems():
                                try:
                                        y = self.__build_customers_bydirdef__(dirsec, searchpattern, dirdef)
                                        fulllist.extend(y)
                                except Exception, exc:
                                        log.error('--- exception --- __build_customers__ (%s) : %s' % (dirsec, exc))
                else:
                        log.warning('there has been no section defined for context %s : can not proceed directory search' % ctx)

                mylines = []
                for itemdir in fulllist:
                        myitems = []
                        for k in self.ctxlist.display_items[ctx]:
                                [title, type, defaultval, format] = self.ctxlist.displays[ctx][k].split('|')
                                basestr = format
                                for k, v in itemdir.iteritems():
                                        basestr = basestr.replace('{%s}' % k, v)
                                myitems.append(basestr)
                        mylines.append(';'.join(myitems))

                mylines.sort()
##                uniq = {}
##                fullstat_body = []
##                for fsl in [uniq.setdefault(e,e) for e in fullstatlist if e not in uniq]:
##                        fullstat_body.append(fsl)
##                return fullstat_body
                tosend = { 'class' : 'directory',
                           'direction' : 'client',
                           'payload' : ';'.join(self.ctxlist.display_header[ctx]) + ';' + ';'.join(mylines) }
                return cjson.encode(tosend)


        def __build_customers_bydirdef__(self, dirname, searchpattern, z):
                fullstatlist = []

                if searchpattern == '':
                        return []

                dbkind = z.uri.split(':')[0]
                if dbkind in ['ldap', 'ldaps']:
                        selectline = []
                        for fname in z.match_direct:
                                if searchpattern == '*':
                                        selectline.append("(%s=*)" % fname)
                                else:
                                        selectline.append("(%s=*%s*)" %(fname, searchpattern))

                        try:
                                results = None
                                if z.uri in self.ldapids:
                                        ldapid = self.ldapids[z.uri]
                                else:
                                        ldapid = xivo_ldap.xivo_ldap(z.uri)
                                        if ldapid.l is not None:
                                                self.ldapids[z.uri] = ldapid
                                if ldapid.l is not None:
                                        results = ldapid.getldap("(|%s)" % ''.join(selectline),
                                                                 z.match_direct)
                                if results is not None:
                                        for result in results:
                                                futureline = {'xivo-dir' : z.name}
                                                for keyw, dbkeys in z.fkeys.iteritems():
                                                        for dbkey in dbkeys:
                                                                if dbkey in result[1]:
                                                                        futureline[keyw] = result[1][dbkey][0]
                                                fullstatlist.append(futureline)
                        except Exception, exc:
                                log.error('--- exception --- ldaprequest (directory) : %s' % exc)

                elif dbkind == 'file':
                        f = urllib.urlopen(z.uri)
                        delimit = ':'
                        n = 0
                        for line in f:
                                if n == 0:
                                        header = line
                                        headerfields = header.strip().split(delimit)
                                else:
                                        ll = line.strip()
                                        if ll.lower().find(searchpattern.lower()) >= 0:
                                                t = ll.split(delimit)
                                                futureline = {'xivo-dir' : z.name}
                                                for keyw, dbkeys in z.fkeys.iteritems():
                                                        for dbkey in dbkeys:
                                                                idx = headerfields.index(dbkey)
                                                                futureline[keyw] = t[idx]
                                                fullstatlist.append(futureline)
                                n += 1
                        if n == 0:
                                log.warning('WARNING : %s is empty' % z.uri)
                        elif n == 1:
                                log.warning('WARNING : %s contains only one line (the header one)' % z.uri)

                elif dbkind == 'http':
                        f = urllib.urlopen('%s%s' % (z.uri, searchpattern))
                        delimit = ':'
                        n = 0
                        for line in f:
                                if n == 0:
                                        header = line
                                        headerfields = header.strip().split(delimit)
                                else:
                                        ll = line.strip()
                                        t = ll.split(delimit)
                                        futureline = {'xivo-dir' : z.name}
                                        for keyw, dbkeys in z.fkeys.iteritems():
                                                for dbkey in dbkeys:
                                                        idx = headerfields.index(dbkey)
                                                        futureline[keyw] = t[idx]
                                        fullstatlist.append(futureline)
                                n += 1
                        if n == 0:
                                log.warning('WARNING : %s is empty' % z.uri)
                        # we don't warn about "only one line" here since the filter has probably already been applied

                elif dbkind != '':
                        if searchpattern == '*':
                                whereline = ''
                        else:
                                wl = []
                                for fname in z.match_direct:
                                        wl.append("%s REGEXP '%s'" %(fname, searchpattern))
                                whereline = 'WHERE ' + ' OR '.join(wl)

                        try:
                                conn = anysql.connect_by_uri(z.uri)
                                cursor = conn.cursor()
                                cursor.query("SELECT ${columns} FROM " + z.sqltable + " " + whereline,
                                             tuple(z.match_direct),
                                             None)
                                results = cursor.fetchall()
                                conn.close()
                                for result in results:
                                        futureline = {'xivo-dir' : z.name}
                                        for keyw, dbkeys in z.fkeys.iteritems():
                                                for dbkey in dbkeys:
                                                        if dbkey in z.match_direct:
                                                                n = z.match_direct.index(dbkey)
                                                                futureline[keyw] = result[n]
                                        fullstatlist.append(futureline)
                        except Exception, exc:
                                log.error('--- exception --- sqlrequest : %s' % exc)
                else:
                        log.warning('no database method defined - please fill the uri field of the directory <%s> definition' % dirname)

                return fullstatlist


        def __counts__(self, presenceid):
                counts = {}
                if presenceid not in self.presence_sections:
                        return counts
                for istate in self.presence_sections[presenceid].getstates():
                        counts[istate] = 0
                for iuserinfo in self.ulist_ng.keeplist.itervalues():
                        if iuserinfo.has_key('capaid'):
                                capaid = iuserinfo['capaid']
                                if capaid in self.capas:
                                        if self.capas[capaid].presenceid == presenceid:
                                                if iuserinfo['state'] in self.presence_sections[presenceid].getstates():
                                                        counts[iuserinfo['state']] += 1
                return counts


        def handle_fagi(self, astid, fastagi):
                """
                Events coming from Fast AGI.
                """
                # check capas !
                context = fastagi.get_variable('XIVO_CONTEXT')
                uniqueid = fastagi.get_variable('UNIQUEID')
                channel = fastagi.get_variable('CHANNEL')
                function = fastagi.env['agi_network_script']
                log.info('handle_fagi %s : context=%s uid=%s chan=%s (%s)' % (astid, context, uniqueid, channel, function))
                
                if function == 'presence':
                        if len(fastagi.args) > 0:
                                presenceid = fastagi.args[0]
                                aststatus = []
                                for var, val in self.__counts__(presenceid).iteritems():
                                        aststatus.append('%s:%d' % (var, val))
                                fastagi.set_variable('XIVO_PRESENCE', ','.join(aststatus))
                        return
                
                elif function == 'queuestatus':
                        if len(fastagi.args) > 0:
                                queuename = fastagi.args[0]
                                if queuename in self.weblist['queues'][astid].keeplist:
                                        qprops = self.weblist['queues'][astid].keeplist[queuename]['agents']
                                        lst = []
                                        for ag, agc in qprops.iteritems():
                                                sstatus = 'unknown'
                                                status = agc.get('Status')
                                                if status == '1':
                                                        if agc.get('Paused') == '1':
                                                                sstatus = 'paused'
                                                        else:
                                                                sstatus = 'available'
                                                elif status == '3':
                                                        sstatus = 'busy'
                                                elif status == '5':
                                                        sstatus = 'away'
                                                lst.append('%s:%s' % (ag, sstatus))
                                        fastagi.set_variable('XIVO_QUEUESTATUS', ','.join(lst))
                                        fastagi.set_variable('XIVO_QUEUEID', self.weblist['queues'][astid].keeplist[queuename]['id'])
                        return
                
                elif function == 'queueentries':
                        if len(fastagi.args) > 0:
                                queuename = fastagi.args[0]
                                if queuename in self.weblist['queues'][astid].keeplist:
                                        qentries = self.weblist['queues'][astid].keeplist[queuename]['channels']
                                        lst = []
                                        for chan, chanprops in qentries.iteritems():
                                                lst.append('%s:%d' % (chan, int(round(time.time() - chanprops.get('updatetime') + chanprops.get('wait')))))
                                        fastagi.set_variable('XIVO_QUEUEENTRIES', ','.join(lst))
                        return
                
                elif function == 'queueholdtime':
                        if len(fastagi.args) > 0:
                                queuename = fastagi.args[0]
                                if queuename in self.weblist['queues'][astid].keeplist:
                                        fastagi.set_variable('XIVO_QUEUEHOLDTIME',
                                                             self.weblist['queues'][astid].keeplist[queuename]['stats']['Holdtime'])
                        else:
                                lst = []
                                for queuename, qprops in self.weblist['queues'][astid].keeplist.iteritems():
                                        lst.append('%s:%s' % (queuename, qprops['stats']['Holdtime']))
                                        fastagi.set_variable('XIVO_QUEUEHOLDTIME', ','.join(lst))
                        return
                
                elif function != 'xivo_push':
                        log.warning('handle_fagi %s : unknown function %s' % (astid, function))
                        return
                
                callednum = fastagi.get_variable('XIVO_DSTNUM')
                calleridnum  = fastagi.env['agi_callerid']
                calleridname = fastagi.env['agi_calleridname']
                
                log.info('handle_fagi : %s : context=%s %s %s <%s>' % (astid, context, callednum, calleridnum, calleridname))
                
                extraevent = {'caller_num' : calleridnum,
                              'called_num' : callednum,
                              'uniqueid' : uniqueid,
                              'channel' : channel}
                clientstate = 'available'
                calleridsolved = calleridname

                self.__sheet_alert__('agi', astid, context, {}, extraevent)

                if calleridnum == 'unknown':
                        # to set according to os.getenv('LANG') or os.getenv('LANGUAGE') later on ?
                        calleridnum = 'Inconnu'
                if calleridname == 'unknown':
                        calleridname = ''

                calleridtoset = '"%s"<%s>' %(calleridsolved, calleridnum)
                # if calleridsolved not found
                # calleridtoset = '"%s"<%s>' %(calleridname, calleridnum)
                log.info('handle_fagi : %s : the CallerId will be set to %s' % (astid, calleridtoset))
                fastagi.set_callerid(calleridtoset)

##                if clientstate == 'available' or clientstate == 'nopresence':
##                        fastagi.set_variable('XIVO_AIMSTATUS', 0)
##                elif clientstate == 'away':
##                        fastagi.set_variable('XIVO_AIMSTATUS', 1)
##                elif clientstate == 'donotdisturb':
##                        fastagi.set_variable('XIVO_AIMSTATUS', 2)
##                elif clientstate == 'outtolunch':
##                        fastagi.set_variable('XIVO_AIMSTATUS', 3)
##                elif clientstate == 'berightback':
##                        fastagi.set_variable('XIVO_AIMSTATUS', 4)
##                else:
##                        print "Unknown user's availability status : <%s>" % clientstate
                return


xivo_commandsets.CommandClasses['xivocti'] = XivoCTICommand


def channel_splitter(channel):
        sp = channel.split('-')
        if len(sp) > 1:
                sp.pop()
        return '-'.join(sp)


def split_from_ui(fullname):
        phone = ""
        channel = ""
        s1 = fullname.split('/')
        if len(s1) == 5:
                phone = s1[3] + "/" + channel_splitter(s1[4])
                channel = s1[3] + "/" + s1[4]
        return [phone, channel]
