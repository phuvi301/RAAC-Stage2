"""Opt-in, per-phase Requests services with no external or host gateway route."""

import io
import json
import tarfile
import time
import uuid

import docker

HTTPBIN_SERVER = b"""import ssl
import threading
import trustme
from httpbin import app
from werkzeug.serving import make_server

ca = trustme.CA()
ca.cert_pem.write_to_path('/tmp/raac-httpbin-ca.pem')
certificate = ca.issue_cert(u'httpbin.org', u'www.google.co.uk')
context = ssl.SSLContext(getattr(ssl, 'PROTOCOL_TLS_SERVER', ssl.PROTOCOL_TLSv1_2))
certificate.configure_cert(context)
https = make_server('0.0.0.0', 443, app, threaded=True, ssl_context=context)
thread = threading.Thread(target=https.serve_forever)
thread.daemon = True
thread.start()
make_server('0.0.0.0', 80, app, threaded=True).serve_forever()
"""

CA_SETUP = b"""import glob
import hashlib
import json
import os

changes = []
paths = ['/workspace/requests/cacert.pem'] + sorted(glob.glob(
    '/home/runner/.local/lib/python*/site-packages/certifi/cacert.pem'
))
for path in paths:
    if not os.path.isfile(path):
        continue
    if os.path.islink(path):
        raise ValueError('Fixture trust bundle must not be a symlink')
    with open(path, 'rb') as source:
        original = source.read()
    with open('/tmp/raac-httpbin-ca.pem', 'rb') as source:
        public_ca = source.read()
    updated = original + b'\\n' + public_ca
    with open(path, 'wb') as output:
        output.write(updated)
    changes.append({
        'path': path.replace('/workspace/', ''),
        'before_sha256': hashlib.sha256(original).hexdigest(),
        'after_sha256': hashlib.sha256(updated).hexdigest(),
        'purpose': 'temporary internal httpbin CA trust',
    })
with open('/tmp/raac-network.json', 'w') as output:
    json.dump({'trust_bundle': changes}, output)
"""


def archive_files(files):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name, content in files.items():
            item = tarfile.TarInfo(name)
            item.size = len(content)
            item.mode = 0o444
            tar.addfile(item, io.BytesIO(content))
    return stream.getvalue()


class NetworkFixture:
    def __init__(self, client, image, kind, directory, phase):
        if kind not in {"requests_httpbin", "requests_tarpit"}:
            raise ValueError("Unknown network fixture")
        self.client, self.image, self.kind = client, image, kind
        self.directory, self.phase = directory, phase
        self.network = self.service = None
        self.files, self.environment, self.options = {}, {}, {}
        self.report = {
            "fixture": kind,
            "network_mode": "isolated-bridge",
            "external_network": False,
            "host_gateway": False,
            "gateway_mode": "isolated",
            "subnet": "10.255.255.0/24",
            "tarpit": "10.255.255.1",
        }

    def start(self):
        version = str(self.client.version().get("Version", ""))
        major = version.partition(".")[0]
        if not major.isdigit() or int(major) < 28:
            raise RuntimeError(
                "Network fixtures require Docker Engine 28+ with isolated gateway mode"
            )
        self.network = self.client.networks.create(
            "raac-fixture-" + uuid.uuid4().hex,
            driver="bridge",
            internal=True,
            options={
                "com.docker.network.bridge.gateway_mode_ipv4": "isolated",
                "com.docker.network.bridge.gateway_mode_ipv6": "isolated",
            },
            ipam=docker.types.IPAMConfig(
                pool_configs=[
                    docker.types.IPAMPool(
                        subnet="10.255.255.0/24",
                        gateway="10.255.255.254",
                        aux_addresses={"tarpit": "10.255.255.1"},
                    )
                ]
            ),
            labels={"raac": self.directory.name},
        )
        self.options = {"network_mode": self.network.name, "dns": ["127.0.0.1"]}
        self.report["network_id"] = self.network.id
        if self.kind == "requests_tarpit":
            return
        self.service = self.client.containers.create(
            self.image.id,
            ["python", "-u", "/tmp/raac-httpbin-server.py"],
            working_dir="/tmp",
            network=self.network.name,
            dns=["127.0.0.1"],
            user="1000:1000",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            sysctls={"net.ipv4.ip_unprivileged_port_start": "0"},
            nano_cpus=500_000_000,
            mem_limit="512m",
            pids_limit=64,
            labels={"raac": self.directory.name},
            use_config_proxy=False,
        )
        self.service.put_archive(
            "/tmp", archive_files({"raac-httpbin-server.py": HTTPBIN_SERVER})
        )
        self.service.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            self.service.reload()
            if self.service.status in {"exited", "dead"}:
                raise RuntimeError("Internal httpbin failed; inspect phase service log")
            result = self.service.exec_run(
                [
                    "python",
                    "-c",
                    "try:\n from urllib.request import urlopen\nexcept ImportError:\n from urllib2 import urlopen\nurlopen('http://127.0.0.1/get', timeout=.5).read()",
                ],
                workdir="/tmp",
            )
            if result.exit_code == 0:
                break
            time.sleep(0.2)
        else:
            raise TimeoutError("Internal httpbin readiness timed out")
        chunks, _ = self.service.get_archive("/tmp/raac-httpbin-ca.pem")
        with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as tar:
            self.files["raac-httpbin-ca.pem"] = tar.extractfile(
                tar.getmembers()[0]
            ).read()
        self.service.reload()
        address = self.service.attrs["NetworkSettings"]["Networks"][self.network.name][
            "IPAddress"
        ]
        aliases = ["httpbin.org", "www.google.co.uk"]
        self.options["extra_hosts"] = dict.fromkeys(aliases, address)
        self.environment = {
            "HTTPBIN_URL": "http://httpbin.org/",
            "REQUESTS_CA_BUNDLE": "/tmp/raac-httpbin-ca.pem",
            "NO_PROXY": "httpbin.org,www.google.co.uk,127.0.0.1,localhost,10.255.255.1",
        }
        self.files["raac-network-setup.py"] = CA_SETUP
        self.report.update(
            service_image_id=self.image.id, httpbin_address=address, aliases=aliases
        )

    def collect(self, container):
        if self.kind != "requests_httpbin":
            return
        chunks, _ = container.get_archive("/tmp/raac-network.json")
        with tarfile.open(fileobj=io.BytesIO(b"".join(chunks))) as tar:
            raw = tar.extractfile(tar.getmembers()[0]).read()
        (self.directory / (self.phase + ".network.json")).write_bytes(raw)
        self.report.update(json.loads(raw))

    def close(self):
        try:
            if self.service is not None:
                try:
                    (self.directory / (self.phase + ".service.log")).write_bytes(
                        self.service.logs(stdout=True, stderr=True)
                    )
                finally:
                    self.service.remove(force=True)
        finally:
            if self.network is not None:
                self.network.remove()
