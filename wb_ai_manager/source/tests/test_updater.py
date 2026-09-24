from pathlib import Path
import zipfile
import pytest

from wb_control_center.updater import UpdateManifest, UpdateManager, current_version


def test_manifest_channel_parsing():
    m=UpdateManifest.from_dict({"channels":{"stable":{"version":"1.2.3","archive_url":"https://example.test/x.zip","sha256":"a"*64}}})
    assert m.version == "1.2.3"
    assert m.sha256 == "a"*64


def test_current_version(tmp_path: Path):
    (tmp_path/"VERSION").write_text("1.0.0\n",encoding="utf-8")
    assert current_version(tmp_path) == "1.0.0"


def test_safe_extract_rejects_path_traversal(tmp_path: Path):
    zpath=tmp_path/"x.zip"
    with zipfile.ZipFile(zpath,"w") as z:
        z.writestr("../evil.txt","x")
    dest=tmp_path/"out"; dest.mkdir()
    with zipfile.ZipFile(zpath,"r") as z:
        with pytest.raises(ValueError):
            UpdateManager._safe_extract(z,dest)


def test_patch_cannot_modify_persistent_user_config(tmp_path: Path):
    root=tmp_path/"app"; root.mkdir()
    (root/"VERSION").write_text("1.0.0\n",encoding="utf-8")
    (root/"config").mkdir()
    (root/"config/catalog.csv").write_text("mine",encoding="utf-8")
    mgr=UpdateManager(root,"https://example.test/manifest.json")
    with pytest.raises(ValueError, match="persistent user path"):
        mgr._apply_file_patch([{"path":"config/catalog.csv","delete":True}],tmp_path/"stage")
    assert (root/"config/catalog.csv").read_text()=="mine"


def test_restore_removes_failed_release_config_files_but_preserves_user_config(tmp_path: Path):
    root=tmp_path/"app"; root.mkdir()
    (root/"VERSION").write_text("1.0.0\n",encoding="utf-8")
    (root/"src").mkdir(); (root/"src/a.py").write_text("old",encoding="utf-8")
    (root/"config").mkdir()
    (root/"config/policies.yaml").write_text("old-policy",encoding="utf-8")
    (root/"config/catalog.csv").write_text("user-catalog",encoding="utf-8")
    mgr=UpdateManager(root,"https://example.test/manifest.json")
    backup=mgr._backup()
    # Simulate a partially applied broken update.
    (root/"src/a.py").write_text("broken",encoding="utf-8")
    (root/"config/policies.yaml").write_text("broken-policy",encoding="utf-8")
    (root/"config/new_release_only.yaml").write_text("bad",encoding="utf-8")
    (root/"config/catalog.csv").write_text("user-changed-after-backup",encoding="utf-8")
    mgr._restore(backup)
    assert (root/"src/a.py").read_text()=="old"
    assert (root/"config/policies.yaml").read_text()=="old-policy"
    assert not (root/"config/new_release_only.yaml").exists()
    # Tenant data modified after backup is deliberately untouched by rollback.
    assert (root/"config/catalog.csv").read_text()=="user-changed-after-backup"
