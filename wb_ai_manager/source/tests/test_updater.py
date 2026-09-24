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


def test_safe_extract_restores_executable_mode(tmp_path: Path):
    zpath=tmp_path/"exec.zip"
    info=zipfile.ZipInfo("START.command")
    info.create_system=3
    info.external_attr=(0o100755 << 16)
    with zipfile.ZipFile(zpath,"w") as z:
        z.writestr(info,"#!/bin/bash\necho ok\n")
    dest=tmp_path/"out"; dest.mkdir()
    with zipfile.ZipFile(zpath,"r") as z:
        UpdateManager._safe_extract(z,dest)
    assert (dest/"START.command").stat().st_mode & 0o111


def test_interrupted_update_is_restored_before_next_start(tmp_path: Path):
    root=tmp_path/"app"; root.mkdir()
    (root/"VERSION").write_text("1.1.3\n",encoding="utf-8")
    (root/"src").mkdir(); (root/"src/a.py").write_text("old",encoding="utf-8")
    (root/"scripts").mkdir(); (root/"scripts/update_smoke.py").write_text("print('ok')\n",encoding="utf-8")
    mgr=UpdateManager(root,"https://example.test/manifest.json")
    backup=mgr._backup()
    mgr._write_status(applying=True,target_version="1.1.4",backup=str(backup))
    (root/"src/a.py").write_text("partial-new",encoding="utf-8")
    # Keep the unit test independent of package layout/dependencies; restore semantics
    # are the contract under test, while smoke is tested separately by release gates.
    mgr._smoke=lambda: (True,"ok")
    out=mgr.recover_interrupted_update()
    assert out["recovered_interrupted_update"] is True
    assert out["applying"] is False
    assert (root/"src/a.py").read_text()=="old"


def test_status_json_write_is_never_left_as_temp_artifact(tmp_path: Path):
    root=tmp_path/"app"; root.mkdir(); (root/"VERSION").write_text("1.1.4\n",encoding="utf-8")
    mgr=UpdateManager(root,"https://example.test/manifest.json")
    mgr._write_status(applying=True,target_version="1.1.5")
    assert mgr.status()["target_version"] == "1.1.5"
    assert not list((root/".updates").glob(".status.json.*.tmp"))


def test_rollback_is_not_reported_ok_until_smoke_passes(tmp_path: Path):
    root=tmp_path/'app'; root.mkdir(); (root/'VERSION').write_text('1.1.4\n',encoding='utf-8')
    (root/'src').mkdir(); (root/'src/a.py').write_text('old',encoding='utf-8')
    mgr=UpdateManager(root,'https://example.test/manifest.json')
    backup=mgr._backup()
    mgr._refresh_editable_install=lambda context: None
    mgr._smoke=lambda: (False,'broken restored package')
    with pytest.raises(RuntimeError, match='rollback smoke'):
        mgr._restore_and_verify(backup,'after failed update')
