#!/usr/bin/env python'
# -*- coding: utf-8 -*-
"""
Ovirt Provider Class
"""

from kvirt import common
from kvirt.ovirt.helpers import TEMPLATES as otemplates
from kvirt.ovirt.helpers import get_home_ssh_key
import ovirtsdk4 as sdk
from ovirtsdk4 import Error as oerror
import ovirtsdk4.types as types
import os
from subprocess import call
from time import sleep


class KOvirt(object):
    def __init__(self, host='127.0.0.1', port=22, user='admin@internal',
                 password=None, insecure=True, ca_file=None, org=None, debug=False,
                 cluster='Default', datacenter='Default', ssh_user='root', imagerepository='ovirt-image-repository'):
        try:
            url = "https://%s/ovirt-engine/api" % host
            self.conn = sdk.Connection(url=url, username=user,
                                       password=password, insecure=insecure,
                                       ca_file=ca_file)
        except oerror as e:
            common.pprint("Unexpected error: %s" % e, color='red')
            return None
        self.debug = debug
        self.vms_service = self.conn.system_service().vms_service()
        self.templates_service = self.conn.system_service().templates_service()
        self.datacenter = datacenter
        self.cluster = cluster
        self.host = host
        self.port = port
        self.user = user
        self.ca_file = ca_file
        self.org = org
        self.ssh_user = ssh_user
        self.imagerepository = imagerepository

    def close(self):
        self.api.disconnect()
        return

    def exists(self, name):
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if vmsearch:
            return True
        return False

    def net_exists(self, name):
        profiles_service = self.conn.system_service().vnic_profiles_service()
        netprofiles = {}
        for prof in profiles_service.list():
                netprofiles[prof.name] = prof.id
        if 'default' not in netprofiles and 'ovirtmgmt' in netprofiles:
            netprofiles['default'] = netprofiles['ovirtmgmt']
        if name in netprofiles:
            return True
        return False

    def disk_exists(self, pool, name):
        print("not implemented")

    def create(self, name, virttype='kvm', profile='', plan='kvirt',
               cpumodel='Westmere', cpuflags=[], numcpus=2, memory=512,
               guestid='guestrhel764', pool='default', template=None,
               disks=[{'size': 10}], disksize=10, diskthin=True,
               diskinterface='virtio', nets=['default'], iso=None, vnc=False,
               cloudinit=True, reserveip=False, reservedns=False,
               reservehost=False, start=True, keys=None, cmds=[], ips=None,
               netmasks=None, gateway=None, nested=True, dns=None, domain=None,
               tunnel=False, files=[], enableroot=True, alias=[], overrides={},
               tags=None):
        clone = not diskthin
        templateobject = types.Template(name=template) if template else None
        console = types.Console(enabled=True)
        vm = self.vms_service.add(types.Vm(name=name, cluster=types.Cluster(name=self.cluster),
                                           template=templateobject, console=console), clone=clone)
        vm_service = self.vms_service.vm_service(vm.id)
        timeout = 0
        while True:
            vm = vm_service.get()
            if vm.status == types.VmStatus.DOWN:
                break
            else:
                timeout += 5
                sleep(5)
                common.pprint("Waiting for vm to be ready", color='green')
            if timeout > 60:
                return {'result': 'failure', 'reason': 'timeout waiting for vm to be ready'}
        profiles_service = self.conn.system_service().vnic_profiles_service()
        netprofiles = {}
        for prof in profiles_service.list():
                netprofiles[prof.name] = prof.id
        if 'default' not in netprofiles and 'ovirtmgmt' in netprofiles:
            netprofiles['default'] = netprofiles['ovirtmgmt']
        nics_service = self.vms_service.vm_service(vm.id).nics_service()
        nic_configurations = []
        for index, net in enumerate(nets):
            netname = None
            netmask = None
            mac = None
            if isinstance(net, str):
                netname = net
            elif isinstance(net, dict) and 'name' in net:
                netname = net['name']
                ip = None
                mac = net.get('mac')
                netmask = next((e for e in [net.get('mask'), net.get('netmask')] if e is not None), None)
                gateway = net.get('gateway')
                noconf = net.get('noconf')
                if noconf is not None:
                    continue
                if 'ip' in net:
                    ip = net['ip']
                # if 'alias' in net:
                #    alias = net['alias']
                if ips and len(ips) > index and ips[index] is not None:
                    ip = ips[index]
                if ip is not None and netmask is not None and gateway is not None:
                    nic_configuration = types.NicConfiguration(name='eth%s' % index, on_boot=True,
                                                               boot_protocol=types.BootProtocol.STATIC,
                                                               ip=types.Ip(version=types.IpVersion.V4, address=ip,
                                                                           netmask=netmask, gateway=gateway))
                    nic_configurations.append(nic_configuration)
            if netname is not None and netname in netprofiles:
                profile_id = netprofiles[netname]
                nics_service.add(types.Nic(name='eth%s' % index, mac=mac,
                                           vnic_profile=types.VnicProfile(id=profile_id)))
        for index, disk in enumerate(disks):
            diskpool = pool
            diskthin = True
            disksize = '10'
            if index == 0 and template is not None:
                continue
            if isinstance(disk, int):
                disksize = disk
            elif isinstance(disk, dict):
                disksize = disk.get('size', disksize)
                diskpool = disk.get('pool', pool)
                diskthin = disk.get('thin', diskthin)
            self.add_disk(name, disksize, pool=diskpool, thin=diskthin)
        initialization = None
        if cloudinit:
            custom_script = ''
            if files:
                data = common.process_files(files=files, overrides=overrides)
                if data != '':
                    custom_script += "write_files:\n"
                    custom_script += data
            cmds.append('sleep 60')
            if template.lower().startswith('centos'):
                cmds.append('yum -y install centos-release-ovirt42')
            if template.lower().startswith('centos') or template.lower().startswith('fedora')\
                    or template.lower().startswith('rhel'):
                cmds.append('yum -y install ovirt-guest-agent-common')
                cmds.append('systemctl enable ovirt-guest-agent')
                cmds.append('systemctl start ovirt-guest-agent')
            if template.lower().startswith('debian'):
                cmds.append('echo "deb http://download.opensuse.org/repositories/home:/evilissimo:/deb/Debian_7.0/ ./" '
                            '>> /etc/apt/sources.list')
                cmds.append('gpg -v -a --keyserver http://download.opensuse.org/repositories/home:/evilissimo:/deb/'
                            'Debian_7.0/Release.key --recv-keys D5C7F7C373A1A299')
                cmds.append('gpg --export --armor 73A1A299 | apt-key add -')
                cmds.append('apt-get update')
                cmds.append('apt-get -Y install ovirt-guest-agent')
                cmds.append('service ovirt-guest-agent enable')
                cmds.append('service ovirt-guest-agent start')
            if [x for x in common.ubuntus if x in template.lower()]:
                cmds.append('echo deb http://download.opensuse.org/repositories/home:/evilissimo:/ubuntu:/16.04/'
                            'xUbuntu_16.04/ /')
                cmds.append('wget http://download.opensuse.org/repositories/home:/evilissimo:/ubuntu:/16.04/'
                            'xUbuntu_16.04//Release.key')
                cmds.append('apt-key add - < Release.key')
                cmds.append('apt-get update')
                cmds.append('apt-get -Y install ovirt-guest-agent')
            data = common.process_cmds(cmds=cmds, overrides=overrides)
            custom_script += "runcmd:\n"
            custom_script += data
            custom_script = None if custom_script == '' else custom_script
            root_password = 'unix1234'
            dns_servers = '8.8.8.8 1.1.1.1'
            key = get_home_ssh_key()
            initialization = types.Initialization(user_name='root', root_password=root_password,
                                                  authorized_ssh_keys=key, host_name=name,
                                                  nic_configurations=nic_configurations,
                                                  dns_servers=dns_servers, dns_search=domain,
                                                  custom_script=custom_script)
            tags_service = self.conn.system_service().tags_service()
            existing_tags = [tag.name for tag in tags_service.list()]
            if "profile_%s" % profile not in existing_tags:
                tags_service.add(types.Tag(name="profile_%s" % profile))
            if "plan_%s" % plan not in existing_tags:
                tags_service.add(types.Tag(name="plan_%s" % plan))
            tags_service = vm_service.tags_service()
            tags_service.add(tag=types.Tag(name="profile_%s" % profile))
            tags_service.add(tag=types.Tag(name="plan_%s" % plan))
        vm_service.start(use_cloud_init=cloudinit, vm=types.Vm(initialization=initialization))
        return {'result': 'success'}

    def start(self, name):
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vminfo = vmsearch[0]
        if str(vminfo.status) == 'down':
            vm = self.vms_service.vm_service(vmsearch[0].id)
            vm.start()
        return {'result': 'success'}

    def stop(self, name):
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vminfo = vmsearch[0]
        if str(vminfo.status) == 'up':
            vm = self.vms_service.vm_service(vmsearch[0].id)
            vm.stop()
        return {'result': 'success'}

    def snapshot(self, name, base, revert=False, delete=False, listing=False):
        vmsearch = self.vms_service.list(search='name=%s' % base)
        if not vmsearch:
            common.pprint("VM %s not found" % base, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % base}
        vm = vmsearch[0]
        snapshots_service = self.vms_service.vm_service(vm.id).snapshots_service()
        snapshots_service.add(types.Snapshot(description=name))
        return

    def restart(self, name):
        print("not implemented")
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vm = vmsearch[0]
        print(vm)
        return {'result': 'success'}

    def report(self):
        print("not implemented")
        return

    def status(self, name):
        print("not implemented")
        return

    def list(self):
        vms = []
        conn = self.conn
        vmslist = self.vms_service.list()
        for vm in vmslist:
            name = vm.name
            state = vm.status
            template = conn.follow_link(vm.template)
            source = template.name
            plan = ''
            profile = ''
            report = 'N/A'
            vm_service = self.vms_service.vm_service(vm.id)
            tags_service = vm_service.tags_service()
            for tag in tags_service.list():
                if tag.name.startswith('plan_'):
                    plan = tag.name.split('_')[1]
                if tag.name.startswith('profile_'):
                    profile = tag.name.split('_')[1]
            ips = []
            devices = self.vms_service.vm_service(vm.id).reported_devices_service().list()
            for device in devices:
                if device.ips:
                    for ip in device.ips:
                        if str(ip.version) == 'v4' and ip.address not in ['172.17.0.1', '127.0.0.1']:
                            ips.append(ip.address)
            ip = ips[-1] if ips else ''
            vms.append([name, state, ip, source, plan, profile, report])
        return vms

    def console(self, name, tunnel=False):
        tunnel = False
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vm = vmsearch[0]
        vm_service = self.vms_service.vm_service(vm.id)
        consoles_service = vm_service.graphics_consoles_service()
        consoles = consoles_service.list(current=True)
        for c in consoles:
            if str(c.protocol) == 'spice':
                console_service = consoles_service.console_service(c.id)
                ticket = console_service.ticket()
                ocacontent = open(self.ca_file).read().replace('\n', '\\n')
                subject = 'O=%s,CN=%s' % (self.org, c.address)
                if tunnel:
                    localport1 = common.get_free_port()
                    localport2 = common.get_free_port()
                    command = "ssh -o LogLevel=QUIET -f -p %s -L %s:127.0.0.1:%s -L %s:127.0.0.1:%s %s@%s sleep 10"\
                        % (self.port, localport1, c.port, localport2, c.tls_port, self.ssh_user, self.host)
                    os.popen(command)
                address = '127.0.0.1' if tunnel else c.address
                port = localport1 if tunnel else c.port
                sport = localport2 if tunnel else c.tls_port
                connectiondetails = """[virt-viewer]
type=spice
host={address}
port={port}
password={ticket}
tls-port={sport}
fullscreen=0
title={name}:%d
enable-smartcard=0
enable-usb-autoshare=1
delete-this-file=0
usb-filter=-1,-1,-1,-1,0
tls-ciphers=DEFAULT
host-subject={subject}
ca={ocacontent}
toggle-fullscreen=shift+f11
release-cursor=shift+f12
secure-attention=ctrl+alt+end
secure-channels=main;inputs;cursor;playback;record;display;usbredir;smartcard""".format(subject=subject,
                                                                                        ocacontent=ocacontent,
                                                                                        address=address,
                                                                                        port=port,
                                                                                        sport=sport,
                                                                                        ticket=ticket.value,
                                                                                        name=name)
            elif str(c.protocol) == 'vnc':
                if tunnel:
                    localport1 = common.get_free_port()
                    command = "ssh -o LogLevel=QUIET -f -p %s -L %s:127.0.0.1:%s %s@%s sleep 10"\
                        % (self.port, localport1, c.port, self.ssh_user, self.host)
                    os.popen(command)
                port = localport1 if tunnel else c.port
                connectiondetails = """[virt-viewer]
type=vnc
host={address}
port={port}
password={ticket}
title={name}:%d
delete-this-file=0
toggle-fullscreen=shift+f11
release-cursor=shift+f12""".format(address=c.address, port=port, ticket=ticket.value, name=name)
        with open("/tmp/console.vv", "w") as f:
            f.write(connectiondetails)
        os.popen("remote-viewer /tmp/console.vv &")
        return

    def serialconsole(self, name):
        # localport1 = common.get_free_port()
        #    command = "ssh -o LogLevel=QUIET -f -p %s -L %s:127.0.0.1:2222  ovirt-vmconsole@%s sleep 10"\
        #        % (self.port, localport, self.host)
        #    os.popen(command)
        system_service = self.conn.system_service()
        users_service = system_service.users_service()
        user = users_service.list(search='usrname=%s-authz' % self.user)[0]
        user_service = users_service.user_service(user.id)
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vm = vmsearch[0]
        # if not vm.console.enabled:
        #    vm_service = self.vms_service.vm_service(vm.id)
        #    vm_service.update(types.Vm(console=types.Console(enabled=True)))
        #    common.pprint("Enabling Serial Console. You will need to reboot VM" % name, color='green')
        #    return
        permissions_service = self.vms_service.vm_service(vm.id).permissions_service()
        permissions_service.add(types.Permission(user=types.User(id=user.id), role=types.Role(name='UserVmManager')))
        keys_service = user_service.ssh_public_keys_service()
        key = get_home_ssh_key()
        if key is None:
            common.print("neither id_rsa.pub or id_dsa public keys found in your .ssh directory. This is required")
            return
        try:
            keys_service.add(key=types.SshPublicKey(content=key))
        except:
            pass
        command = "ssh -t -p 2222 ovirt-vmconsole@%s connect --vm-name %s" % (self.host, name)
        call(command, shell=True)
        return

    def info(self, name, output='plain', fields=None, values=False):
        conn = self.conn
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vm = vmsearch[0]
        if self.debug:
            print(vars(vm))
        vm_service = self.vms_service.vm_service(vm.id)
        yamlinfo = {'name': vm.name, 'disks': [], 'nets': []}
        host = conn.follow_link(vm.host)
        yamlinfo['host'] = host.name
        # yamlinfo['autostart'] = ''
        yamlinfo['status'] = vm.status
        tags_service = vm_service.tags_service()
        for tag in tags_service.list():
            if tag.name.startswith('plan_'):
                yamlinfo['plan'] = tag.name.split('_')[1]
            if tag.name.startswith('profile_'):
                yamlinfo['profile'] = tag.name.split('_')[1]
        template = conn.follow_link(vm.template)
        source = template.name
        yamlinfo['template'] = source
        yamlinfo['memory'] = int(vm._memory / 1024 / 1024)
        cores = vm.cpu.topology.cores
        # sockets = vm.cpu.topology.sockets
        yamlinfo['cpus'] = cores
        yamlinfo['creationdate'] = vm._creation_time.strftime("%d-%m-%Y %H:%M")
        devices = self.vms_service.vm_service(vm.id).reported_devices_service().list()
        ips = []
        for device in devices:
            if device.ips:
                for ip in device.ips:
                    if str(ip.version) == 'v4' and ip.address not in ['172.17.0.1', '127.0.0.1']:
                        ips.append(ip.address)
        nics = self.vms_service.vm_service(vm.id).nics_service().list()
        profiles_service = self.conn.system_service().vnic_profiles_service()
        netprofiles = {}
        if ips:
            yamlinfo['ip'] = ips[-1]
        for profile in profiles_service.list():
                netprofiles[profile.id] = profile.name
        for nic in nics:
            device = nic.name
            mac = nic.mac.address
            network = netprofiles[nic.vnic_profile.id]
            network_type = nic.interface
            yamlinfo['nets'].append({'device': device, 'mac': mac, 'net': network, 'type': network_type})
        attachments = self.vms_service.vm_service(vm.id).disk_attachments_service().list()
        for attachment in attachments:
            disk = conn.follow_link(attachment.disk)
            device = disk.name
            disksize = int(disk.provisioned_size / 2**30)
            diskformat = disk.format
            drivertype = disk.content_type
            path = disk.id
            yamlinfo['disks'].append({'device': device, 'size': disksize, 'format': diskformat, 'type': drivertype,
                                      'path': path})

        common.print_info(yamlinfo, output=output, fields=fields, values=values)
        return {'result': 'success'}

