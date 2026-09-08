from __future__ import annotations

import gzip
import io
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_digest_image import (
    OCI_MANIFEST,
    VerificationError,
    bind,
    sha256_bytes,
)

REFERENCE = "example.invalid/ballpark-weather-lab:reviewed"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar"
OCI_LAYER_GZIP = "application/vnd.oci.image.layer.v1.tar+gzip"


@dataclass
class ImageFixture:
    archive: Path
    layout: Path
    remote: Path
    config_name: str
    layer_name: str
    docker_config: bytes
    oci_config: bytes
    layer_tar: bytes
    manifest: dict[str, Any]


def _json(value: object, *, sort_keys: bool = True) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")


def _config_with(
    config: bytes, key: str, value: object, *, sort_keys: bool = True
) -> bytes:
    parsed = json.loads(config)
    parsed[key] = value
    return _json(parsed, sort_keys=sort_keys)


def _tar_layer(payload: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        entry = tarfile.TarInfo("usr/share/ballpark/payload.txt")
        entry.size = len(payload)
        archive.addfile(entry, io.BytesIO(payload))
    return output.getvalue()


def _add_member(archive: tarfile.TarFile, name: str, value: bytes) -> None:
    entry = tarfile.TarInfo(name)
    entry.size = len(value)
    archive.addfile(entry, io.BytesIO(value))


def _blob_path(layout: Path, digest: str) -> Path:
    return layout / "blobs" / "sha256" / digest.removeprefix("sha256:")


def _write_blob(layout: Path, value: bytes) -> str:
    digest = sha256_bytes(value)
    path = _blob_path(layout, digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)
    return digest


def _read_index(fixture: ImageFixture) -> dict[str, Any]:
    return json.loads((fixture.layout / "index.json").read_bytes())


def _write_index(fixture: ImageFixture, index: dict[str, Any]) -> None:
    (fixture.layout / "index.json").write_bytes(_json(index))


def _replace_manifest(fixture: ImageFixture, manifest: dict[str, Any]) -> None:
    manifest_bytes = _json(manifest)
    digest = _write_blob(fixture.layout, manifest_bytes)
    index = _read_index(fixture)
    descriptor = index["manifests"][0]
    descriptor["digest"] = digest
    descriptor["size"] = len(manifest_bytes)
    _write_index(fixture, index)
    fixture.manifest = manifest


def _replace_oci_config(fixture: ImageFixture, value: bytes) -> None:
    digest = _write_blob(fixture.layout, value)
    fixture.manifest["config"]["digest"] = digest
    fixture.manifest["config"]["size"] = len(value)
    _replace_manifest(fixture, fixture.manifest)


def _rewrite_docker_archive(
    fixture: ImageFixture,
    *,
    manifest_bytes: bytes | None = None,
    config_name: str | None = None,
    config_bytes: bytes | None = None,
    layer_name: str | None = None,
    layer_bytes: bytes | None = None,
    layer_type: bytes | None = None,
) -> None:
    config_name = config_name or fixture.config_name
    layer_name = layer_name or fixture.layer_name
    config_bytes = config_bytes or fixture.docker_config
    layer_bytes = layer_bytes or fixture.layer_tar
    if manifest_bytes is None:
        manifest_bytes = _json(
            [
                {
                    "Config": config_name,
                    "RepoTags": [REFERENCE],
                    "Layers": [layer_name],
                }
            ]
        )
    with tarfile.open(fixture.archive, "w") as archive:
        _add_member(archive, "manifest.json", manifest_bytes)
        _add_member(archive, config_name, config_bytes)
        if layer_type is None:
            _add_member(archive, layer_name, layer_bytes)
        else:
            entry = tarfile.TarInfo(layer_name)
            entry.type = layer_type
            entry.linkname = "outside"
            archive.addfile(entry)


def _fixture(tmp_path: Path, *, payload: bytes = b"fixture payload") -> ImageFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    archive = tmp_path / "image.tar"
    layout = tmp_path / "oci"
    remote = tmp_path / "remote-manifest.json"
    layer_tar = _tar_layer(payload)
    diff_id = sha256_bytes(layer_tar)
    docker_config = _json(
        {
            "architecture": "amd64",
            "created": "2026-09-08T00:00:00Z",
            "os": "linux",
            "rootfs": {"diff_ids": [diff_id], "type": "layers"},
        }
    )
    # Conversion may reorder equal JSON fields, producing a distinct config digest.
    oci_config = (
        b'{"rootfs":{"type":"layers","diff_ids":["'
        + diff_id.encode("ascii")
        + b'"]},"os":"linux","created":"2026-09-08T00:00:00Z","architecture":"amd64"}'
    )
    config_name = "config.json"
    layer_name = "layers/one/layer.tar"
    _rewrite_docker_archive(
        ImageFixture(
            archive,
            layout,
            remote,
            config_name,
            layer_name,
            docker_config,
            oci_config,
            layer_tar,
            {},
        )
    )

    compressed_layer = gzip.compress(layer_tar, mtime=0)
    config_digest = _write_blob(layout, oci_config)
    layer_digest = _write_blob(layout, compressed_layer)
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST,
        "config": {"mediaType": OCI_CONFIG, "digest": config_digest, "size": len(oci_config)},
        "layers": [
            {
                "mediaType": OCI_LAYER_GZIP,
                "digest": layer_digest,
                "size": len(compressed_layer),
            }
        ],
    }
    manifest_bytes = _json(manifest)
    manifest_digest = _write_blob(layout, manifest_bytes)
    (layout / "oci-layout").write_bytes(_json({"imageLayoutVersion": "1.0.0"}))
    _write_index(
        ImageFixture(
            archive,
            layout,
            remote,
            config_name,
            layer_name,
            docker_config,
            oci_config,
            layer_tar,
            manifest,
        ),
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": OCI_MANIFEST,
                    "digest": manifest_digest,
                    "size": len(manifest_bytes),
                    "annotations": {"org.opencontainers.image.ref.name": "reviewed"},
                }
            ],
        },
    )
    remote.write_bytes(manifest_bytes)
    return ImageFixture(
        archive,
        layout,
        remote,
        config_name,
        layer_name,
        docker_config,
        oci_config,
        layer_tar,
        manifest,
    )


