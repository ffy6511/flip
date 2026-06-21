from flip import engine, engine_loop, store


def _patch_tty(monkeypatch, keys):
    key_iter = iter(keys)
    monkeypatch.setattr(engine_loop, "save_tty", lambda: None)
    monkeypatch.setattr(engine_loop, "restore_tty", lambda _settings: None)
    monkeypatch.setattr(engine_loop, "enter_cbreak", lambda: None)
    monkeypatch.setattr(engine_loop, "read_key", lambda: next(key_iter))


def test_selector_set_from_text_uses_engine_chapter_selector():
    assert engine_loop._selector_set_from_text(
        "5,3-4", ["1", "2", "3", "4", "5"], 5
    ) == {"3", "4", "5"}


def test_prompt_answer_refreshes_marked_state_after_m(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    rendered = []
    _patch_tty(monkeypatch, ["m", "1", "\r"])

    def capture_render(*_args, **kwargs):
        rendered.append(kwargs["marked"])

    monkeypatch.setattr(engine_loop, "render_question", capture_render)

    result = engine_loop.prompt_answer(deck, config, 1, 1, "1", q)

    assert result[0] == "A"
    assert rendered[:2] == [False, True]
    assert store.load_tiku(deck)["1"][0]["marked"] is True
    assert any(
        item.get("key") == engine.question_key("1", q)
        for item in store.load_marked(deck)
    )


def test_prompt_result_refreshes_marked_state_after_m(deck, config, monkeypatch):
    q = store.load_tiku(deck)["1"][0]
    rendered = []
    _patch_tty(monkeypatch, ["m", "\r"])

    def capture_render(*_args, **kwargs):
        rendered.append(kwargs["marked"])

    monkeypatch.setattr(engine_loop, "render_result", capture_render)

    result = engine_loop.prompt_result(deck, config, 1, 1, "1", q, "B", True)

    assert result[0] == "next"
    assert rendered[:2] == [False, True]
    assert store.load_tiku(deck)["1"][0]["marked"] is True
    assert any(
        item.get("key") == engine.question_key("1", q)
        for item in store.load_marked(deck)
    )
