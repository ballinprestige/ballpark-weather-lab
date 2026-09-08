from pathlib import Path


def test_digest_publication_is_verified_before_and_after_registry_copy() -> None:
    text = (Path(__file__).parents[1] / ".github/workflows/publish-runtime-image.yml").read_text()
    assert "--preserve-digests --format oci" in text
    assert 'docker://${IMAGE}@${OCI_MANIFEST_DIGEST}' in text
    assert "verify-artifacts" in text
    assert text.index("verify-artifacts") < text.index("copy --preserve-digests")
    assert text.index("copy --preserve-digests") < text.rindex("verify-artifacts")
    assert "docker pull \"${IMAGE}@${OCI_MANIFEST_DIGEST}\"" in text
    assert (
        'copy --preserve-digests "docker://${IMAGE}@${OCI_MANIFEST_DIGEST}" '
        "oci:/work/remote-oci:reviewed"
    ) in text
    assert (
        'cmp -- "reviewed-oci/blobs/sha256/${oci_config_hex}" '
        '"remote-oci/blobs/sha256/${oci_config_hex}"'
    ) in text
    assert "/packages?package_type=container" not in text
    assert "list-tags \"docker://${IMAGE}\"" in text
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