def _two_layer_fixture(tmp_path: Path) -> ImageFixture:
    fixture = _fixture(tmp_path)
    second_layer = _tar_layer(b"second layer")
    diff_ids = [sha256_bytes(fixture.layer_tar), sha256_bytes(second_layer)]
    docker_config = _json(
        {
            "architecture": "amd64",
            "created": "2026-09-08T00:00:00Z",
            "os": "linux",
            "rootfs": {"diff_ids": diff_ids, "type": "layers"},
        }
    )
    oci_config = _json(
        {
            "rootfs": {"type": "layers", "diff_ids": diff_ids},
            "os": "linux",
            "created": "2026-09-08T00:00:00Z",
            "architecture": "amd64",
        },
        sort_keys=False,
    )
    second_name = "layers/two/layer.tar"
    compressed_second = gzip.compress(second_layer, mtime=0)
    fixture.docker_config = docker_config
    fixture.oci_config = oci_config
    _rewrite_docker_archive(
        fixture,
        manifest_bytes=_json(
            [
                {
                    "Config": fixture.config_name,
                    "RepoTags": [REFERENCE],
                    "Layers": [fixture.layer_name, second_name],
                }
            ]
        ),
        config_bytes=docker_config,
    )
    with tarfile.open(fixture.archive, "a") as archive:
        _add_member(archive, second_name, second_layer)
    config_digest = _write_blob(fixture.layout, oci_config)
    second_digest = _write_blob(fixture.layout, compressed_second)
    fixture.manifest["config"] = {
        "mediaType": OCI_CONFIG,
        "digest": config_digest,
        "size": len(oci_config),
    }
    fixture.manifest["layers"].append(
        {"mediaType": OCI_LAYER_GZIP, "digest": second_digest, "size": len(compressed_second)}
    )
    _replace_manifest(fixture, fixture.manifest)
    fixture.remote.write_bytes(_json(fixture.manifest))
    return fixture


