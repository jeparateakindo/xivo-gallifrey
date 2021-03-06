<?php

#
# XiVO Web-Interface
# Copyright (C) 2006-2010  Proformatique <technique@proformatique.com>
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

class xivo_service_asterisk_queuefeatures_identity
{
	function _prepare_identity(&$data,$full=true)
	{
		if(dwho_has_len($data['context']) === false)
			$data['context'] = 'default';

		$data['identity'] = trim($data['name']);

		if($data['identity'] === '')
			$data['identity'] = '-';

		if((bool) $full === false)
			return($data);
		else if(dwho_has_len($data['number']) === true)
			$data['identity'] .= ' ('.$data['number'].'@'.$data['context'].')';
		else
			$data['identity'] .= ' ('.$data['context'].')';

		return($data);
	}

	function mk_identity($arr,$full=true)
	{
		if(is_array($arr) === false || isset($arr['name']) === false)
			return(false);

		if(isset($arr['number']) === false)
			$arr['number'] = '';

		if(isset($arr['context']) === false)
			$arr['context'] = '';

		$this->_prepare_identity($arr,$full);

		return($arr['identity']);
	}
}

?>
