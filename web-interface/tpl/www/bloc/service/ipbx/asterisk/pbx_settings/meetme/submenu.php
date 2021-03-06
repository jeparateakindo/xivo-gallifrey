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
<div class="sb-smenu">
	<ul>
		<li id="dwsm-tab-1"
		    class="dwsm-blur"
		    onmouseout="dwho_submenu.blur(this);"
		    onmouseover="dwho_submenu.focus(this);">
			<div onclick="dwho_submenu.select(dwho_eid('dwsm-tab-1'),'sb-part-first');">
				<div class="tab">
					<span class="span-center">
						<a href="#" onclick="return(false);"><?=$this->bbf('smenu_general');?></a>
					</span>
				</div>
				<span class="span-right">&nbsp;</span>
			</div>
			<div class="stab">
				<ul>
					<li><a href="#"
					       onclick="dwho_submenu.select(dwho_eid('dwsm-tab-1'),'sb-part-period');
							return(false);"><?=$this->bbf('smenu_period');?></a></li>
				</ul>
			</div>
		</li>
		<li id="dwsm-tab-2"
		    class="dwsm-blur"
		    onclick="dwho_submenu.select(this,'sb-part-administrator');"
		    onmouseout="dwho_submenu.blur(this);"
		    onmouseover="dwho_submenu.focus(this);">
			<div class="tab">
				<span class="span-center">
					<a href="#" onclick="return(false);"><?=$this->bbf('smenu_administrator');?></a>
				</span>
			</div>
			<span class="span-right">&nbsp;</span>
		</li>
		<li id="dwsm-tab-3"
		    class="dwsm-blur"
		    onclick="dwho_submenu.select(this,'sb-part-user');"
		    onmouseout="dwho_submenu.blur(this);"
		    onmouseover="dwho_submenu.focus(this);">
			<div class="tab">
				<span class="span-center">
					<a href="#" onclick="return(false);"><?=$this->bbf('smenu_user');?></a>
				</span>
			</div>
			<span class="span-right">&nbsp;</span>
		</li>
		<li id="dwsm-tab-4"
		    class="dwsm-blur-last"
		    onmouseout="dwho_submenu.blur(this,1);"
		    onmouseover="dwho_submenu.focus(this,1);">
			<div onclick="dwho_submenu.select(dwho_eid('dwsm-tab-4'),'sb-part-last',1);">
				<div class="tab">
					<span class="span-center">
						<a href="#" onclick="return(false);"><?=$this->bbf('smenu_guest');?></a>
					</span>
				</div>
				<span class="span-right">&nbsp;</span>
			</div>
			<div class="stab">
				<ul>
					<li><a href="#"
					       onclick="dwho_submenu.select(dwho_eid('dwsm-tab-4'),'sb-part-email',1);
							return(false);"><?=$this->bbf('smenu_email');?></a></li>
				</ul>
			</div>
		</li>
	</ul>
</div>
