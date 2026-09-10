# 半导体 Python 包发布版采集记录

核查日期：2026-09-10。四个仓库均通过官方 GitHub `releases/latest` 选择已发布标签，使用
`git clone --depth 1 --branch <tag> <repository>.git seed_pypi_raw/<directory>` 克隆。
采集阶段的全量来源参考索引现保存在 `pypi_outputs/ori_all/`；后续精选结果保存在
`pypi_outputs/` 根目录。未安装依赖或执行器件仿真。精选规则、统计和验证见
[pypi_selection_profiles/semiconductor_selection.md](pypi_selection_profiles/semiconductor_selection.md)。

## 官方来源与版本

| 包 | 官方代码库 | 官方文档 | 克隆标签 | 发布日期 UTC | 源码目录 |
| --- | --- | --- | --- | --- | --- |
| DEVSIM | [devsim/devsim](https://github.com/devsim/devsim) | [DEVSIM Manual](https://devsim.net/) | v2.11.0.rc5 | 2026-08-15 | `seed_pypi_raw/devsim/` |
| nextnanopy | [nextnano-GmbH/nextnanopy](https://github.com/nextnano-GmbH/nextnanopy) | [nextnanopy](https://www.nextnano.com/docu/nextnanopy/) | v1.3.2 | 2026-09-10 | `seed_pypi_raw/nextnanopy/` |
| Sesame | [usnistgov/sesame](https://github.com/usnistgov/sesame) | [Sesame](https://sesame.readthedocs.io/en/latest/) | 2.0.3 | 2019-04-25 | `seed_pypi_raw/sesame/` |
| Solcore | [qpv-research-group/solcore5](https://github.com/qpv-research-group/solcore5) | [Solcore](https://solcore5.readthedocs.io/en/latest/) | v5.10.1 | 2025-07-03 | `seed_pypi_raw/solcore/` |

上述四个文档首页均实际请求成功（HTTP 200）。官方 README、PyPI 项目元数据和 GitHub Release
用于交叉核对。固定配置与完整提交号在 [pypi_release_sources.json](pypi_release_sources.json)，
各产物的 `others.source_metadata` 也保存来源、发布标签、提交和版本差异。

- DEVSIM 最新 GitHub Release 的实际标签为 `v2.11.0.rc5`，`prerelease=false`。
  同标签 `CHANGES.md` 记录 `Version 2.11.0`，PyPI 最新版本也是 `2.11.0`。
  Git 输出的 `refs/tags/v2.11.0.rc5 ... is not a commit!` 是标签对象提示；解引用后的提交与 HEAD
  均为 `43b41ca845184c47e22b72d144db7e7db8509377`，克隆成功。
- nextnanopy 的最新 Release 为 `v1.3.2`，同标签 `pyproject.toml` 也是 `1.3.2`；
  核查时 PyPI 元数据仍为 `1.3.1`，因此采用较新的已发布 GitHub 版本。
- Sesame 为半导体漂移扩散求解器。PyPI 同名 `sesame 0.3.3` 是 `mafrosis/sesame` 加密工具，
  不属于本任务；未确认对应 PyPI 分发，`others.source_metadata.pypi` 和 `pypi_version` 留空，
  `basic_info.url` 仅放官方仓库与文档。`source=pypi` 沿用指定的 Python 包种子格式。
  Release 为 `2.0.3`，标签内 `_version.py` 仍为 `2.0`，记录实际发布标签。
- Solcore 最新 Release 与 PyPI 均为 `5.10.1`。README 还链接到旧
  `docs.solcore.solar/en/master/`（页面显示 5.7.7）；本次使用显示 5.10.1 的官方 Read the Docs 入口。

## 产物与统计

| 全量 JSON（位于 `pypi_outputs/ori_all/`） | class | function | class_func | all_func |
| --- | ---: | ---: | ---: | ---: |
| [devsim_v2.11.0.rc5.json](pypi_outputs/ori_all/devsim_v2.11.0.rc5.json) | 6 | 167 | 31 | 198 |
| [nextnanopy_v1.3.2.json](pypi_outputs/ori_all/nextnanopy_v1.3.2.json) | 46 | 93 | 307 | 400 |
| [sesame_2.0.3.json](pypi_outputs/ori_all/sesame_2.0.3.json) | 20 | 36 | 104 | 140 |
| [solcore_v5.10.1.json](pypi_outputs/ori_all/solcore_v5.10.1.json) | 63 | 310 | 237 | 547 |

统计写入 `environment.nums`；`all_func = function + class_func`，不额外加类数。
各包为单元素 JSON 数组，索引依次为 3、4、5、6，避免复用原示例的 1、2。
顶层包描述为基于官方资料的中文介绍，未知领域子分类为 null，`init_ref_tasks` 为空数组。

## 解析范围与重现

[extract_release_python_seeds.py](scripts/extract_release_python_seeds.py) 读取固定清单，检查源码 HEAD
与发布标签、提交号一致，然后调用已有 AST 解析器。纳入顶层公开类、顶层公开函数、`__init__`
和源码直接定义的公开类方法（包含 property）；不展开继承、导入别名和嵌套定义。
包内 GUI 等辅助模块在全量范围中，包目录外的测试、示例和构建脚本不计入。

DEVSIM 的 `devsim.python_packages` 和 `devsim.umfpack` 按官方打包布局映射模块名；另从
`src/pythonapi/CommandTable.cc` 的导出表与 `DevsimDoc.cc` 的内嵌文档补充 **106 个原生 Python API**，
不把 C++ 内部类算作 Python 类。每个文件的相对路径与 SHA-256 记录在产物元数据中。

解析器补充了 Solcore 使用的 reStructuredText `:param:` / `:type:` / `:return:` / `:rtype:`
格式，同时保留 Google、NumPy 格式支持。签名来自 AST（DEVSIM 原生 API 使用内嵌文档签名）；
缺少的说明、类型保留空字符串，缺少返回段为 null。保留完整 `ori_description`，不凭函数名生成说明。
只记录有直接来源的字段；静态索引不保证运行时 API 完整性或可执行性。

从仓库根目录重新生成全量索引（默认只写 `ori_all/`，不覆盖根目录精选结果）：

```powershell
python -W ignore::SyntaxWarning -m seed_gen.scripts.extract_release_python_seeds
```

只重新提取并与现有产物比较：

```powershell
python -W ignore::SyntaxWarning -m seed_gen.scripts.extract_release_python_seeds --check
python -m unittest tests.test_extract_python_ref_tools tests.test_select_python_ref_tools
```

源码含旧式字符串转义，Python AST 会报告 `SyntaxWarning: invalid escape sequence`；上述命令只屏蔽
这类警告，不修改上游源码。未调用包内代码。DEVSIM 构建用第三方子模块未初始化，其状态已记入元数据。
若后续要从源码编译，仍需获取构建依赖并另行验证。

## 本次验证结果与边界

- 四个仓库 HEAD 与标签匹配，工作区均干净。
- 全部产物与源码重新提取的结果逐项相等；类/函数及类内方法重复项均为 0。
- 实际核对 `devsim.create_1d_mesh` 的内嵌参数说明和 `Solcore.SolarCell.__init__` 的 reST 参数说明。
- 8 项相关单元测试通过；解析脚本编译检查通过。
- 使用 `schemas/validation/env_seeds.schema.json` 校验，所有错误仅为用户要求保留的空说明违反
  `minLength: 1`。其余字段结构均符合 Schema。没有修改通用 Schema，也没有虚构缺失说明。

| 包 | 顶层空说明数 | 类方法空说明数 | 严格 Schema 错误数 |
| --- | ---: | ---: | ---: |
| DEVSIM | 37 | 31 | 68 |
| nextnanopy | 107 | 292 | 399 |
| Sesame | 36 | 70 | 106 |
| Solcore | 138 | 117 | 255 |

这些是允许信息缺失的原始参考种子，尚不能作为通过严格发布校验的成品环境。
后续可筛选有文档的 API 或单独补充有来源的说明，再进入环境生成和真实执行验证。
本次未验证数值求解、运行环境兼容性、nextnano 许可或外部程序可用性。

工作区已有的 `python包种子示例.json`、`pymatgen-core_v2026.8.30.json`、`atomate2_v0.1.5.json`
在本次开始前已有修改；全局 `git diff --check` 报告这些文件新增 PyPI 链接行的行尾空白。
本次未改动这些文件，新增及修改的任务文件单独检查。
