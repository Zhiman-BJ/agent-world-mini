"""Compatibility entry point for the verifier maintained in eval.verifier."""

import sys

from eval import verifier


if __name__ == "__main__":
    verifier.main()
else:
    sys.modules[__name__] = verifier
