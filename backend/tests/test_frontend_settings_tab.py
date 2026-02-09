from __future__ import annotations

from pathlib import Path


def test_settings_is_tab_not_modal():
    repo_root = Path(__file__).resolve().parents[2]
    html = (repo_root / "frontend" / "index.html").read_text(encoding="utf-8")

    assert 'data-main-tab="settings"' in html
    assert 'data-tab="settings"' in html
    assert 'id="modal"' not in html
    assert 'id="modalBackdrop"' not in html
    assert 'id="btnSettings"' not in html
