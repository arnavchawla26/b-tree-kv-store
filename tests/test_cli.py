from btreekv.cli import main


def test_put_and_get(tmp_db_path, capsys):
    assert main(["put", tmp_db_path, "name", "arnav"]) == 0
    assert main(["get", tmp_db_path, "name"]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "arnav"


def test_get_missing_key_returns_nonzero(tmp_db_path, capsys):
    assert main(["get", tmp_db_path, "nope"]) == 1
    err = capsys.readouterr().err
    assert "not found" in err


def test_delete(tmp_db_path, capsys):
    main(["put", tmp_db_path, "a", "1"])
    assert main(["delete", tmp_db_path, "a"]) == 0
    assert main(["get", tmp_db_path, "a"]) == 1
    assert main(["delete", tmp_db_path, "a"]) == 1


def test_scan(tmp_db_path, capsys):
    for i in range(5):
        main(["put", tmp_db_path, f"k{i}", str(i)])
    capsys.readouterr()  # discard prior output
    assert main(["scan", tmp_db_path, "--quiet"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == [f"k{i}\t{i}" for i in range(5)]


def test_scan_with_bounds(tmp_db_path, capsys):
    for i in range(10):
        main(["put", tmp_db_path, f"k{i}", str(i)])
    capsys.readouterr()
    main(["scan", tmp_db_path, "--start", "k3", "--end", "k5", "--quiet"])
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["k3\t3", "k4\t4", "k5\t5"]


def test_stats(tmp_db_path, capsys):
    for i in range(3):
        main(["put", tmp_db_path, f"k{i}", str(i)])
    capsys.readouterr()
    assert main(["stats", tmp_db_path]) == 0
    out = capsys.readouterr().out
    assert "num_keys: 3" in out
    assert "height:" in out
    assert "page_count:" in out


def test_repl_put_get_quit(tmp_db_path, monkeypatch, capsys):
    inputs = iter(["put greeting hello world", "get greeting", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main(["repl", tmp_db_path]) == 0
    out = capsys.readouterr().out
    assert "OK" in out
    assert "hello world" in out


def test_repl_delete_and_stats(tmp_db_path, monkeypatch, capsys):
    inputs = iter(["put a 1", "delete a", "get a", "stats", "quit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))
    assert main(["repl", tmp_db_path]) == 0
    out = capsys.readouterr().out
    assert "(not found)" in out
    assert "num_keys: 0" in out
