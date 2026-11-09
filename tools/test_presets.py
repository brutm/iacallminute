"""Verifica preset provider di testo + default Groq senza chiavi a codice."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config  # noqa: E402


def main() -> int:
    ok = 0

    # 1. nessuna chiave hardcoded nei default
    assert config.DEFAULT_SETTINGS["stt"]["api_key"] == ""
    assert config.DEFAULT_SETTINGS["text"]["api_key"] == ""
    ok += 1
    print("1 default senza chiavi hardcoded ok")

    # 2. default del testo = Groq
    t = config.DEFAULT_SETTINGS["text"]
    assert t["type"] == "openai", t
    assert "groq.com" in t["base_url"], t
    assert t["model"], t
    ok += 1
    print("2 text di default = Groq ok")

    # 3. preset coerenti
    for key in ("groq", "openrouter", "mistral", "gemini"):
        p = config.TEXT_PRESETS[key]
        assert p["type"] in ("gemini", "openai"), (key, p)
        assert p["model"], key
        if p["type"] == "openai":
            assert p["base_url"].startswith("https://"), (key, p)
    ok += 1
    print("3 TEXT_PRESETS ok")

    # 4. senza chiave Groq le credenziali testo risultano mancanti
    # (usa esplicitamente i default: il settings.json reale ha la chiave)
    s = {
        "stt": {**config.DEFAULT_SETTINGS["stt"], "api_key": "AIza_dummy"},
        "text": {**config.DEFAULT_SETTINGS["text"], "api_key": ""},
    }
    msgs = config.missing_credentials(s)
    assert any("testo" in m and "chiave" in m for m in msgs), msgs
    assert not any("trascrizione" in m for m in msgs), msgs
    ok += 1
    print("4 missing_credentials segnala solo la chiave testo ok")

    # 5. con chiave Groq le credenziali sono complete
    s = {
        "stt": {**config.DEFAULT_SETTINGS["stt"], "api_key": "AIza_dummy"},
        "text": {**config.DEFAULT_SETTINGS["text"], "api_key": "gsk_prova"},
    }
    assert config.missing_credentials(s) == [], config.missing_credentials(s)
    ok += 1
    print("5 credenziali complete con chiave ok")

    # 6. UI: i preset riempiono tipo/URL/modello
    import tkinter as tk
    from app.ui import settings as ui_settings
    root = tk.Tk()
    root.withdraw()
    dlg = ui_settings.SettingsDialog(root)
    dlg.update_idletasks()
    dlg._apply_text_preset("groq")
    assert dlg.text_type.get() == "openai"
    assert "groq.com" in dlg.text_base.get()
    assert dlg.text_model.get() == "openai/gpt-oss-20b"
    dlg._apply_text_preset("openrouter")
    assert "openrouter.ai" in dlg.text_base.get()
    dlg._apply_text_preset("gemini")
    assert dlg.text_type.get() == "gemini"
    assert dlg.text_base.get() == ""
    dlg.destroy()
    root.destroy()
    ok += 1
    print("6 preset da UI ok")

    # 7. cambiando radio i campi si riempiono da soli (niente digitazione)
    import tkinter as tk
    from app.ui import settings as ui_settings
    root = tk.Tk()
    root.withdraw()
    dlg = ui_settings.SettingsDialog(root)
    dlg.update_idletasks()
    dlg._apply_text_preset("groq")
    assert "groq.com" in dlg.text_base.get() and "/" in dlg.text_model.get()
    dlg.text_type.set("gemini")
    dlg._on_text_type_change()
    assert dlg.text_base.get() == "", dlg.text_base.get()
    assert dlg.text_model.get() == config.DEFAULT_MODEL, dlg.text_model.get()
    dlg.text_type.set("openai")
    dlg._on_text_type_change()
    assert "groq.com" in dlg.text_base.get(), dlg.text_base.get()
    assert dlg.text_model.get() == "openai/gpt-oss-20b", dlg.text_model.get()
    ok += 1
    print("7 radio -> modello/URL automatici ok")

    # 8. _collect normalizza: Gemini non deve salvare un URL di router
    dlg._apply_text_preset("groq")
    cand = dlg._collect()
    assert cand["text"]["type"] == "openai"
    assert "groq.com" in cand["text"]["base_url"]
    dlg.text_type.set("gemini")
    dlg.text_base.set("https://api.groq.com/openai/v1")   # URL dimenticato
    dlg.text_model.set("openai/gpt-oss-20b")
    cand = dlg._collect()
    assert cand["text"]["base_url"] == "", cand["text"]
    assert cand["text"]["model"] == config.DEFAULT_MODEL, cand["text"]
    dlg.destroy()
    root.destroy()
    ok += 1
    print("8 _collect normalizza Gemini/router ok")

    # 9. la generazione ritenta su connessione interrotta (10054)
    from app.ai import service
    from app.ai.errors import ProviderError
    calls = {"n": 0}
    orig = service.gemini.complete_text

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ProviderError(
                "connessione interrotta dal provider (errore 10054)",
                0, True)
        return "RIASSUNTO OK"

    service.gemini.complete_text = flaky
    statuses: list[str] = []
    old_backoff = config.BACKOFF_BASE_S
    config.BACKOFF_BASE_S = 0.01
    try:
        out = service.complete(
            {"stt": {"type": "gemini", "model": "x", "api_key": "k"},
             "text": {"type": "gemini", "model": "x", "api_key": "k"}},
            "system", "user", on_status=statuses.append)
    finally:
        service.gemini.complete_text = orig
        config.BACKOFF_BASE_S = old_backoff
    assert out == "RIASSUNTO OK", out
    assert calls["n"] == 3, calls
    assert any("riprovo" in s for s in statuses), statuses
    ok += 1
    print("9 retry su connessione interrotta ok")

    # 10. preset GitHub Copilot: UI, tipo e modello economico
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    dlg = ui_settings.SettingsDialog(root)
    dlg.update_idletasks()
    dlg._apply_text_preset("copilot")
    assert dlg.text_type.get() == "copilot"
    assert dlg.text_model.get() == "gpt-4o-mini", dlg.text_model.get()
    dlg._collect()
    cand = dlg._collect()
    assert cand["text"]["type"] == "copilot"
    assert cand["text"]["base_url"] == "https://api.githubcopilot.com"
    assert cand["text"]["model"] == "gpt-4o-mini"
    # manca il collegamento -> missing_credentials lo dice in modo chiaro
    # (simula «non collegato»: su questo PC copilot.json esiste già)
    from app.ai import copilot as _cp
    from app import config as _c
    s = {"stt": {**_c.DEFAULT_SETTINGS["stt"], "api_key": "AIza_dummy"},
         "text": {"type": "copilot", "api_key": "", "model": "gpt-4o-mini"}}
    orig_logged = _cp.is_logged_in
    _cp.is_logged_in = lambda: False
    try:
        msgs = config.missing_credentials(s)
        assert _c.text_key(s["text"]) is None
        assert any("copilot" in m.lower() and "accedi" in m.lower()
                   for m in msgs), msgs
    finally:
        _cp.is_logged_in = orig_logged
    _cp.is_logged_in = lambda: True
    try:
        assert _c.text_key(s["text"]) == "copilot"
        assert config.missing_credentials(s) == []
    finally:
        _cp.is_logged_in = orig_logged
    dlg.destroy()
    root.destroy()
    ok += 1
    print("10 preset GitHub Copilot ok")

    # 11. service.complete instrada il tipo copilot verso copilot.chat_text
    from app.ai import copilot, service
    seen = {}

    def fake_chat(cfg, system, user_text, timeout=None):
        seen["model"] = cfg.get("model")
        seen["n"] = len(system) + len(user_text)
        return "RIASSUNTO COPILOT"

    orig_chat = copilot.chat_text
    copilot.chat_text = fake_chat
    try:
        out = service.complete(
            {"stt": {"type": "gemini", "model": "x", "api_key": "k"},
             "text": {"type": "copilot", "model": "gpt-4o-mini"}},
            "system", "user")
    finally:
        copilot.chat_text = orig_chat
    assert out == "RIASSUNTO COPILOT", out
    assert seen["model"] == "gpt-4o-mini", seen
    ok += 1
    print("11 routing service -> copilot ok")

    print(f"PASS {ok}/11 test_presets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
