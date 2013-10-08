# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2013 Cloudbase Solutions Srl
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import os

from cloudbaseinit.openstack.common import log as logging
from cloudbaseinit.openstack.common import cfg
from cloudbaseinit.osutils import factory as osutils_factory
from cloudbaseinit.plugins import base


opts = [
    cfg.StrOpt('user_scripts_folder', default='cloud_scripts',
               help='Specifies a folder to store script files.'),
]

CONF = cfg.CONF
CONF.register_opts(opts)

LOG = logging.getLogger(__name__)


class UserScriptsPlugin(base.BasePlugin):
    """This plugin is used to execute all scripts from a folder."""

    def execute(self, service):
        osutils = osutils_factory.OSUtilsFactory().get_os_utils()

        if CONF.user_scripts_folder == 'cloud_scripts':
            folder = os.path.join(osutils.get_folder_path(),
                                  CONF.user_scripts_folder) + "\\"
        else:
            folder = os.path.join(CONF.user_scripts_folder) + "\\"
        if os.path.isdir(folder):
            for filename in os.listdir(folder):
                target_path = os.path.join(folder, filename)
                (args, shell) = osutils.get_params_from_extension(target_path)
                try:
                    (out, err, ret_val) = osutils.execute_process(args, shell)
                    LOG.info('User script ended with return code: %d'
                             % ret_val)
                    LOG.debug('User script stdout:\n%s' % out)
                    LOG.debug('User script stderr:\n%s' % err)
                except Exception, ex:
                    LOG.error('An error occurred during'
                              ' User script execution: \'%s\'' % ex)
        return (base.PLUGIN_EXECUTION_DONE, False)
