"""Program-form TaskGen 的 Step 1 到 Step 12。"""

from .step1_prepare_environment import run_step1
from .step2_gen_task_solution import run_step2
from .step3_debug_solution_jsonl import run_step3
from .step4_ground_truth_jsonl_in_jsonl_out import run_step4
from .step5_verifier_code_jsonl_in_jsonl_out import run_step5
from .step6_debug_verifier_jsonl_in_jsonl_out import run_step6
from .step7_consistency_jsonl_in_jsonl_out import run_step7
from .step8_filter_rewrite_jsonl_in_jsonl_out import run_step8
from .step9_filter_trace_state_jsonl_in_jsonl_out import run_step9
from .step10_gen_task_rubric_jsonl_in_jsonl_out import run_step10
from .step11_difficulty_eval_jsonl_in_jsonl_out import run_step11
from .step12_final_output import run_step12

__all__ = [f"run_step{number}" for number in range(1, 13)]
