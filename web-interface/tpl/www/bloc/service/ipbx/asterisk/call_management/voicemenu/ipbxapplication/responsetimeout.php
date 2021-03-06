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

?>
<div id="fd-ipbxapplication-responsetimeout" class="b-nodisplay">
<?php

$form = &$this->get_module('form');
$apparg_responsetimeout = $this->get_var('apparg_responsetimeout');

echo	$form->text(array('desc'	=> $this->bbf('fm_ipbxapplication_responsetimeout-timeout'),
			  'name'	=> 'ipbxapplication[responsetimeout][timeout]',
			  'labelid'	=> 'ipbxapplication-responsetimeout-timeout',
			  'size'	=> 10,
			  'default'	=> $apparg_responsetimeout['timeout']['default'])),

	$form->button(array('name'	=> 'add-ipbxapplication-responsetimeout',
			    'id'	=> 'it-add-ipbxapplication-responsetimeout',
			    'value'	=> $this->bbf('fm_bt-add')),
		      'onclick="xivo_ast_application_responsetimeout();"');

?>
</div>
