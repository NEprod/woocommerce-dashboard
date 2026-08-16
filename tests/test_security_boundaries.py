from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_sensitive_runtime_patterns_are_ignored():
    gitignore = (ROOT / ".gitignore").read_text()
    for pattern in (
        ".env",
        "instance/",
        "*.db",
        ".scanned",
        ".scanned.pending",
        ".update",
        "sku_index.json",
        "backups/",
        "*.log",
    ):
        assert pattern in gitignore

    dockerignore = (ROOT / ".dockerignore").read_text()
    assert ".scanned.pending" in dockerignore


def test_docker_drops_to_non_root_single_worker_without_debug_mode():
    dockerfile = (ROOT / "Dockerfile").read_text()
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text()
    assert "USER app" not in dockerfile
    assert 'ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]' in dockerfile
    assert 'exec gosu "${PUID}:${PGID}" "$@"' in entrypoint
    assert '"--workers", "1"' in dockerfile
    assert '"--threads", "4"' in dockerfile
    assert "debug" not in dockerfile.lower()


def test_environment_example_contains_placeholders_not_local_values():
    example = (ROOT / ".env.example").read_text()
    assert "DISCORD_ENABLED=false" in example
    assert "replace-with-a-long-random-value" in example
    assert "/Users/" not in example
    assert "discord.com/api/webhooks" not in example
