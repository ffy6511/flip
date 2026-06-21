import pytest

from flip.deck import load_deck, list_decks, DeckError


class TestLoadDeck:
    def test_loads_example_deck(self, deck):
        assert deck.slug == "example"
        assert deck.name == "示例题库"
        assert deck.source_lang == "en"
        assert deck.answer_alphabet == "ABCDE"

    def test_paths_resolve(self, deck):
        assert deck.tiku_path.name == "tiku.json"
        assert deck.manifest_path.name == "manifest.toml"
        assert deck.marked_path.name == "marked.json"
        assert deck.wrong_dir.name == "wrong"

    def test_explain_config(self, deck):
        assert deck.explain.role == "通用助教"
        assert deck.explain.max_chars == 200


class TestLoadDeckValidation:
    def test_missing_manifest_raises(self, tmp_path):
        (tmp_path / "deck").mkdir()  # dir exists, but no manifest
        with pytest.raises(DeckError, match="manifest not found"):
            load_deck(tmp_path / "deck")

    def test_missing_name_raises(self, tmp_path):
        (tmp_path / "deck").mkdir()
        (tmp_path / "deck" / "manifest.toml").write_text(
            '[deck]\nslug = "deck"\nsource_lang = "en"\n\n'
            '[explain]\nrole = "r"\n',
            encoding="utf-8",
        )
        with pytest.raises(DeckError, match="name is required"):
            load_deck(tmp_path / "deck")

    def test_missing_role_raises(self, tmp_path):
        (tmp_path / "deck").mkdir()
        (tmp_path / "deck" / "manifest.toml").write_text(
            '[deck]\nname = "Deck"\nslug = "deck"\nsource_lang = "en"\n\n'
            '[explain]\n',
            encoding="utf-8",
        )
        with pytest.raises(DeckError, match="role is required"):
            load_deck(tmp_path / "deck")

    def test_slug_mismatch_raises(self, tmp_path):
        (tmp_path / "deck").mkdir()
        (tmp_path / "deck" / "manifest.toml").write_text(
            '[deck]\nname = "X"\nslug = "other"\nsource_lang = "en"\n\n'
            '[explain]\nrole = "r"\n',
            encoding="utf-8",
        )
        with pytest.raises(DeckError, match="must equal directory name"):
            load_deck(tmp_path / "deck")

    def test_bad_alphabet_raises(self, tmp_path):
        (tmp_path / "deck").mkdir()
        (tmp_path / "deck" / "manifest.toml").write_text(
            '[deck]\nname = "X"\nslug = "deck"\nsource_lang = "en"\n'
            'answer_alphabet = "AB1"\n\n'
            '[explain]\nrole = "r"\n',
            encoding="utf-8",
        )
        with pytest.raises(DeckError, match="letters only"):
            load_deck(tmp_path / "deck")


class TestListDecks:
    def test_lists_example(self, config):
        slugs = list_decks(config.decks_dir)
        assert "example" in slugs

    def test_empty_when_no_dir(self, tmp_path):
        assert list_decks(tmp_path / "nope") == []


class TestExplainModelResolution:
    def test_env_overrides_default(self, deck, monkeypatch):
        monkeypatch.setenv("FLIP_EXPLAIN_MODEL", "custom-model")
        assert deck.explain.resolve_model() == "custom-model"

    def test_falls_back_to_default(self, deck, monkeypatch):
        monkeypatch.delenv("FLIP_EXPLAIN_MODEL", raising=False)
        assert deck.explain.resolve_model() == "gpt-5.3-codex-spark"