def _bind(fixture: ImageFixture, *, remote: Path | None = None) -> dict[str, object]:
    return bind(fixture.layout, fixture.archive, "reviewed", REFERENCE, remote)


def test_bind_accepts_semantically_equal_reordered_configs_with_distinct_digests(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    receipt = _bind(fixture, remote=fixture.remote)

    assert fixture.docker_config != fixture.oci_config
    assert sha256_bytes(fixture.docker_config) != sha256_bytes(fixture.oci_config)
    assert receipt == {
        "docker_archive_config_digest": sha256_bytes(fixture.docker_config),
        "docker_archive_rootfs_diff_ids": [sha256_bytes(fixture.layer_tar)],
        "oci_manifest_digest": sha256_bytes(fixture.remote.read_bytes()),
        "oci_config_digest": sha256_bytes(fixture.oci_config),
        "oci_layer_digests": [sha256_bytes(gzip.compress(fixture.layer_tar, mtime=0))],
        "oci_layer_descriptors": [
            {
                "mediaType": OCI_LAYER_GZIP,
                "digest": sha256_bytes(gzip.compress(fixture.layer_tar, mtime=0)),
                "size": len(gzip.compress(fixture.layer_tar, mtime=0)),
            }
        ],
        "remote_manifest_verified": True,
    }


def test_bind_accepts_equivalent_nested_config_objects_despite_key_order(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    extension = {"nested": {"enabled": True, "retries": 1}, "ordered": [1, 2, 3]}
    docker_config = _config_with(fixture.docker_config, "extension", extension)
    oci_config = _config_with(
        fixture.oci_config,
        "extension",
        {"ordered": [1, 2, 3], "nested": {"retries": 1, "enabled": True}},
        sort_keys=False,
    )
    _rewrite_docker_archive(fixture, config_bytes=docker_config)
    _replace_oci_config(fixture, oci_config)

    receipt = _bind(fixture)

    assert receipt["docker_archive_config_digest"] == sha256_bytes(docker_config)
    assert receipt["oci_config_digest"] == sha256_bytes(oci_config)


@pytest.mark.parametrize(
    ("docker_value", "oci_value"),
    [
        (True, 1),
        (1, 1.0),
        ({"nested": {"enabled": True}}, {"nested": {"enabled": 1}}),
        ([1, 2, 3], [3, 2, 1]),
    ],
    ids=("bool-vs-int", "int-vs-float", "nested-bool-vs-int", "list-order"),
)
def test_bind_rejects_config_type_or_list_order_changes(
    tmp_path: Path, docker_value: object, oci_value: object
) -> None:
    fixture = _fixture(tmp_path)
    docker_config = _config_with(fixture.docker_config, "extension", docker_value)
    oci_config = _config_with(fixture.oci_config, "extension", oci_value, sort_keys=False)
    _rewrite_docker_archive(fixture, config_bytes=docker_config)
    _replace_oci_config(fixture, oci_config)

    with pytest.raises(VerificationError, match="semantics"):
        _bind(fixture)


@pytest.mark.parametrize(
    ("target", "number"),
    [
        ("docker", "NaN"),
        ("oci", "NaN"),
        ("oci", "Infinity"),
        ("oci", "-Infinity"),
        ("oci", "1e9999"),
    ],
    ids=("docker-nan", "oci-nan", "oci-infinity", "oci-negative-infinity", "oci-overflow"),
)
def test_bind_rejects_nonfinite_json_numbers(tmp_path: Path, target: str, number: str) -> None:
    fixture = _fixture(tmp_path)
    original = fixture.docker_config if target == "docker" else fixture.oci_config
    invalid_config = original[:-1] + f',"invalid":{number}}}'.encode("ascii")
    if target == "docker":
        _rewrite_docker_archive(fixture, config_bytes=invalid_config)
    else:
        _replace_oci_config(fixture, invalid_config)

    with pytest.raises(VerificationError, match="non-finite JSON number"):
        _bind(fixture)


def test_bind_streams_a_practical_large_layer_without_loading_it_as_one_value(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, payload=b"a" * (5 * 1024 * 1024))

    receipt = _bind(fixture)

    assert receipt["docker_archive_rootfs_diff_ids"] == [sha256_bytes(fixture.layer_tar)]
    assert receipt["oci_layer_digests"] == [sha256_bytes(gzip.compress(fixture.layer_tar, mtime=0))]


def test_bind_reports_complete_ordered_oci_layer_descriptors(tmp_path: Path) -> None:
    fixture = _two_layer_fixture(tmp_path)

    receipt = _bind(fixture)

    assert receipt["oci_layer_descriptors"] == fixture.manifest["layers"]
    assert [descriptor["digest"] for descriptor in receipt["oci_layer_descriptors"]] == [
        descriptor["digest"] for descriptor in fixture.manifest["layers"]
    ]
    assert all(
        set(descriptor) == {"digest", "mediaType", "size"}
        and isinstance(descriptor["size"], int)
        and descriptor["mediaType"] == OCI_LAYER_GZIP
        for descriptor in receipt["oci_layer_descriptors"]
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda fixture: _rewrite_docker_archive(
                fixture,
                manifest_bytes=(
                    b'[{"Config":"config.json","Config":"other.json",'
                    b'"RepoTags":["example.invalid/ballpark-weather-lab:reviewed"],'
                    b'"Layers":["layers/one/layer.tar"]}]'
                ),
            ),
            "duplicate key",
        ),
        (
            lambda fixture: _rewrite_docker_archive(
                fixture,
                config_bytes=(
                    b'{"architecture":"amd64","architecture":"amd64","os":"linux",'
                    b'"rootfs":{"type":"layers","diff_ids":["'
                    + sha256_bytes(fixture.layer_tar).encode("ascii")
                    + b'"]}}'
                ),
            ),
            "duplicate key",
        ),
        (
            lambda fixture: _rewrite_docker_archive(
                fixture,
                manifest_bytes=_json(
                    [
                        {
                            "Config": fixture.config_name,
                            "RepoTags": ["example.invalid/other:reviewed"],
                            "Layers": [fixture.layer_name],
                        }
                    ]
                ),
            ),
            "RepoTags",
        ),
        (
            lambda fixture: _rewrite_docker_archive(fixture, config_name="../config.json"),
            "path",
        ),
        (
            lambda fixture: _rewrite_docker_archive(fixture, layer_type=tarfile.SYMTYPE),
            "path",
        ),
    ],
    ids=(
        "duplicate-docker-manifest-key",
        "duplicate-docker-config-key",
        "wrong-repotag",
        "traversal-config",
        "symlink-layer",
    ),
)
def test_bind_rejects_untrusted_docker_archive_metadata(
    tmp_path: Path, mutate: object, message: str
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture)  # type: ignore[operator]

    with pytest.raises(VerificationError, match=message):
        _bind(fixture)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda fixture: (fixture.layout / "oci-layout").write_bytes(
                _json({"imageLayoutVersion": "1.1.0"})
            ),
            "metadata",
        ),
        (
            lambda fixture: _write_index(
                fixture,
                {
                    "schemaVersion": 2,
                    "manifests": _read_index(fixture)["manifests"] * 2,
                },
            ),
            "index",
        ),
        (
            lambda fixture: _write_index(
                fixture,
                {**_read_index(fixture), "schemaVersion": 1},
            ),
            "index",
        ),
        (
            lambda fixture: _write_index(
                fixture,
                {
                    "schemaVersion": 2,
                    "manifests": [
                        {
                            **_read_index(fixture)["manifests"][0],
                            "annotations": {"org.opencontainers.image.ref.name": "wrong"},
                        }
                    ],
                },
            ),
            "index",
        ),
        (
            lambda fixture: _write_index(
                fixture,
                {
                    "schemaVersion": 2,
                    "manifests": [
                        {
                            **_read_index(fixture)["manifests"][0],
                            "mediaType": "application/vnd.oci.image.index.v1+json",
                        }
                    ],
                },
            ),
            "index",
        ),
        (
            lambda fixture: _write_index(
                fixture,
                {
                    "schemaVersion": 2,
                    "manifests": [{**_read_index(fixture)["manifests"][0], "size": 1}],
                },
            ),
            "descriptor",
        ),
    ],
    ids=(
        "layout-version",
        "extra-index-entry",
        "wrong-index-schema",
        "wrong-ref",
        "wrong-index-media",
        "index-size",
    ),
)
def test_bind_rejects_invalid_oci_layout_or_index(
    tmp_path: Path, mutate: object, message: str
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture)  # type: ignore[operator]

    with pytest.raises(VerificationError, match=message):
        _bind(fixture)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda fixture: (fixture.layout / "oci-layout").write_bytes(
            b'{"imageLayoutVersion":"1.0.0","imageLayoutVersion":"1.0.0"}'
        ),
        lambda fixture: (fixture.layout / "index.json").write_bytes(
            b'{"schemaVersion":2,"schemaVersion":2,"manifests":[]}'
        ),
        lambda fixture: (
            lambda value: (
                _write_blob(fixture.layout, value),
                _write_index(
                    fixture,
                    {
                        "schemaVersion": 2,
                        "manifests": [
                            {
                                **_read_index(fixture)["manifests"][0],
                                "digest": sha256_bytes(value),
                                "size": len(value),
                            }
                        ],
                    },
                ),
            )
        )(
            b'{"schemaVersion":2,"schemaVersion":2,"mediaType":"'
            + OCI_MANIFEST.encode("ascii")
            + b'","config":{},"layers":[]}'
        ),
    ],
    ids=("layout", "index", "manifest"),
)
def test_bind_rejects_duplicate_keys_in_every_oci_metadata_object(
    tmp_path: Path, mutate: object
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture)  # type: ignore[operator]

    with pytest.raises(VerificationError, match="duplicate key"):
        _bind(fixture)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda fixture: _replace_manifest(
                fixture,
                {**fixture.manifest, "mediaType": "application/vnd.oci.image.index.v1+json"},
            ),
            "manifest type",
        ),
        (
            lambda fixture: _replace_manifest(
                fixture,
                {
                    **fixture.manifest,
                    "config": {**fixture.manifest["config"], "mediaType": "wrong"},
                },
            ),
            "config descriptor",
        ),
        (
            lambda fixture: _replace_manifest(
                fixture,
                {**fixture.manifest, "config": {**fixture.manifest["config"], "size": 1}},
            ),
            "config blob",
        ),
        (
            lambda fixture: _replace_manifest(
                fixture,
                {
                    **fixture.manifest,
                    "layers": [{**fixture.manifest["layers"][0], "mediaType": "wrong"}],
                },
            ),
            "layer media",
        ),
        (
            lambda fixture: _replace_manifest(
                fixture,
                {
                    **fixture.manifest,
                    "layers": [{**fixture.manifest["layers"][0], "size": 1}],
                },
            ),
            "layer blob",
        ),
        (
            lambda fixture: _replace_manifest(fixture, {**fixture.manifest, "layers": []}),
            "layer count",
        ),
    ],
    ids=(
        "manifest-media",
        "config-media",
        "config-size",
        "layer-media",
        "layer-size",
        "layer-count",
    ),
)
def test_bind_rejects_invalid_manifest_descriptors(
    tmp_path: Path, mutate: object, message: str
) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture)  # type: ignore[operator]

    with pytest.raises(VerificationError, match=message):
        _bind(fixture)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda fixture: _replace_oci_config(
                fixture,
                fixture.oci_config.replace(b'"architecture":"amd64"', b'"architecture":"arm64"'),
            ),
            "semantics",
        ),
        (
            lambda fixture: _replace_oci_config(
                fixture,
                fixture.oci_config.replace(b'"os":"linux"', b'"os":"windows"'),
            ),
            "semantics",
        ),
        (
            lambda fixture: _replace_oci_config(
                fixture,
                b'{"architecture":"amd64","architecture":"arm64","os":"linux","rootfs":{"type":"layers","diff_ids":[]}}',
            ),
            "duplicate key",
        ),
    ],
    ids=("wrong-architecture", "wrong-os", "duplicate-oci-config-key"),
)
def test_bind_rejects_bad_config_semantics(tmp_path: Path, mutate: object, message: str) -> None:
    fixture = _fixture(tmp_path)
    mutate(fixture)  # type: ignore[operator]

    with pytest.raises(VerificationError, match=message):
        _bind(fixture)


