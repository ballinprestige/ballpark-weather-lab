"""Bind a Docker save archive, a converted OCI layout, and an optional remote manifest."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import tarfile
from pathlib import Path, PurePosixPath
from typing import BinaryIO

DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"


class VerificationError(RuntimeError):
    pass


def sha256_bytes(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def rootfs_diff_id(stream: BinaryIO) -> str:
    """Return the digest of an archive layer's uncompressed tar stream."""
    buffered = stream if hasattr(stream, "peek") else __import__("io").BufferedReader(stream)
    if buffered.peek(2).startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=buffered, mode="rb") as uncompressed:
            return sha256_stream(uncompressed)
    return sha256_stream(buffered)


def json_value(value: bytes, label: str) -> object:
    try:

        def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, item in pairs:
                if key in result:
                    raise VerificationError("JSON duplicate key")
                result[key] = item
            return result

        def reject_nonfinite(_token: str) -> object:
            raise VerificationError(f"{label} contains a non-finite JSON number")

        def finite_float(token: str) -> float:
            parsed_float = float(token)
            if not math.isfinite(parsed_float):
                raise VerificationError(f"{label} contains a non-finite JSON number")
            return parsed_float

        parsed = json.loads(
            value,
            object_pairs_hook=unique,
            parse_constant=reject_nonfinite,
            parse_float=finite_float,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationError(f"{label} is not valid JSON") from error
    return parsed


def json_semantically_equal(left: object, right: object) -> bool:
    """Compare JSON values without accepting Python's cross-type equality."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            json_semantically_equal(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            json_semantically_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    return left == right


def json_object(value: bytes, label: str) -> dict[str, object]:
    parsed = json_value(value, label)
    if not isinstance(parsed, dict):
        raise VerificationError(f"{label} must be a JSON object")
    return parsed


def nonnegative_int(value: object) -> bool:
    return type(value) is int and value >= 0


def archive_member(archive: tarfile.TarFile, name: str, label: str) -> tarfile.TarInfo:
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise VerificationError(f"Docker archive {label} path is invalid")
    matches = [member for member in archive.getmembers() if member.name == name]
    if len(matches) != 1 or not matches[0].isfile():
        raise VerificationError(f"Docker archive {label} path is invalid")
    return matches[0]


def digest_file(layout: Path, digest: str) -> Path:
    match = DIGEST.fullmatch(digest)
    if not match:
        raise VerificationError("OCI descriptor digest must be a lower-case sha256 digest")
    path = layout / "blobs" / "sha256" / match.group(1)
    if path.is_symlink() or not path.is_file():
        raise VerificationError("OCI descriptor blob is missing")
    return path


def docker_archive(path: Path, expected_reference: str) -> tuple[bytes, list[str]]:
    try:
        with tarfile.open(path, "r") as archive:
            manifest_member = archive_member(archive, "manifest.json", "manifest")
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise VerificationError("Docker archive manifest path is invalid")
            manifest = json_value(manifest_stream.read(), "Docker archive manifest")
            if (
                not isinstance(manifest, list)
                or len(manifest) != 1
                or not isinstance(manifest[0], dict)
            ):
                raise VerificationError("Docker archive must have exactly one manifest entry")
            entry = manifest[0]
            if entry.get("RepoTags") != [expected_reference]:
                raise VerificationError("Docker archive RepoTags do not bind expected image")
            config_name = entry.get("Config")
            layer_names = entry.get("Layers")
            if not isinstance(config_name, str) or not isinstance(layer_names, list):
                raise VerificationError("Docker archive manifest is incomplete")
            if not all(isinstance(layer, str) for layer in layer_names):
                raise VerificationError("Docker archive manifest layer names are invalid")
            config_member = archive_member(archive, config_name, "config")
            config_stream = archive.extractfile(config_member)
            if config_stream is None:
                raise VerificationError("Docker archive config path is invalid")
            config_bytes = config_stream.read()
            config = json_object(config_bytes, "Docker archive config")
            rootfs = config.get("rootfs")
            diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
            if not isinstance(diff_ids, list) or not all(
                isinstance(diff_id, str) and DIGEST.fullmatch(diff_id) for diff_id in diff_ids
            ):
                raise VerificationError("Docker archive config rootfs diff_ids are invalid")
            if len(layer_names) != len(diff_ids):
                raise VerificationError("Docker archive layer count does not match config rootfs")
            observed = []
            for layer_name, expected in zip(layer_names, diff_ids, strict=True):
                member = archive_member(archive, layer_name, "layer")
                stream = archive.extractfile(member)
                if stream is None or rootfs_diff_id(stream) != expected:
                    raise VerificationError(
                        "Docker archive layer does not match config rootfs diff_id"
                    )
                observed.append(expected)
    except (OSError, tarfile.TarError, KeyError, TypeError) as error:
        raise VerificationError("Docker archive could not be read safely") from error
    return config_bytes, observed


def oci_manifest(layout: Path, ref_name: str) -> tuple[bytes, dict[str, object]]:
    if json_object((layout / "oci-layout").read_bytes(), "OCI layout") != {
        "imageLayoutVersion": "1.0.0"
    }:
        raise VerificationError("OCI layout metadata is invalid")
    index = json_object((layout / "index.json").read_bytes(), "OCI index")
    manifests = index.get("manifests")
    if index.get("schemaVersion") != 2 or not isinstance(manifests, list) or len(manifests) != 1:
        raise VerificationError("OCI index lacks manifests")
    matches = [
        item
        for item in manifests
        if isinstance(item, dict)
        and item.get("mediaType") == OCI_MANIFEST
        and isinstance(item.get("annotations"), dict)
        and item["annotations"].get("org.opencontainers.image.ref.name") == ref_name
    ]
    if (
        len(matches) != 1
        or not isinstance(matches[0].get("digest"), str)
        or not nonnegative_int(matches[0].get("size"))
    ):
        raise VerificationError("OCI index must contain one named OCI manifest descriptor")
    descriptor = matches[0]
    blob = digest_file(layout, descriptor["digest"])
    manifest_bytes = blob.read_bytes()
    if (
        sha256_bytes(manifest_bytes) != descriptor["digest"]
        or len(manifest_bytes) != descriptor["size"]
    ):
        raise VerificationError("OCI manifest blob does not match index descriptor digest")
    manifest = json_object(manifest_bytes, "OCI manifest")
    if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != OCI_MANIFEST:
        raise VerificationError("OCI manifest type is invalid")
    return manifest_bytes, manifest


def bind(
    layout: Path, archive: Path, ref_name: str, expected_reference: str, remote: Path | None
) -> dict[str, object]:
    config_bytes, diff_ids = docker_archive(archive, expected_reference)
    manifest_bytes, manifest = oci_manifest(layout, ref_name)
    config = manifest.get("config")
    layers = manifest.get("layers")
    if (
        not isinstance(config, dict)
        or not isinstance(config.get("digest"), str)
        or config.get("mediaType") != "application/vnd.oci.image.config.v1+json"
        or not nonnegative_int(config.get("size"))
    ):
        raise VerificationError("OCI manifest config descriptor is invalid")
    local_config_digest = sha256_bytes(config_bytes)
    oci_config_bytes = digest_file(layout, config["digest"]).read_bytes()
    if (
        sha256_bytes(oci_config_bytes) != config["digest"]
        or len(oci_config_bytes) != config["size"]
    ):
        raise VerificationError("OCI config blob does not match descriptor digest")
    oci_config = json_object(oci_config_bytes, "OCI config")
    docker_config = json_object(config_bytes, "Docker archive config")
    if (
        not json_semantically_equal(oci_config, docker_config)
        or oci_config.get("os") != "linux"
        or oci_config.get("architecture") != "amd64"
    ):
        raise VerificationError("OCI config semantics differ from Docker archive config")
    if not isinstance(layers, list) or len(layers) != len(diff_ids):
        raise VerificationError("OCI manifest layer count differs from Docker archive rootfs")
    layer_digests: list[str] = []
    layer_descriptors: list[dict[str, object]] = []
    for descriptor, expected_diff_id in zip(layers, diff_ids, strict=True):
        if (
            not isinstance(descriptor, dict)
            or not isinstance(descriptor.get("digest"), str)
            or not nonnegative_int(descriptor.get("size"))
        ):
            raise VerificationError("OCI manifest layer descriptor is invalid")
        media_type = descriptor.get("mediaType")
        blob = digest_file(layout, descriptor["digest"])
        with blob.open("rb") as compressed:
            compressed_digest = sha256_stream(compressed)
        if compressed_digest != descriptor["digest"] or blob.stat().st_size != descriptor["size"]:
            raise VerificationError("OCI layer blob does not match descriptor digest")
        if media_type == "application/vnd.oci.image.layer.v1.tar+gzip":
            try:
                with gzip.open(blob, "rb") as stream:
                    actual_diff_id = sha256_stream(stream)
            except (EOFError, OSError) as error:
                raise VerificationError("OCI gzip layer could not be read safely") from error
        elif media_type == "application/vnd.oci.image.layer.v1.tar":
            with blob.open("rb") as stream:
                actual_diff_id = sha256_stream(stream)
        else:
            raise VerificationError("OCI layer media type is not an accepted tar representation")
        if actual_diff_id != expected_diff_id:
            raise VerificationError("OCI layer rootfs content differs from Docker archive config")
        layer_digests.append(descriptor["digest"])
        layer_descriptors.append(dict(descriptor))
    manifest_digest = sha256_bytes(manifest_bytes)
    if remote is not None:
        remote_bytes = remote.read_bytes()
        if sha256_bytes(remote_bytes) != manifest_digest or remote_bytes != manifest_bytes:
            raise VerificationError("remote manifest bytes differ from the verified OCI manifest")
    return {
        "docker_archive_config_digest": local_config_digest,
        "docker_archive_rootfs_diff_ids": diff_ids,
        "oci_manifest_digest": manifest_digest,
        "oci_config_digest": config["digest"],
        "oci_layer_digests": layer_digests,
        "oci_layer_descriptors": layer_descriptors,
        "remote_manifest_verified": remote is not None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-archive", type=Path, required=True)
    parser.add_argument("--oci-layout", type=Path, required=True)
    parser.add_argument("--oci-ref-name", required=True)
    parser.add_argument("--expected-reference", required=True)
    parser.add_argument("--remote-manifest", type=Path)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = bind(
            args.oci_layout,
            args.docker_archive,
            args.oci_ref_name,
            args.expected_reference,
            args.remote_manifest,
        )
    except (OSError, VerificationError) as error:
        parser.error(str(error))
    args.receipt.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
