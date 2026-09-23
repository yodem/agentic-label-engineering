from ale.cli import _parser


def test_board_cli_parser_supports_run_selection_and_open():
    args = _parser().parse_args(["board", "--run-dir", "/tmp/run", "--open"])
    assert args.cmd == "board"
    assert args.run_dir == "/tmp/run"
    assert args.open is True
