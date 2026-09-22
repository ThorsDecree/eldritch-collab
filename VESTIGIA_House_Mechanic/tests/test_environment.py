from house_mechanic.environment import filtered_environment


def test_filtered_environment_matches_allowed_names_case_insensitively() -> None:
    source = {
        "SYSTEMROOT": r"C:\Windows",
        "SystemDrive": "C:",
        "Path": r"C:\Python",
        "TEMP": r"C:\Temp",
        "PYTHONUTF8": "1",
        "SECRET_THAT_MUST_NOT_LEAK": "nope",
    }

    minimal = filtered_environment("minimal", source)
    assert minimal["SYSTEMROOT"] == r"C:\Windows"
    assert minimal["SystemDrive"] == "C:"
    assert minimal["Path"] == r"C:\Python"
    assert "PYTHONUTF8" not in minimal
    assert "SECRET_THAT_MUST_NOT_LEAK" not in minimal

    python = filtered_environment("python", source)
    assert python["PYTHONUTF8"] == "1"
    assert "SECRET_THAT_MUST_NOT_LEAK" not in python
