<?php

#
# XiVO Web-Interface
# Copyright (C) 2009  Proformatique <technique@proformatique.com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

require_once(xivo_file::joinpath(XIVO_PATH_LIB,'tpl','module','xmlphone','abstract','abstract.inc'));

class xivo_tpl_xmlphone_snom extends xivo_tpl_xmlphone_abstract
{
	var $_vendor		= 'snom';
	var $_model		= null;
	var $_argseparator	= '&';
	var $_cdata		= false;
	var $_escape		= false;
	var $_utf8		= true;

	function xivo_tpl_xmlphone_aastra()
	{
		$this->_model = $this->get_model();
	}

	function get_model()
	{
		if(isset($_SERVER['HTTP_USER_AGENT']) === false
		|| preg_match('/(snom3[026]0)-/',
			      $_SERVER['HTTP_USER_AGENT'],
			      $match) !== 1)
			return(null);

		return($match[1]);
	}

	function tag_directory()
	{
		return('SnomIPPhoneDirectory');
	}

	function tag_menu()
	{
		return('SnomIPPhoneMenu');
	}

	function tag_input()
	{
		return('SnomIPPhoneInput');
	}
}

?>