def test_bind_rejects_a_semantically_equal_non_linux_amd64_config(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    docker_config = fixture.docker_config.replace(
        b'"architecture":"amd64"', b'"architecture":"arm64"'
    )
    oci_config = fixture.oci_config.replace(b'"architecture":"amd64"', b'"architecture":"arm64"')
    _rewrite_docker_archive(fixture, config_bytes=docker_config)
    _replace_oci_config(fixture, oci_config)

    with pytest.raises(VerificationError, match="semantics"):
        _bind(fixture)


def test_bind_rejects_missing_hash_mismatched_and_symlinked_oci_blobs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    layer_digest = fixture.manifest["layers"][0]["digest"]
    layer_path = _blob_path(fixture.layout, layer_digest)
    layer_path.unlink()
    with pytest.raises(VerificationError, match="missing"):
        _bind(fixture)


def test_bind_rejects_missing_or_hash_mismatched_manifest_and_config_blobs(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    config_digest = fixture.manifest["config"]["digest"]
    _blob_path(fixture.layout, config_digest).unlink()
    with pytest.raises(VerificationError, match="missing"):
        _bind(fixture)

    fixture = _fixture(tmp_path / "bad-config")
    config_digest = fixture.manifest["config"]["digest"]
    _blob_path(fixture.layout, config_digest).write_bytes(b"different config")
    with pytest.raises(VerificationError, match="config blob"):
        _bind(fixture)

    fixture = _fixture(tmp_path / "bad-manifest")
    descriptor = _read_index(fixture)["manifests"][0]
    _blob_path(fixture.layout, descriptor["digest"]).write_bytes(b"different manifest")
    with pytest.raises(VerificationError, match="manifest blob"):
        _bind(fixture)

    fixture = _fixture(tmp_path / "mismatched")
    layer_digest = fixture.manifest["layers"][0]["digest"]
    _blob_path(fixture.layout, layer_digest).write_bytes(b"different bytes")
    with pytest.raises(VerificationError, match="does not match"):
        _bind(fixture)

    fixture = _fixture(tmp_path / "symlink")
    layer_digest = fixture.manifest["layers"][0]["digest"]
    layer_path = _blob_path(fixture.layout, layer_digest)
    target = tmp_path / "external-layer"
    target.write_bytes(layer_path.read_bytes())
    layer_path.unlink()
    try:
        layer_path.symlink_to(target)
    except OSError:
        # Windows developer-mode symlinks are unavailable on this author host; Linux CI replays it.
        return
    with pytest.raises(VerificationError, match="missing"):
        _bind(fixture)


def test_bind_rejects_corrupt_compressed_layer_and_diff_id_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    corrupt = b"not a gzip stream"
    digest = _write_blob(fixture.layout, corrupt)
    fixture.manifest["layers"][0]["digest"] = digest
    fixture.manifest["layers"][0]["size"] = len(corrupt)
    _replace_manifest(fixture, fixture.manifest)
    with pytest.raises(VerificationError, match="gzip layer"):
        _bind(fixture)

    fixture = _fixture(tmp_path / "diff-id")
    replacement = gzip.compress(_tar_layer(b"different rootfs"), mtime=0)
    digest = _write_blob(fixture.layout, replacement)
    fixture.manifest["layers"][0]["digest"] = digest
    fixture.manifest["layers"][0]["size"] = len(replacement)
    _replace_manifest(fixture, fixture.manifest)
    with pytest.raises(VerificationError, match="rootfs"):
        _bind(fixture)


def test_bind_rejects_changed_layer_order(tmp_path: Path) -> None:
    fixture = _two_layer_fixture(tmp_path)
    fixture.manifest["layers"].reverse()
    _replace_manifest(fixture, fixture.manifest)

    with pytest.raises(VerificationError, match="rootfs"):
        _bind(fixture)


def test_bind_rejects_remote_manifest_mismatch(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    fixture.remote.write_bytes(b"{}")

    with pytest.raises(VerificationError, match="remote manifest"):
        _bind(fixture, remote=fixture.remote)
