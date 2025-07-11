# -*- coding: utf-8 -*-
"""
get bns
"""
import sys
import time
import concurrent.futures
PYTHON3 = False if sys.version_info < (3, 0) else True


if PYTHON3:
    import subprocess
else:
    import commands

bns_server_cache = {}

def _get_bns_server(bnsname):
    """Get Bns Server"""
    result = []
    retry = 3
    while retry > 0:
        cmd = "get_instance_by_service -ips %s" % bnsname
        if PYTHON3:
            res = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
            output = res.stdout.read()
        else:
            status, output = commands.getstatusoutput(cmd)
        if PYTHON3:
            output = str(output, encoding="utf-8")
        for ip_port in output.strip().split('\n'):
            items = ip_port.strip().split(' ')
            if len(items) >= 4 and items[3].isdigit() and int(items[3]) == 0:
                result.append((str(items[1]), int(items[2])))
        if len(result) > 0:
            return result
        retry -= 1
        time.sleep(0.1)
    return result


def get_bns_server(bnsname):
    """Get Bns Server with Cache"""
    now_ts = int(time.time())
    global bns_server_cache
    if bnsname in bns_server_cache and (now_ts - bns_server_cache[bnsname]["update_time"]) < 60:
        return bns_server_cache[bnsname]["bns"]
    else:
        bns = _get_bns_server(bnsname=bnsname)
        if len(bns) > 0:
            bns_server_cache[bnsname] = {"bns": bns, "update_time": int(time.time())}
            return bns
        # update bns failed
        else:
            # return from cache if exists
            if bnsname in bns_server_cache:
                return bns_server_cache[bnsname]["bns"]
    return []


if __name__ == "__main__":
    bns = "group.opera-default-wordcomprehension-word-all.wenku.all"
    bns = "group.python-ppt-fenji.pandora.all"
    bns = "group.python-ppt-online.pandora.all"
    bns = "group.gdp-wenchainapi-online.pandora.all"
    print(get_bns_server(bns))
