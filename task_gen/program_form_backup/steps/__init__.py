"""Program-form TaskGen 的五个显式步骤。"""

from .step1_prepare_environment import run_step1
from .step2_research_real_world_tasks import run_step2
from .step3_generate_task_solution import run_step3
from .step4_generate_scoring_criteria import run_step4
from .step5_evaluate_difficulty import run_step5

__all__ = [f"run_step{number}" for number in range(1, 6)]
