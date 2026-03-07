'''
Link Lean sideick web apps to LSP-enabled editors
'''


def main(cmd_line_args: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    args = parser.parse_args()
    return 1
