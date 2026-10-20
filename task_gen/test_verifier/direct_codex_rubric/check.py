"""Direct design acceptance, including recursive necessity-field checks."""
from ..codex_rubric_verifier.check import main as check_main
from .contracts import validate_package


def main():
    check_main(package_validator=validate_package)


if __name__ == '__main__':
    main()