# should return ip string
    def ip(self, name):
        print("not implemented")
        return None

    def volumes(self, iso=False):
        if iso:
            return []
            isos = []
            for pool in self.conn.system_service().storage_domains_service().list():
                if str(pool.type) == 'iso':
                    continue
            return isos
        else:
            templates = []
            templates_service = self.templates_service
            templateslist = templates_service.list()
            for template in templateslist:
                if template.name != 'Blank':
                    templates.append(template.name)
            return templates

    def delete(self, name, snapshots=False):
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vminfo = vmsearch[0]
        vm = self.vms_service.vm_service(vminfo.id)
        if str(vminfo.status) == 'up':
            vm.stop()
        vm.remove()
        return {'result': 'success'}

    def clone(self, old, new, full=False, start=False):
        print("not implemented")
        return

    def update_metadata(self, name, metatype, metavalue):
        print("not implemented")
        return

    def update_memory(self, name, memory):
        print("not implemented")
        return

    def update_cpu(self, name, numcpus):
        print("not implemented")
        return

    def update_start(self, name, start=True):
        print("not implemented")
        return

    def update_information(self, name, information):
        print("not implemented")
        return

    def update_iso(self, name, iso):
        print("not implemented")
        return

    def create_disk(self, name, size, pool=None, thin=True, template=None):
        print("not implemented")
        return

    def add_disk(self, name, size, pool=None, thin=True, template=None,
                 shareable=False, existing=None):
        size *= 2**30
        system_service = self.conn.system_service()
        sds_service = system_service.storage_domains_service()
        poolcheck = sds_service.list(search='name=%s' % pool)
        if not poolcheck:
            return {'result': 'failure', 'reason': "Pool %s not found" % pool}
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return {'result': 'failure', 'reason': "VM %s not found" % name}
        vm = self.vms_service.vm_service(vmsearch[0].id)
        disk_attachments_service = vm.disk_attachments_service()
        currentdisk = len(disk_attachments_service.list())
        diskindex = currentdisk + 1
        diskname = '%s_Disk%s' % (name, diskindex)
        disk_attachment = disk_attachments_service.add(types.DiskAttachment(disk=types.Disk(name=diskname,
                                                                                            format=types.DiskFormat.COW,
                                                                                            provisioned_size=size,
                                                                                            storage_domains=[
                                                                                                types.StorageDomain(
                                                                                                    name=pool)]),
                                                                            interface=types.DiskInterface.VIRTIO,
                                                                            bootable=False, active=True))
        disks_service = self.conn.system_service().disks_service()
        disk_service = disks_service.disk_service(disk_attachment.disk.id)
        timeout = 0
        while True:
            disk = disk_service.get()
            if disk.status == types.DiskStatus.OK:
                break
            else:
                timeout += 5
                sleep(5)
                common.pprint("Waiting for disk %s to be ready" % diskname, color='green')
            if timeout > 40:
                return {'result': 'failure', 'reason': 'timeout waiting for disk %s to be ready' % diskname}

    def delete_disk(self, name, diskname):
        print("not implemented")
        return

