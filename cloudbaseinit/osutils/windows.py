# vim: tabstop=4 shiftwidth=4 softtabstop=4

# Copyright 2012 Cloudbase Solutions Srl
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

import _winreg
import ctypes
import os
import re
import time
import win32process
import win32security
import wmi

from ctypes import windll
from ctypes import wintypes

from cloudbaseinit.openstack.common import log as logging
from cloudbaseinit.osutils import base

advapi32 = windll.advapi32
kernel32 = windll.kernel32
netapi32 = windll.netapi32
userenv = windll.userenv

LOG = logging.getLogger(__name__)


class Win32_PROFILEINFO(ctypes.Structure):
    _fields_ = [
        ('dwSize',          wintypes.DWORD),
        ('dwFlags',         wintypes.DWORD),
        ('lpUserName',      wintypes.LPWSTR),
        ('lpProfilePath',   wintypes.LPWSTR),
        ('lpDefaultPath',   wintypes.LPWSTR),
        ('lpServerName',    wintypes.LPWSTR),
        ('lpPolicyPath',    wintypes.LPWSTR),
        ('hprofile',        wintypes.HANDLE)
    ]


class Win32_LOCALGROUP_MEMBERS_INFO_3(ctypes.Structure):
    _fields_ = [
        ('lgrmi3_domainandname', wintypes.LPWSTR)
    ]


