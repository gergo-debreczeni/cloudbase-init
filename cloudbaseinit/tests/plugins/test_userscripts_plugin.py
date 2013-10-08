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

import mock
import os
import unittest

from cloudbaseinit.osutils import factory
from cloudbaseinit.plugins import base
from cloudbaseinit.plugins.windows import userscripts_plugin


class UserScriptsTest(unittest.TestCase):

    _FAKE_SERVICE = None
    _FAKE_FOLDER_PATH = "fake\\folder\\path"
    _FAKE_FOLDER = "fake\\folder"
    _FAKE_FILES = ['fake\\path\\file1',
                   'fake\\path\\file2']
    _FAKE_PARAMS = ('first_param', 'second_param')

    def setUp(self):
        self._winutils = mock.MagicMock()
        self._os = userscripts_plugin.os = mock.MagicMock()
        self._user_script_plugin = userscripts_plugin.UserScriptsPlugin()

    def test_execute(self):
        factory.OSUtilsFactory.get_os_utils = mock.MagicMock(return_value=
                                                             self._winutils)
        fp = self._FAKE_FOLDER_PATH
        self._winutils.get_folder_path = mock.MagicMock(return_value=fp)

        side_effect = lambda v1, v2: v1 + v2

        self._os.path.join = mock.MagicMock(side_effect=side_effect)
        self._os.listdir = mock.MagicMock(return_value=self._FAKE_FILES)
        self._winutils.get_params_from_extension = mock.MagicMock(
                                        return_value=self._FAKE_PARAMS)

        response = self._user_script_plugin.execute(self._FAKE_SERVICE)
        userscripts_plugin.os = os
        self.assertTrue(self._winutils.get_folder_path.called)
        self.assertEqual(response, (base.PLUGIN_EXECUTION_DONE, False))