# should return a dict of {'pool': poolname, 'path': name}
    def list_disks(self):
        print("not implemented")
        return

    def add_nic(self, name, network):
        print("not implemented")
        return

    def delete_nic(self, name, interface):
        print("not implemented")
        return

# should return (user, ip)
    def _ssh_credentials(self, name):
        ip = ''
        user = 'root'
        vmsearch = self.vms_service.list(search='name=%s' % name)
        if not vmsearch:
            common.pprint("VM %s not found" % name, color='red')
            return 'root', None
        vm = vmsearch[0]
        ips = []
        devices = self.vms_service.vm_service(vm.id).reported_devices_service().list()
        for device in devices:
            if device.ips:
                for i in device.ips:
                    ips.append(i.address)
        if not ips:
            common.print("No ip found. Cannot ssh...", color='red')
        else:
            ip = ips[-1]
        return user, ip

    def ssh(self, name, user=None, local=None, remote=None, tunnel=False,
            insecure=False, cmd=None, X=False, D=None):
        u, ip = self._ssh_credentials(name)
        if ip == '':
            return None
        sshcommand = common.ssh(name, ip=ip, host=self.host, port=self.port, hostuser=self.ssh_user, user=u,
                                local=local, remote=remote, tunnel=tunnel, insecure=insecure, cmd=cmd, X=X,
                                debug=self.debug)
        return sshcommand

    def scp(self, name, user=None, source=None, destination=None, tunnel=False,
            download=False, recursive=False):
        u, ip = self._ssh_credentials(name)
        if ip == '':
            return None
        scpcommand = common.scp(name, ip=ip, host=self.host, port=self.port,
                                hostuser=self.user, user=user, source=source, destination=destination,
                                recursive=recursive, tunnel=tunnel, debug=self.debug, download=False)
        return scpcommand

    def create_pool(self, name, poolpath, pooltype='dir', user='qemu'):
        print("not implemented")
        return

    def add_image(self, image, pool, short=None, cmd=None, name=None, size=1):
        image = os.path.basename(image)
        if image not in otemplates:
            return {'result': 'failure', 'reason': "Image not supported"}
        if image in self.volumes():
            common.pprint("Image %s already there" % image)
            return {'result': 'success'}
        system_service = self.conn.system_service()
        sds_service = system_service.storage_domains_service()
        poolcheck = sds_service.list(search='name=%s' % pool)
        if not poolcheck:
            return {'result': 'failure', 'reason': "Pool %s not found" % pool}
        sd = sds_service.list(search='name=%s' % self.imagerepository)
        common.pprint("Using %s glance repository" % self.imagerepository, color='green')
        if not sd:
            common.confirm("No glance repo found. Do you want public glance repo to be installed?")
            providers_service = system_service.openstack_image_providers_service()
            sd_service = providers_service.add(provider=types.OpenStackImageProvider(name='ovirt-image-repository',
                                                                                     url='http://glance.ovirt.org:9292',
                                                                                     requires_authentication=False))
            return {'result': 'success'}
        else:
            sd_service = sds_service.storage_domain_service(sd[0].id)
        images_service = sd_service.images_service()
        images = images_service.list()
        imageobject = next((i for i in images if i.name == otemplates[image]), None)
        image_service = images_service.image_service(imageobject.id)
        image_service.import_(import_as_template=True, template=types.Template(name=image),
                              cluster=types.Cluster(name=self.cluster),
                              storage_domain=types.StorageDomain(name=pool))
        return {'result': 'success'}

    def create_network(self, name, cidr=None, dhcp=True, nat=True, domain=None,
                       plan='kvirt', pxe=None, vlan=None):
        if vlan is None:
            return {'result': 'failure', 'reason': "Missing Vlan"}
        networks_service = self.conn.system_service().networks_service()
        networks_service.add(network=types.Network(name=name, data_center=types.DataCenter(name=self.datacenter),
                                                   vlan=types.Vlan(vlan), usages=[types.NetworkUsage.VM], mtu=1500))
        return

    def delete_network(self, name=None, cidr=None):
        print("not implemented")
        return

# should return a dict of pool strings
    def list_pools(self):
        return [pool.name for pool in self.conn.system_service().storage_domains_service().list()]

    def list_networks(self):
        print("not implemented")
        return {}

    def list_subnets(self):
        print("not implemented")
        return {}

    def delete_pool(self, name, full=False):
        print("not implemented")
        return

    def network_ports(self, name):
        print("not implemented")
        return

    def vm_ports(self, name):
        print("not implemented")
        return

# returns the path of the pool, if it makes sense. used by kcli list --pools
    def get_pool_path(self, pool):
        poolsearch = self.conn.system_service().storage_domains_service().list(search='name=%s' % pool)
        if not poolsearch:
            common.pprint("Pool %s not found" % pool, color='red')
            return {'result': 'failure', 'reason': "Pool %s not found" % pool}
        pool = poolsearch[0]
        return pool.storage.path
