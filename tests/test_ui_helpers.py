from types import SimpleNamespace


def test_clipboard_action_paste_by_v_keycode():
    import ui_helpers

    # Physical V key (86) with Control — works even when layout is Hebrew/Russian
    event = SimpleNamespace(state=0x4, keycode=86, keysym="hebrew_finalnun")
    assert ui_helpers.clipboard_action_from_event(event) == "paste"


def test_clipboard_action_copy_by_c_keycode():
    import ui_helpers

    event = SimpleNamespace(state=0x4, keycode=67, keysym="hebrew_bet")
    assert ui_helpers.clipboard_action_from_event(event) == "copy"


def test_clipboard_action_cut_by_x_keycode():
    import ui_helpers

    event = SimpleNamespace(state=0x4, keycode=88, keysym="hebrew_samekh")
    assert ui_helpers.clipboard_action_from_event(event) == "cut"


def test_clipboard_action_select_all_by_a_keycode():
    import ui_helpers

    event = SimpleNamespace(state=0x4, keycode=65, keysym="hebrew_shin")
    assert ui_helpers.clipboard_action_from_event(event) == "select_all"


def test_clipboard_action_paste_via_shift_insert():
    import ui_helpers

    event = SimpleNamespace(state=0x1, keycode=45, keysym="Insert")
    assert ui_helpers.clipboard_action_from_event(event) == "paste"


def test_clipboard_action_none_without_modifier():
    import ui_helpers

    event = SimpleNamespace(state=0, keycode=86, keysym="v")
    assert ui_helpers.clipboard_action_from_event(event) is None


def test_clipboard_action_english_control_v_keysym_fallback():
    import ui_helpers

    # Some platforms report Latin keysym even under Control
    event = SimpleNamespace(state=0x4, keycode=0, keysym="v")
    assert ui_helpers.clipboard_action_from_event(event) == "paste"


def test_wrap_tooltip_keeps_short_text():
    import ui_helpers

    assert ui_helpers.wrap_tooltip("Paste one barcode per line.") == "Paste one barcode per line."


def test_wrap_tooltip_breaks_long_text():
    import ui_helpers

    text = " ".join(["word"] * 20)
    wrapped = ui_helpers.wrap_tooltip(text, width=40)
    assert "\n" in wrapped
    assert wrapped.replace("\n", " ") == text
