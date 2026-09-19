# 03.11.01 / 03.11.04 模拟设计与商业桥接种子

2026-09-18整理：本组输出已合入 [正式产物](../../pypi_outputs/final_results/README.md)，L3统一位于`final_results/l3/`；重复worker输出已移出产物目录，恢复位置见正式产物说明。下文旧输出路径和命令保留为重建记录，重建的worker目录不上传。

本lane独立承接两个场景，不修改已冻结的07/08产物或设计组目录。两种子共122项参考操作，4条完整固定任务，每条各重复两次一致。源码重解析及生成实际输出`Generated 2 scenario-first seeds.`；最后`--check`实际输出`Verified 2 scenario-first seeds.`，退出码0。本lane冻结待主分支提升。

| 场景 | 包组合 | class | function | class_func | all_func |
| --- | --- | ---: | ---: | ---: | ---: |
| 03.11.01 Code-first Analog Design | HDL21 + KLayout + VLSIRtools | 22 | 4 | 71 | 75 |
| 03.11.04 Commercial-flow Generator | skillbridge | 13 | 5 | 42 | 47 |

75/47为源码参考操作数，包含构造/属性；不等于最终Agent动作。商业桥接通过一个RemoteFunction承载动态SKILL名字，不把任意字符串展开成源函数凑50。最终产物在`seed_gen/pypi_outputs/scenario_collection/analog_bridge/`，全量候选在`seed_gen/pypi_outputs/ori_all_funcs/scenario_collection/analog_bridge/`，合计4包5414操作。profiles/research/runtime在本目录。

## 来源与取舍

- skillbridge官方release `releases/1.8.0`，2026-03-23，commit `ca6105eec77a587db39d61c6e26f60be46065e48`，PyPI1.8.0一致。源码`seed_pypi_raw/analog_bridge/skillbridge/`。tag含斜杠，候选原始文件实际位于`skillbridge_releases/1.8.0.json`，不擅改发布标签。
- HDL21 v6.0.0 commit `3f5f3b325ca7eefaad51302a4faec2b2d19b4033`、VLSIRtools v6.0.0 commit `9452f2c050b659ea4b3b14c7ae0822582bf76a8f`、KLayout v0.30.12 commit `e71272c3b178105bd2a2f25af54673a7af7ed60d`只读复用设计组固定源码，独立重解析。前两包PyPI7.0.0和稳定Git标签不一致，沿用可核验6.0.0发布源码与兼容vlsir6.0.0；差异保留在manifest。
- `fredrief/pade`、`bluecheetah/bag`、`ucb-art/BAG_framework`的官方releases/tags各返回空列表。`research/unavailable_candidates.json`记录响应文件和哈希，不将main快照当发布版，也不伪造不存在的全量候选。PyPI pade是多Agent开发框架，与模拟IC PADE无关。
- 03.11.01保留PADE所启发的原理图/版图/求解共同状态，但只以制造电阻子链验证。03.11.04用skillbridge覆盖Python商业调用桥接层，不宣称完整替代BAG模拟设计生成器。
- 每场景4–5个实际读取来源，包括官方例子、固定发布源码和应用说明。403/挑战页不作为证据；KLayout提取文档采用官方v0.30.12内嵌源码，明确矩形电阻`R=L/W*sheet_rho`以及R/C层和tA/tB端子。

## 运行环境和依赖锁

可跟踪的Python依赖锁为本目录[requirements-python311.txt](requirements-python311.txt)，实际Windows CPython3.11.16，15包经`uv pip check`兼容。

ngspice为Ubuntu noble amd64已发布分发`42+ds-3build1`，只解包到`/home/zjs32/.local/share/semiconductor-analog-bridge-20260917/ngspice-deb/root/`；没有全局安装或更改其他lane。外部二进制不宣称最新上游版本，版本输出在运行报告。所需deb精确版本见[requirements-ngspice-debs.txt](requirements-ngspice-debs.txt)，文件摘要见[ngspice-debs.sha256](ngspice-debs.sha256)。依赖还使用现有WSL Ubuntu基础libc/libstdc++/libedit/libfontconfig等系统库，因此这些deb列表不是跨发行版通用镜像。

```powershell
C:/Apps/anaconda3/Scripts/uv.exe venv --python 3.11 .venv-scenario-analog-bridge
C:/Apps/anaconda3/Scripts/uv.exe pip install --python .venv-scenario-analog-bridge/Scripts/python.exe -r seed_gen/scenario_collection/analog_bridge/requirements-python311.txt
C:/Apps/anaconda3/Scripts/uv.exe pip check --python .venv-scenario-analog-bridge/Scripts/python.exe
```

