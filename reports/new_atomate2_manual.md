# atomate2 环境人工复核（2026-09-13）

复核根目录：`runs/atomate2_sol_full/20260913_183040_866328_pypi_atomate2_3_gpt-5.6-sol`。同时检查 `qwen_trial_retry` 的 result/calls/state，以及参考链 `tasks.json`、execution.tool_calls、final 文件；重新读取 SQLite、输入文件、归档 ZIP/manifest 并重算哈希。按任务文本判定，环境或模型错误一律 FAIL，已恢复的单次工具错误不单独扣分。

## 结论

| task | Qwen | 参考链 | 关键依据 |
|---|---|---|---|
| 1 | FAIL | PASS | Qwen 无回答（LLM 无文本）；参考生成 66 行完整记录清单，覆盖族/电荷态。 |
| 4 | FAIL | PASS | Qwen context 超限；参考给出 Ga_i1/Ga_i2 电荷态、稳定区间、跃迁和 Ga_i1 中性条目。 |
| 6 | FAIL | PASS | Qwen context 超限；参考归档含5个源 JSON 和审查 CSV，manifest 逐项列出6个文件路径、大小和 SHA-256，并经独立重算一致。 |
| 11 | PASS | PASS | 两链均验证19节点/40引用、目录与单文件 ZIP；引用清单不是该任务要求的逐引用交付。 |
| 12 | PASS | PASS | 输入实际修改值正确，AMSET/VASP/HDF5/工作流/GaN复核和 GaN 归档均可验证；未宣称新计算执行。 |
| 21 | FAIL | PASS | Qwen仅交付引用数量和部分示例，未交付完整40项依赖清单；参考完整列出。 |
| 23 | FAIL | PASS | Qwen context 超限；参考 9×9×9 KPOINTS、gzip 原文等价、两份 Ga_i1 导出实际存在。 |

## Qwen 实际结果

task1、4、6、23 的 `qwen_trial_retry/task*/result.json` 分别是无文本或 131072 context 超限；按端到端标准 FAIL。task11/12/21 有回答，task11 的 `gaas_flow_delivery.zip` 只含 `gaas_flow.json`，且 manifest 逐项 SHA-256/大小与 ZIP 和原文件一致；task12 的 settings.yaml、KPOINTS、INCAR 现场读取均为目标值，`validate_amset_input_bundle` 和 `validate_hdf5_signatures` 返回 valid；GaN handoff ZIP 5 个源文件和 manifest 也逐项一致。task21回答3089字符，只给层级摘要、引用数量和部分示例，没有任务要求的完整输出依赖清单，判FAIL。此前“有40项清单”的断言错误，已依据实际answer更正；工具取得全量信息不等于最终交付了全量清单。

## 参考 task1/4

参考 task1 的 answer 与工具调用均成功；`tasks/task1/final/.../projects/gan_defects/gan_defect_record_manifest.csv` 实际 66 行，字段含 state_id、defect_id、charge、formation_energy、两类 correction 和 is_shallow，覆盖数据库全部 66 电荷态。GaN 原始包 5 个 JSON，原始详细计算仅 bulk 与 Ga_i1 q=0；回答明确其余 65 态无独立 CalcResults，且报告 Extended FNV 远场残差均值约 -0.1069 eV、汇总 alignment=0，正确标出需复核而非虚称通过。PASS。

参考 task4 的形成能线性下包络重新核对：Ga_i1、Ga_i2 在 EF=0 和 1.8406 eV 均为 q=-1；两族均 q=[-1,0,+1,+2,+3]，中性结构条目与计算字段存在。回答明确其余电荷态无详细超胞计算，不推断未计算内容。PASS。

## 参考 task6：PASS

参考 answer 的治理/科学结论多数有证据：12 族/66 态、Ga_i1 q=0 结构、bulk/defect 收敛、能量差 4.55000795 eV、远场 123 点和归档 ZIP 均真实。`tasks/task6/final/.../projects/gan_defects_review.zip` 成员 6 个，包含 5 个源 JSON 及 `review/charge_state_audit.csv`；ZIP、manifest、成员 hash/大小与源文件独立核对一致。

task_text 要求的完整性清单是归档文件级校验清单；manifest 已列 archive_path/hash、project_path 以及6个成员的路径、大小、SHA-256，ZIP 和每个成员均独立重算一致。CSV 66 行存在且与数据库电荷态集合一致。回答对其余65态缺少独立原始计算证据的限制声明准确，不能因 manifest 未重复业务统计字段而扩大要求。PASS。

## 参考 task11 与 task21

task11 的参考链归档 `archives/gaas_amset_flow_verified.zip` 仅含完整案例文件 `gaas_flow.json`；manifest 的 file_count=1，哈希与源文件一致。任务要求“完整目录归档”，案例目录真实仅该文件，因此 PASS。参考和 Qwen 都报告专用 reconciliation 发现 19 节点/40 OutputReference；一次 hierarchy 路径失败后用正确路径恢复，不扣分。

task21 的参考 answer 显式列出 `gaas_ref_001` 至 `gaas_ref_040`，每项含 owner、target、属性链和 JSON 定位，并给出全部节点层级、19 UUID、40/40 reconciliation。它满足“完整的节点层级结构和输出依赖清单”。检查器概览 `output_reference_count=0` 与深度枚举40的口径差异被正确标明，PASS。

## 参考 task12：PASS

实际 final 输入：`projects/si_amset/settings.yaml` 的 temperatures `[300, 600]`、doping 为两个目标数量、interpolation_factor=10；GaAs `KPOINTS` Gamma 6×6×6 零偏移，INCAR 的 ENCUT=500、EDIFF=1e-5、ISMEAR=0、SIGMA=0.05。AMSET bundle、HDF5、VASP 输入和 workflow 结构均返回 valid；GaN 5 个源 JSON reconciliation 差异0。hand-off ZIP 成员正好为 5 个原始 GaN JSON，manifest/hash/大小重算一致。参考明确新输入未执行，符合边界要求。Qwen 修改后的 YAML 使用科学记号字符串/数值，但工具解析与验证通过，不能据此失败。

## 参考 task23：PASS

实际 final 中 KPOINTS 为 Monkhorst-Pack `9 9 9`、`0 0 0`；原 `vasprun.xml` 保留，gzip 解压字节与原文件完全相同。`delivery/Ga_i1_charge_0_entry.json` 和 `..._correction.json` 均存在，分别 271266/127560 bytes，JSON 可读且内容对应 defect_entry/defect_correction 查询；校正含 300 sites、177 内/123 外、point_charge=0、区域外 alignment 均值约 -0.1069。回答正确声明静态检查不能证明新网格收敛或科学合理性。PASS。

## 总计

Qwen：2 PASS（11、12）/5 FAIL（1、4、6、21、23）。参考链：**7 PASS、0 FAIL**。
