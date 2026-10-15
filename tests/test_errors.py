"""Bad input ends in ``error:`` with a non-zero exit code, never in a traceback."""


def test_invalid_config(ncg, project):
    for text in ("[1]", '{"layout": 3}', "{"):
        (project / "nodecanvas-cli.json").write_text(text, encoding="utf-8")
        code, _, err = ncg("list")
        assert code == 2 and err.startswith("error:")


def test_missing_ops_file(ncg, project):
    code, _, err = ncg("apply", "Bot_BT", "nope.json")
    assert code == 1 and "nope.json" in err


def test_graph_name_matching_a_folder(ncg, project, monkeypatch):
    (project / "Assets/Bot_BT").mkdir()
    monkeypatch.chdir(project / "Assets")
    code, out, _ = ncg("show", "Bot_BT")
    assert code == 0 and "Bot_BT" in out


def test_retarget_needs_a_member(ncg):
    code, _, err = ncg("refactor", "retarget", "Bot", "--to", "Vector3", "--dry-run")
    assert code == 1 and "Type.Method" in err


def test_diff_rejects_options_as_revision(ncg, project):
    code, _, err = ncg("diff", "Bot_BT", f"--rev=--output={project / 'pwned'}")
    assert code == 1 and "not a revision" in err
    assert not (project / "pwned").exists()


def test_graph_with_infinity_can_be_edited(ncg, project):
    path = project / "Assets/Graphs/Sub_BT.asset"
    path.write_text(path.read_text().replace('waitTime":{"_value":0.5}', 'waitTime":{"_value":Infinity}'))
    code, out, err = ncg("show", "Sub_BT")
    assert code == 0, err
    (project / "ops.json").write_text('[{"op": "var-add", "name": "Extra", "type": "int"}]')
    code, _, err = ncg("apply", "Sub_BT", "ops.json")
    assert code == 0, err
    assert '"_value":Infinity' in path.read_text().replace("\n    ", " ")
