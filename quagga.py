# Copyright (C) 2016 Nippon Telegraph and Telephone Corporation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from base import *
from tempfile import NamedTemporaryFile
import jtextfsm as textfsm

class Quagga(Container):
    CONTAINER_NAME = None
    GUEST_DIR = '/root/config'


    def __init__(self, host_dir, conf, image='bgperf/quagga'):
        super(Quagga, self).__init__(self.CONTAINER_NAME, image, host_dir, self.GUEST_DIR, conf)

    @classmethod
    def build_image(cls, force=False, tag='bgperf/quagga', checkout='HEAD', nocache=False):
        cls.dockerfile = '''
FROM ubuntu:latest

WORKDIR /root

RUN apt-get update; apt-get upgrade -y
RUN apt-get install -y quagga python3 python3-pip sudo
RUN pip3 install matplotlib jtextfsm

RUN mkdir /var/run/quagga && chown quagga:quagga /var/run/quagga
RUN mkdir /var/log/quagga && chown quagga:quagga /var/log/quagga
RUN chown -R quagga /etc/quagga

ENV PATH "/usr/lib/quagga/:/sbin:/bin:/usr/sbin:/usr/bin"
'''.format(checkout)
        super(Quagga, cls).build_image(force, tag, nocache)


class QuaggaTarget(Quagga, Target):

    CONTAINER_NAME = 'bgperf_quagga_target'
    CONFIG_FILE_NAME = 'bgpd.conf'
#     SHOW_BGP_TEXTFSM_TEMPLATE = '''Value Neighbor (\d+.\d+.\d+.\d+.)
# Value Spk (\d+)
# Value AS (\d+)
# Value MsgRcvd (\d+)
# Value MsgSent (\d+)
# Value TblVer (\d+)
# Value InQ (\d+)
# Value OutQ (\d+)
# Value up_down (\d+:\d+:\d+)
# Value St (\S+)

# Start
#     ^${Neighbor}\s+${Spk}+\s+${AS}+\s+${MsgRcvd}\s+${MsgSent}+\s+${TblVer}+\s+${InQ}\s+${OutQ}+\s+${up_down}+\s+${St} -> Record
# 
# EOF'''
    SHOW_BGP_TEXTFSM_TEMPLATE = '''Value Neighbor (\d+.\d+.\d+.\d+.)
Value Spk (\d+)
Value AS (\d+)
Value MsgRcvd (\d+)
Value MsgSent (\d+)
Value TblVer (\d+)
Value InQ (\d+)
Value OutQ (\d+)
Value up_down (\d+:\d+:\d+)
Value St (\S+)

Start
  ^${Neighbor}\s+${Spk}+\s+${AS}+\s+${MsgRcvd}\s+${MsgSent}+\s+${TblVer}+\s+${InQ}\s+${OutQ}+\s+${up_down}+\s+${St} -> Record

EOF'''

    def write_config(self):

        config = """hostname bgpd
password zebra
router bgp {0}
bgp router-id {1}
""".format(self.conf['as'], self.conf['router-id'])

        def gen_neighbor_config(n):
            local_addr = n['local-address']
            c = """neighbor {0} remote-as {1}
neighbor {0} advertisement-interval 1
neighbor {0} route-server-client
neighbor {0} timers 30 90
""".format(local_addr, n['as'])
            if 'filter' in n:
                for p in (n['filter']['in'] if 'in' in n['filter'] else []):
                    c += 'neighbor {0} route-map {1} export\n'.format(local_addr, p)
            return c

        with open('{0}/{1}'.format(self.host_dir, self.CONFIG_FILE_NAME), 'w') as f:
            f.write(config)
            for n in list(flatten(t.get('neighbors', {}).values() for t in self.scenario_global_conf['testers'])) + [self.scenario_global_conf['monitor']]:
                f.write(gen_neighbor_config(n))

            if 'policy' in self.scenario_global_conf:
                seq = 10
                for k, v in self.scenario_global_conf['policy'].items():
                    match_info = []
                    for i, match in enumerate(v['match']):
                        n = '{0}_match_{1}'.format(k, i)
                        if match['type'] == 'prefix':
                            f.write(''.join('ip prefix-list {0} deny {1}\n'.format(n, p) for p in match['value']))
                            f.write('ip prefix-list {0} permit any\n'.format(n))
                        elif match['type'] == 'as-path':
                            f.write(''.join('ip as-path access-list {0} deny _{1}_\n'.format(n, p) for p in match['value']))
                            f.write('ip as-path access-list {0} permit .*\n'.format(n))
                        elif match['type'] == 'community':
                            f.write(''.join('ip community-list standard {0} permit {1}\n'.format(n, p) for p in match['value']))
                            f.write('ip community-list standard {0} permit\n'.format(n))
                        elif match['type'] == 'ext-community':
                            f.write(''.join('ip extcommunity-list standard {0} permit {1} {2}\n'.format(n, *p.split(':', 1)) for p in match['value']))
                            f.write('ip extcommunity-list standard {0} permit\n'.format(n))

                        match_info.append((match['type'], n))

                    f.write('route-map {0} permit {1}\n'.format(k, seq))
                    for info in match_info:
                        if info[0] == 'prefix':
                            f.write('match ip address prefix-list {0}\n'.format(info[1]))
                        elif info[0] == 'as-path':
                            f.write('match as-path {0}\n'.format(info[1]))
                        elif info[0] == 'community':
                            f.write('match community {0}\n'.format(info[1]))
                        elif info[0] == 'ext-community':
                            f.write('match extcommunity {0}\n'.format(info[1]))

                    seq += 10

    def get_startup_cmd(self):
        return '\n'.join(
            ['#!/bin/bash',
             'ulimit -n 65536',
             'cp {guest_dir}/{config_file_name} /etc/quagga/{config_file_name}',
             'service bgpd start']
        ).format(
            guest_dir=self.guest_dir,
            config_file_name=self.CONFIG_FILE_NAME)

    def get_version_cmd(self):
        return ['vtysh', '-c', 'show version', '|', 'head -1']

    def exec_version_cmd(self):
        ret = super().exec_version_cmd()
        return ret.split('\n')[0]


    def get_neighbors_state(self):
        neighbors_accepted = {}
        neighbors_received = {}

        f = NamedTemporaryFile(mode='w+', delete=False)
        f.write(self.SHOW_BGP_TEXTFSM_TEMPLATE)
        f.close()
        template=open(f.name)
        re_t=textfsm.TextFSM(template)

        neighbor_received_output = self.local("vtysh -c 'sh ip bgp summary'")
        if neighbor_received_output:
            fsm_res = re_t.ParseText(neighbor_received_output.decode('utf-8'))
            for item in fsm_res:
                neighbors_accepted[item[0].strip()] = int(item[9])

        return neighbors_received, neighbors_accepted
