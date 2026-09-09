from pathlib import Path


def test_digest_publication_is_verified_before_and_after_registry_copy() -> None:
    text = (Path(__file__).parents[1] / ".github/workflows/publish-runtime-image.yml").read_text()
    assert "--preserve-digests --format oci" in text
    assert 'docker://${IMAGE}@${OCI_MANIFEST_DIGEST}' in text
    assert "verify-artifacts" in text
    publish_copy = "copy --authfile /tmp/ghcr-auth.json --preserve-digests --format oci"
    assert text.index("verify-artifacts") < text.index(publish_copy)
    assert text.index(publish_copy) < text.rindex("verify-artifacts")
    assert "docker pull \"${IMAGE}@${OCI_MANIFEST_DIGEST}\"" in text
    assert (
        "copy --authfile /tmp/ghcr-auth.json --preserve-digests "
        '"docker://${IMAGE}@${OCI_MANIFEST_DIGEST}" '
        "oci:/work/remote-oci:reviewed"
    ) in text
    assert (
        'cmp -- "reviewed-oci/blobs/sha256/${oci_config_hex}" '
        '"remote-oci/blobs/sha256/${oci_config_hex}"'
    ) in text
    assert "/packages?package_type=container" not in text
    assert "list-tags --authfile /tmp/ghcr-auth.json \"docker://${IMAGE}\"" in text
    assert "scripts/sanitize_registry_tags.py" in text
    assert 'printf \'{"state":"unavailable"}\\n\'' in text
    assert "reviewed_revision_tag_present == false" not in text
    assert 'docker run --rm --network none "$SKOPEO_IMAGE" --version' in text
    assert "publication-diagnostics/skopeo-runtime-identity.json" in text
    assert "skopeo-version.txt" in text
    assert '--arg skopeo_image "$SKOPEO_IMAGE"' in text
    assert '--argjson skopeo_version "$skopeo_version"' in text
    assert "skopeo_image:$skopeo_image" in text
    assert "skopeo_version:$skopeo_version" in text


def test_networked_skopeo_commands_use_only_the_explicit_read_only_authfile() -> None:
    text = (Path(__file__).parents[1] / ".github/workflows/publish-runtime-image.yml").read_text()
    mount = '-v "$HOME/.docker/config.json:/tmp/ghcr-auth.json:ro"'
    preflight = "login --authfile /tmp/ghcr-auth.json --get-login ghcr.io"
    commands = (
        preflight,
        "copy --authfile /tmp/ghcr-auth.json --preserve-digests --format oci",
        "inspect --authfile /tmp/ghcr-auth.json --raw",
        'copy --authfile /tmp/ghcr-auth.json --preserve-digests "docker://${IMAGE}@${OCI_MANIFEST_DIGEST}"',
        "list-tags --authfile /tmp/ghcr-auth.json \"docker://${IMAGE}\"",
    )

    assert mount in text
    assert "$HOME/.docker:/root/.docker:ro" not in text
    assert text.index(preflight) < text.index(commands[1])
    assert text.index(preflight) < text.index('printf \'{"push_attempted":true')
    preflight_step = text[
        text.rfind("Verify the mounted GHCR credential identity") : text.index(preflight)
    ]
    assert "--network none" in preflight_step
    for command in commands:
        command_start = text.index(command)
        assert mount in text[max(0, command_start - 260) : command_start]
    assert text.count("--authfile /tmp/ghcr-auth.json") == len(commands)


def test_container_smoke_exercises_the_pinned_authfile_lookup_without_network() -> None:
    text = (Path(__file__).parents[1] / ".github/workflows/container-smoke.yml").read_text()
    step_start = text.index("Prove pinned Skopeo uses only an explicit offline authfile")
    step_end = text.index("Prove fixture worker iteration", step_start)
    step = text[step_start:step_end]

    pinned = (
        "quay.io/skopeo/stable@sha256:"
        "db4108427c05acbadd1447316caa9b5f097a9a737d897d2297443baedff37ded"
    )
    assert pinned in step
    assert 'docker pull "$SKOPEO_IMAGE"' in step
    assert "--network none" in step
    assert '-v "$authfile:/tmp/ghcr-auth.json:ro"' in step
    assert "login --authfile /tmp/ghcr-auth.json --get-login ghcr.io" in step
    assert '[[ "$observed_actor" == "test-user" ]]' in step
    assert '-v "$authfile:/root/.docker/config.json:ro"' in step
    assert "login --get-login ghcr.io >/dev/null 2>&1" in step
    assert "unexpectedly used the legacy Docker config fallback" in step
    assert "upload-artifact" not in step
    assert "cat \"$authfile\"" not in step