class WindowsUtils(base.BaseOSUtils):
    NERR_GroupNotFound = 2220
    ERROR_ACCESS_DENIED = 5
    ERROR_NO_SUCH_MEMBER = 1387
    ERROR_MEMBER_IN_ALIAS = 1378
    ERROR_INVALID_MEMBER = 1388

    _config_key = 'SOFTWARE\\Cloudbase Solutions\\Cloudbase-Init\\'
    _service_name = 'cloudbase-init'

    def _enable_shutdown_privilege(self):
        process = win32process.GetCurrentProcess()
        token = win32security.OpenProcessToken(
            process,
            win32security.TOKEN_ADJUST_PRIVILEGES |
            win32security.TOKEN_QUERY)
        priv_luid = win32security.LookupPrivilegeValue(
            None, win32security.SE_SHUTDOWN_NAME)
        privilege = [(priv_luid, win32security.SE_PRIVILEGE_ENABLED)]
        win32security.AdjustTokenPrivileges(token, False, privilege)

    def reboot(self):
        self._enable_shutdown_privilege()

        ret_val = advapi32.InitiateSystemShutdownW(0, "Cloudbase-Init reboot",
                                                   0, True, True)
        if not ret_val:
            raise Exception("Reboot failed")

    def _get_user_wmi_object(self, username):
        conn = wmi.WMI(moniker='//./root/cimv2')
        username_san = self._sanitize_wmi_input(username)
        q = conn.query('SELECT * FROM Win32_Account where name = '
                       '\'%(username_san)s\'' % locals())
        if len(q) > 0:
            return q[0]
        return None

    def user_exists(self, username):
        return self._get_user_wmi_object(username) is not None

    def _create_or_change_user(self, username, password, create,
                               password_expires):
        username_san = self.sanitize_shell_input(username)
        password_san = self.sanitize_shell_input(password)

        args = ['NET', 'USER', username_san, password_san]
        if create:
            args.append('/ADD')

        (out, err, ret_val) = self.execute_process(args)
        if not ret_val:
            self._set_user_password_expiration(username, password_expires)
        else:
            if create:
                msg = "Create user failed: %(err)s"
            else:
                msg = "Set user password failed: %(err)s"
            raise Exception(msg % locals())

    def _sanitize_wmi_input(self, value):
        return value.replace('\'', '\'\'')

    def _set_user_password_expiration(self, username, password_expires):
        r = self._get_user_wmi_object(username)
        if not r:
            return False
        r.PasswordExpires = password_expires
        r.Put_()
        return True

    def create_user(self, username, password, password_expires=False):
        self._create_or_change_user(username, password, True,
                                    password_expires)

    def set_user_password(self, username, password, password_expires=False):
        self._create_or_change_user(username, password, False,
                                    password_expires)

    def _get_user_sid_and_domain(self, username):
        sid = ctypes.create_string_buffer(1024)
        cbSid = wintypes.DWORD(ctypes.sizeof(sid))
        domainName = ctypes.create_unicode_buffer(1024)
        cchReferencedDomainName = wintypes.DWORD(
            ctypes.sizeof(domainName) / ctypes.sizeof(wintypes.WCHAR))
        sidNameUse = wintypes.DWORD()

        ret_val = advapi32.LookupAccountNameW(
            0, unicode(username), sid, ctypes.byref(cbSid), domainName,
            ctypes.byref(cchReferencedDomainName), ctypes.byref(sidNameUse))
        if not ret_val:
            raise Exception("Cannot get user SID")

        return (sid, domainName.value)

    def add_user_to_local_group(self, username, groupname):

        lmi = Win32_LOCALGROUP_MEMBERS_INFO_3()
        lmi.lgrmi3_domainandname = unicode(username)

        ret_val = netapi32.NetLocalGroupAddMembers(0, unicode(groupname), 3,
                                                   ctypes.addressof(lmi), 1)

        if ret_val == self.NERR_GroupNotFound:
            raise Exception('Group not found')
        elif ret_val == self.ERROR_ACCESS_DENIED:
            raise Exception('Access denied')
        elif ret_val == self.ERROR_NO_SUCH_MEMBER:
            raise Exception('Username not found')
        elif ret_val == self.ERROR_MEMBER_IN_ALIAS:
            # The user is already a member of the group
            pass
        elif ret_val == self.ERROR_INVALID_MEMBER:
            raise Exception('Invalid user')
        elif ret_val != 0:
            raise Exception('Unknown error')

    def get_user_sid(self, username):
        r = self._get_user_wmi_object(username)
        if not r:
            return None
        return r.SID

    def create_user_logon_session(self, username, password, domain='.',
                                  load_profile=True):
        token = wintypes.HANDLE()
        ret_val = advapi32.LogonUserW(unicode(username), unicode(domain),
                                      unicode(password), 2, 0,
                                      ctypes.byref(token))
        if not ret_val:
            raise Exception("User logon failed")

        if load_profile:
            pi = Win32_PROFILEINFO()
            pi.dwSize = ctypes.sizeof(Win32_PROFILEINFO)
            pi.lpUserName = unicode(username)
            ret_val = userenv.LoadUserProfileW(token, ctypes.byref(pi))
            if not ret_val:
                kernel32.CloseHandle(token)
                raise Exception("Cannot load user profile")

        return token

    def close_user_logon_session(self, token):
        kernel32.CloseHandle(token)

    def get_user_home(self, username):
        user_sid = self.get_user_sid(username)
        if user_sid:
            with _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE, 'SOFTWARE\\'
                                 'Microsoft\\Windows NT\\CurrentVersion\\'
                                 'ProfileList\\%s' % user_sid) as key:
                return _winreg.QueryValueEx(key, 'ProfileImagePath')[0]
        LOG.debug('Home directory not found for user \'%s\'' % username)
        return None

    def sanitize_shell_input(self, value):
        return value.replace('"', '\\"')

    def set_host_name(self, new_host_name):
        conn = wmi.WMI(moniker='//./root/cimv2')
        comp = conn.Win32_ComputerSystem()[0]
        if comp.Name != new_host_name:
            comp.Rename(new_host_name, None, None)
            return True
        else:
            return False

    def get_network_adapters(self):
        l = []
        conn = wmi.WMI(moniker='//./root/cimv2')
        # Get Ethernet adapters only
        q = conn.query('SELECT * FROM Win32_NetworkAdapter WHERE '
                       'AdapterTypeId = 0 AND PhysicalAdapter = True AND '
                       'MACAddress IS NOT NULL')
        for r in q:
            l.append(r.Name)
        return l

    def set_static_network_config(self, adapter_name, address, netmask,
                                  broadcast, gateway, dnsnameservers):
        conn = wmi.WMI(moniker='//./root/cimv2')

        adapter_name_san = self._sanitize_wmi_input(adapter_name)
        q = conn.query('SELECT * FROM Win32_NetworkAdapter WHERE '
                       'MACAddress IS NOT NULL AND '
                       'Name = \'%(adapter_name_san)s\'' % locals())
        if not len(q):
            raise Exception("Network adapter not found")

        adapter_config = q[0].associators(
            wmi_result_class='Win32_NetworkAdapterConfiguration')[0]

        LOG.debug("Setting static IP address")
        (ret_val,) = adapter_config.EnableStatic([address], [netmask])
        if ret_val > 1:
            raise Exception("Cannot set static IP address on network adapter")
        reboot_required = (ret_val == 1)

        LOG.debug("Setting static gateways")
        (ret_val,) = adapter_config.SetGateways([gateway], [1])
        if ret_val > 1:
            raise Exception("Cannot set gateway on network adapter")
        reboot_required = reboot_required or ret_val == 1

        LOG.debug("Setting static DNS servers")
        (ret_val,) = adapter_config.SetDNSServerSearchOrder(dnsnameservers)
        if ret_val > 1:
            raise Exception("Cannot set DNS on network adapter")
        reboot_required = reboot_required or ret_val == 1

        return reboot_required

    def _get_config_key_name(self, section):
        key_name = self._config_key
        if section:
            key_name += section + '\\'
        return key_name

    def set_config_value(self, name, value, section=None):
        key_name = self._get_config_key_name(section)

        with _winreg.CreateKey(_winreg.HKEY_LOCAL_MACHINE,
                               key_name) as key:
            if type(value) == int:
                regtype = _winreg.REG_DWORD
            else:
                regtype = _winreg.REG_SZ
            _winreg.SetValueEx(key, name, 0, regtype, value)

    def get_config_value(self, name, section=None):
        key_name = self._get_config_key_name(section)

        try:
            with _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE,
                                 key_name) as key:
                (value, regtype) = _winreg.QueryValueEx(key, name)
                return value
        except WindowsError:
            return None

    def wait_for_boot_completion(self):
        try:
            with _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE,
                                 "SYSTEM\\Setup\\Status\\SysprepStatus", 0,
                                 _winreg.KEY_READ) as key:
                while True:
                    gen_state = _winreg.QueryValueEx(key,
                                                     "GeneralizationState")[0]
                    if gen_state == 7:
                        break
                    time.sleep(1)
                    LOG.debug('Waiting for sysprep completion. '
                              'GeneralizationState: %d' % gen_state)
        except WindowsError, ex:
            if ex.winerror == 2:
                LOG.debug('Sysprep data not found in the registry, '
                          'skipping sysprep completion check.')
            else:
                raise ex

    def _stop_service(self, service_name):
        LOG.debug('Stopping service %s' % service_name)

        conn = wmi.WMI(moniker='//./root/cimv2')
        service = conn.Win32_Service(Name=service_name)[0]

        (ret_val,) = service.StopService()
        if ret_val != 0:
            raise Exception('Stopping service %(service_name)s failed with '
                            'return value: %(ret_val)d' % locals())

    def terminate(self):
        # Wait for the service to start. Polling the service "Started" property
        # is not enough
        time.sleep(3)
        self._stop_service(self._service_name)

    def get_default_gateway(self):
        conn = wmi.WMI(moniker='//./root/cimv2')
        for net_adapter_config in conn.Win32_NetworkAdapterConfiguration():
            if net_adapter_config.DefaultIPGateway:
                return (net_adapter_config.InterfaceIndex,
                        net_adapter_config.DefaultIPGateway[0])
        return (None, None)

    def check_static_route_exists(self, destination):
        conn = wmi.WMI(moniker='//./root/cimv2')
        return len(conn.Win32_IP4RouteTable(Destination=destination)) > 0

    def add_static_route(self, destination, mask, next_hop, interface_index,
                         metric):
        args = ['ROUTE', 'ADD', destination, 'MASK', mask, next_hop]
        (out, err, ret_val) = self.execute_process(args)
        # Cannot use the return value to determine the outcome
        if err:
            raise Exception('Unable to add route: %(err)s' % locals())

        # TODO(alexpilotti): The following code creates the route properly and
        # "route print" shows the added route, but routing to the destination
        # fails. This option would be preferable compared to spawning a
        # "ROUTE ADD" process.
        '''
        ROUTE_PROTOCOL_NETMGMT = 3
        ROUTE_TYPE_INDIRECT = 4

        conn = wmi.WMI(moniker='//./root/cimv2')

        route = conn.Win32_IP4RouteTable.SpawnInstance_()
        route.Destination = destination
        route.Mask = mask
        route.NextHop = next_hop
        route.InterfaceIndex = interface_index
        route.Metric1 = metric
        route.Protocol = self.ROUTE_PROTOCOL_NETMGMT
        route.Type = self.ROUTE_TYPE_INDIRECT
        route.Put_()
        '''

    def get_os_version(self):
        conn = wmi.WMI(moniker='//./root/cimv2')
        return conn.Win32_OperatingSystem()[0].Version

    def get_volume_label(self, drive):
        max_label_size = 261
        label = ctypes.create_unicode_buffer(max_label_size)
        ret_val = kernel32.GetVolumeInformationW(unicode(drive), label,
                                                 max_label_size, 0, 0, 0, 0, 0)
        if ret_val:
            return label.value

    def get_folder_path(self):
        """ Get the cloudbase-init installation folder path."""
        path = os.path.realpath(__file__)
        dirr = "Cloudbase-Init"
        return os.path.join(path[:path.find(dirr)+len(dirr)])

    def sanitaze_powershell_path(self, path):
        escape_dict = {'\a': r'\a',
                       '\b': r'\b',
                       '\c': r'\c',
                       '\f': r'\f',
                       '\n': r'\n',
                       '\r': r'\r',
                       '\t': r'\t',
                       '\v': r'\v',
                       '\'': r'\'',
                       '\"': r'\"',
                       '\0': r'\0',
                       '\1': r'\1',
                       '\2': r'\2',
                       '\3': r'\3',
                       '\4': r'\4',
                       '\5': r'\5',
                       '\6': r'\6',
                       '\7': r'\7',
                       '\8': r'\8',
                       '\9': r'\9'
                       }
        clean_path = ""

        for c in path:
            if not c.isalnum():
                clean_path += '`' + c
            else:
                clean_path += c
        clean_path = "".join([escape_dict.get(c, c) for c in clean_path])

        return clean_path

    def get_params_from_extension(self, target_path):
        """ Get the execution params based on the file extension. """
        extension = os.path.splitext(target_path)[1][1:].strip()
        if extension == "ps1":
            target_path = self.sanitaze_powershell_path(target_path)
            if os.path.isdir(os.path.expandvars('%windir%\\sysnative')):
                eng = script_params['ps64']
            else:
                eng = script_params['ps86']
        elif extension == "sh":
            eng = script_params['bash']
        elif extension == "cmd":
            if os.path.isdir(os.path.expandvars('%windir%\\sysnative')):
                eng = script_params['cmd64']
            else:
                eng = script_params['cmd86']
        elif extension == "py":
            eng = script_params['py']
        else:
            eng['args'] = []
            eng['shell'] = False
        eng['args'].append(target_path)
        return (eng['args'], eng['shell'])

    def get_userdata_params(self, target_path, user_data):
        if re.search(r'^rem cmd\s', user_data, re.I):
            if os.path.isdir(os.path.expandvars('%windir%\\sysnative')):
                target_path += '.cmd'
                eng = script_params['cmd64']
            else:
                target_path += '.cmd'
                eng = script_params['cmd86']
        elif re.search(r'^#!', user_data, re.I):
            target_path += '.sh'
            eng = script_params['bash']
        elif re.search(r'^#ps1\s', user_data, re.I):
            target_path = self.sanitaze_powershell_path(target_path)
            if os.path.isdir(os.path.expandvars('%windir%\\sysnative')):
                target_path += '.ps1'
                eng = script_params['ps64']
            else:
                target_path += '.ps1'
                eng = script_params['ps86']
        else:
            # Unsupported
            LOG.warning('Unsupported user_data format')
            raise Exception()
        eng['args'].append(target_path)
        return (eng['args'], eng['shell'])

script_params = {'ps64': {'args': [os.path.expandvars('%windir%\\sysnative\\'
                                                      'WindowsPowerShell\\'
                                                      'v1.0\\powershell.exe'),
                                   '-ExecutionPolicy', 'RemoteSigned',
                                   '-NonInteractive'],
                          'shell': False
                          },
                 'ps86': {'args': ['powershell.exe',
                                   '-ExecutionPolicy', 'RemoteSigned',
                                   '-NonInteractive'],
                          'shell': False
                          },
                 'bash': {'args': ['bash.exe'],
                          'shell': False
                          },
                 'cmd64': {'args': [os.path.expandvars('%windir%\\sysnative\\'
                                                       'cmd.exe'), '/c'],
                           'shell': True
                           },
                 'cmd86': {'args': [],
                           'shell': True
                           },
                 'py': {'args': [os.getcwd() + '\\python.exe'],
                        'shell': False
                        }
                 }
