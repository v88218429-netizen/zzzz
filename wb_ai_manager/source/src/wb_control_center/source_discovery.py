from __future__ import annotations
import json, os, shutil, subprocess
from pathlib import Path
from typing import Any
import yaml

from .config import project_root

class SourceDiscovery:
    """Find known trusted sources without asking the user to paste spreadsheet URLs.

    It uses: (1) already configured read-only bridge, (2) Google Drive Desktop local mount,
    (3) optional rclone remote named `wbai`. Private Google data always requires a one-time
    Google consent somewhere; the agent automates everything after that consent.
    """
    def __init__(self):
        self.root=project_root(); self.registry=yaml.safe_load((self.root/'config/store_registry.yaml').read_text()) or {}

    def known_sources(self)->list[dict[str,Any]]:
        return [{'id':k,'title':v.get('title'),'spreadsheet_id':v.get('spreadsheet_id')} for k,v in (self.registry.get('sources') or {}).items()]

    def local_drive_roots(self)->list[Path]:
        home=Path.home(); roots=[]
        cloud=home/'Library/CloudStorage'
        if cloud.exists(): roots += [x for x in cloud.glob('GoogleDrive-*') if x.is_dir()]
        for x in [home/'Google Drive', Path('/Volumes/GoogleDrive')]:
            if x.exists(): roots.append(x)
        return roots

    def discover(self)->dict[str,Any]:
        found=[]; roots=self.local_drive_roots()
        for src in self.known_sources():
            title=src['title']; hit=None
            for root in roots:
                # Avoid full-drive recursion when possible, but allow a bounded title search.
                candidates=list(root.glob(f'**/{title}.xlsx'))[:2] + list(root.glob(f'**/{title}.gsheet'))[:2]
                if candidates: hit=str(candidates[0]); break
            found.append(src|{'local_path':hit,'found_local':bool(hit)})
        return {'google_drive_roots':[str(x) for x in roots],'sources':found,'rclone_available':bool(shutil.which('rclone'))}
