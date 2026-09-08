import json

from scripts import sanitize_registry_tags


def test_tag_observation_never_emits_tag_names(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["tag", "--reviewed-revision", "a" * 40])
    document = '{"Tags":["private-tag","' + "a" * 40 + '"]}'
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(document))
    assert sanitize_registry_tags.main() == 0
    output = capsys.readouterr().out
    assert "private-tag" not in output
    assert json.loads(output)["reviewed_revision_tag_present"] is True


def test_malformed_tag_document_is_indeterminate(monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.argv", ["tag", "--reviewed-revision", "a" * 40])
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO('{"Tags":"nope"}'))
    assert sanitize_registry_tags.main() == 2
    assert json.loads(capsys.readouterr().out) == {"state": "indeterminate"}
