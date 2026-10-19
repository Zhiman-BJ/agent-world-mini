"""Generate this method's own criteria; never load another method's package."""
from ..codex_rubric_verifier.generation import generate_for


def generate(evidence_dir, output_dir, config=None, *, model_call=None):
    return generate_for('direct_codex_rubric', evidence_dir, output_dir, config, model_call=model_call)
