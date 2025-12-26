from __future__ import annotations

from src import paths
from src.parsing.config import ParsingConfig, load_parsing_config


def test_load_parsing_config_defaults(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    config = load_parsing_config(None)

    assert isinstance(config, ParsingConfig)
    
    # Expected root depends on CWD since SPECULUM_DATA_DIR is not set
    # paths.get_evidence_root() returns Path(".") / "evidence" = "evidence" (relative)
    # Then it gets resolved relative to CWD (tmp_path)
    expected_root = (tmp_path / "evidence" / "parsed").resolve()
    assert config.output_root == expected_root
    
    assert config.scan.suffixes
    assert config.scan.recursive is True
    assert config.scan.include == ()
    assert config.scan.exclude == ()


def test_load_parsing_config_from_file(tmp_path) -> None:
    config_dir = tmp_path / "conf"
    config_dir.mkdir()
    yaml_path = config_dir / "parsing.yaml"
    yaml_path.write_text(
        """
output_root: artifacts
scan:
  suffixes: [.txt, txt]
  recursive: false
  include:
    - "**/*.txt"
  exclude:
    - "skip/**"
        """.strip()
        + "\n",
        encoding="utf-8",
    )

    config = load_parsing_config(yaml_path)

    assert config.output_root == (config_dir / "artifacts").resolve()
    assert config.scan.suffixes == (".txt",)
    assert config.scan.recursive is False
    assert config.scan.include == ("**/*.txt",)
    assert config.scan.exclude == ("skip/**",)


def test_load_parsing_config_uses_default_location(monkeypatch, tmp_path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    yaml_path = config_dir / "parsing.yaml"
    yaml_path.write_text("output_root: alt\n", encoding="utf-8")

    monkeypatch.chdir(tmp_path)

    config = load_parsing_config(None)

    assert config.output_root == (config_dir / "alt").resolve()