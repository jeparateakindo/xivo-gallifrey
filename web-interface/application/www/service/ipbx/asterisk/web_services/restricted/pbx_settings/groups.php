<?php

xivo::load_class('xivo_accesswebservice',XIVO_PATH_OBJECT,null,false);
$_AWS = new xivo_accesswebservice();

$access = $_AWS->chk_http_access('pbx_settings','groups');

include(dirname(__FILE__).'/../restricted.php');

$appgroup = &$ipbx->get_application('group',null,false);

switch($_QRY->get_qs('act'))
{
	case 'list':
	default:
		if(($groups = $appgroup->get_groups_list()) === false)
		{
			xivo::load_class('xivo_http');
			$http = new xivo_http();
			$http->set_status(204);
			$http->send(true);
		}

		$_HTML->set_var('groups',$groups);
		$_HTML->set_var('sum',$_QRY->get_qs('sum'));
		$_HTML->display('/service/ipbx/'.$ipbx->get_name().'/pbx_settings/groups');
}

?>