WSL Ubuntu noble中从本仓库根目录执行下列下载/解包，无sudo：

```sh
mkdir -p /home/zjs32/.local/share/semiconductor-analog-bridge-20260917/ngspice-deb
cd /home/zjs32/.local/share/semiconductor-analog-bridge-20260917/ngspice-deb
tr -d '\r' < /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/analog_bridge/requirements-ngspice-debs.txt | while IFS= read -r bridge_spec; do
  case "$bridge_spec" in ''|'#'*) continue ;; esac
  apt-get download "$bridge_spec"
done
tr -d '\r' < /mnt/d/Desktop/agent-world-mini/seed_gen/scenario_collection/analog_bridge/ngspice-debs.sha256 | sha256sum -c -
for bridge_deb in ./*.deb; do dpkg-deb -x "$bridge_deb" root; done
env LD_LIBRARY_PATH=/home/zjs32/.local/share/semiconductor-analog-bridge-20260917/ngspice-deb/root/usr/lib/x86_64-linux-gnu ./root/usr/bin/ngspice -v
```

## 可复现任务与生成检查

```powershell
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.collect_scenario_web_evidence --urls seed_gen/scenario_collection/analog_bridge/research/analog_bridge_urls.json --output seed_gen/scenario_collection/analog_bridge/research/analog_bridge_web
C:/Apps/anaconda3/python.exe -u -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.prepare_analog_bridge_sources
.venv-scenario-analog-bridge/Scripts/python.exe -u -W ignore -X utf8 seed_gen/scenario_collection/analog_bridge/verify_code_analog.py
.venv-scenario-analog-bridge/Scripts/python.exe -u -X utf8 seed_gen/scenario_collection/analog_bridge/verify_commercial_bridge.py
C:/Apps/anaconda3/python.exe -X utf8 -m seed_gen.scripts.prepare_analog_bridge_profiles
C:/Apps/anaconda3/python.exe -u -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/analog_bridge/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/analog_bridge --merged-name semiconductor_scenario_analog_bridge.json --group-by-l1 --verify-sources
C:/Apps/anaconda3/python.exe -u -W ignore::SyntaxWarning -X utf8 -m seed_gen.scripts.build_joint_scenario_seeds --profiles seed_gen/scenario_collection/analog_bridge/profiles --output-dir seed_gen/pypi_outputs/scenario_collection/analog_bridge --merged-name semiconductor_scenario_analog_bridge.json --group-by-l1 --check
```

03.11.01：固定制造sheet100Ω/square、W1µm、L10/20µm，代码生成GDS后以新Layout读回，实际DeviceExtractorResistor得到1k/2k及L/W/A/P，实际网络为VDD–OUT–VSS共三网。提取值/端子转HDL21原语，与独立固定参考电路分别运行ngspice，均得Vout=.6666666666666666V、供电电流=-.0003333333333333334A。错误L20→15µm实际提取1.5k，仿真Vout=.6V、I=-.0004A；恢复L20后重新生成/读取/提取/仿真回到原目标，两个独立重复完全一致。数值oracle来自独立分压/串联电流公式，容差1e-12。

03.11.04：固定SKILL字面量和顺序作为独立oracle，真实skillbridge Workspace/RemoteFunction/DefaultTranslator把open、rodCreatePath的cvId/layer/width/pts及check/save编码解码。错误宽度.8对目标.08不符，改参数重编码恢复；另一个任务把r模式句柄收到的录制error映射为真实ParseError，错误后没有save，修复为a模式新句柄后严格check→save。所有返回值来自明确手写fixture，报告固定`domain_execution=not_executed`。没有连接Cadence，没有执行任何SKILL设计数据库或几何操作；返回True不能用作版图/原理图成功证明。

实际失败与修复：首次ngspice启动缺`libXaw.so.7`，ldd定位libXt/libXft/libgomp等后只下载解包其依赖；未修改全局。首次Op未命名时结果实际名`Analysis0`，用`result['op']`触发KeyError，修复为`Op(name='op')`并以正式结果get查询。profile初稿把Python关键字from写成dict参数导致SyntaxError，改为source/target键后生成，未修改数据契约。

未验证：真实OTA、晶体管PDK、寄生/噪声/温度、完整DRC/LVS/签核，Cadence商业执行；全部参考API运行覆盖；Agent存储、权限、reset/step和跨任务隔离。场景profile明确保留这些边界。
