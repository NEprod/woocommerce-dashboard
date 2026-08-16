import os
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "entrypoint.sh"
DOCKERFILE = ROOT / "Dockerfile"
COMPOSE = ROOT / "compose.yaml"
UNRAID_TEMPLATE = ROOT / "unraid" / "my-woocommerce-dashboard.xml"


def _validate_config(**overrides):
    environment = os.environ.copy()
    for name in ("PUID", "PGID", "UMASK"):
        environment.pop(name, None)
    environment.update(overrides)
    return subprocess.run(
        ["sh", str(ENTRYPOINT), "--validate-config"],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_entrypoint_uses_backward_compatible_defaults():
    result = _validate_config()

    assert result.returncode == 0
    assert result.stdout.strip() == "PUID=100 PGID=100 UMASK=002"
    assert result.stderr == ""


def test_entrypoint_accepts_unraid_and_custom_non_root_ids():
    unraid = _validate_config(PUID="99", PGID="100", UMASK="002")
    custom = _validate_config(PUID="1234", PGID="2345", UMASK="027")

    assert unraid.returncode == 0
    assert unraid.stdout.strip() == "PUID=99 PGID=100 UMASK=002"
    assert custom.returncode == 0
    assert custom.stdout.strip() == "PUID=1234 PGID=2345 UMASK=027"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"PUID": ""}, "PUID must be a non-zero numeric UID"),
        ({"PUID": "abc"}, "PUID must be a non-zero numeric UID"),
        ({"PUID": "0"}, "PUID must be a non-zero numeric UID"),
        ({"PGID": ""}, "PGID must be a non-zero numeric GID"),
        ({"PGID": "group"}, "PGID must be a non-zero numeric GID"),
        ({"PGID": "0"}, "PGID must be a non-zero numeric GID"),
        ({"UMASK": ""}, "UMASK must be three or four octal digits"),
        ({"UMASK": "22"}, "UMASK must be three or four octal digits"),
        ({"UMASK": "099"}, "UMASK must be three or four octal digits"),
    ],
)
def test_entrypoint_rejects_invalid_runtime_identity(overrides, message):
    result = _validate_config(**overrides)

    assert result.returncode != 0
    assert result.stdout == ""
    assert message in result.stderr
    assert "SECRET_KEY" not in result.stderr


def test_docker_runtime_uses_gosu_and_drops_privileges_before_application_load():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "gosu" in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert "USER app" not in dockerfile
    assert "chmod -R a+rX /app/app /app/migrations /app/config.py /app/run.py" in dockerfile
    assert 'exec gosu "${PUID}:${PGID}" "$@"' in entrypoint
    assert "umask \"${UMASK}\"" in entrypoint
    assert "mkdir -p /app/instance/backups" in entrypoint
    assert "chown -R" in entrypoint
    assert "chown -R \"${PUID}:${PGID}\" /app/instance" in entrypoint
    assert "chown -R \"${PUID}:${PGID}\" /catalogue" not in entrypoint
    assert "chown -R \"${PUID}:${PGID}\" /output" not in entrypoint


def test_compose_and_unraid_template_expose_consumed_permission_variables():
    compose = COMPOSE.read_text(encoding="utf-8")
    root = ET.parse(UNRAID_TEMPLATE).getroot()
    configs = {node.attrib["Name"]: node for node in root.findall("Config")}

    assert root.findtext("Repository") == "neprod/woocommerce-dashboard:0.2.3"
    expected = {"PUID": "99", "PGID": "100", "UMASK": "002"}
    for name, default in expected.items():
        node = configs[name]
        assert node.attrib["Target"] == name
        assert node.attrib["Default"] == default
        assert node.attrib["Type"] == "Variable"
        assert node.attrib["Required"] == "true"
        assert f'{name}: "${{{name}:-' in compose

    assert "UID used by the application process" in configs["PUID"].attrib[
        "Description"
    ]
    assert "GID used by the application process" in configs["PGID"].attrib[
        "Description"
    ]
    assert "permissions mask" in configs["UMASK"].attrib["Description"]
