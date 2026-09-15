# Python 包采集与筛选：Agent 操作指南

输入：包名列表、领域、版本要求；默认选择执行时最新的官方发布版本，每包精选 `all_func` 目标一般为 50–100，可酌情调整，也不用硬保留或硬删除。
目标：找到可信来源，保存发布版源码与全量 API 索引，再生成能力覆盖合理、适合后续构建可验证任务的精选索引。

所有命令从项目根目录执行。开始前检查工作区已有修改，复用已完成产物，不覆盖用户修改。

## 1. 查找官方来源并确定版本

1. 查询 PyPI 项目元数据：`https://pypi.org/pypi/{package}/json`，读取描述、版本及 `project_urls`。
2. 用官网、官方仓库 README 和文档互相核对包身份；注意同名包，不能只凭 PyPI 名称判断。
3. 请求文档首页，确认可访问且对应目标项目。
4. GitHub 仓库查询 `https://api.github.com/repos/{owner}/{repo}/releases/latest`，检查标签、发布日期及预发布标记，再与 PyPI 和标签内版本文件核对。
5. 用户指定版本优先；否则采用最新官方非预发布 Release。无 Release 时，核对官方标签、变更日志与分发版本再选择；无法确认则记录缺失，不将默认分支冒充发布版。

记录官方仓库、文档、PyPI 链接、实际标签、完整提交号、发布日期、核查日期及版本差异。
标签名称与包版本不一致时保留实际标签并解释；无法确认的链接留空。
参考来源清单：[pypi_release_sources.json](pypi_release_sources.json)。该清单是固定快照，每次新增或升级都需重新核查。

## 2. 克隆发布版源码

替换下面的占位值后执行：

```powershell
git clone --depth 1 --branch <tag> <official-repository>.git seed_pypi_raw/<package>
git -C seed_pypi_raw/<package> rev-parse HEAD
git -C seed_pypi_raw/<package> describe --tags --exact-match
git -C seed_pypi_raw/<package> status --short
```

已有目录先检查 remote、HEAD、标签及工作区状态；匹配则复用，不匹配时使用独立版本目录。
记录是否获取子模块。静态解析通常不需要安装包或编译依赖；不要将源码克隆成功写成运行验证通过。

## 3. 生成全量参考索引

按当前 [python包种子示例.json](python包种子示例.json) 组织单元素 JSON 数组，保存至：

```text
seed_gen/pypi_outputs/ori_all/{package}_{tag}.json
```

- 填写独立的 `global_id`、包名、标签、唯一 index、官方链接、基于官方资料的中文包描述和领域。
- 使用 [extract_python_ref_tools.py](scripts/extract_python_ref_tools.py) 静态解析包目录，正确设置 `--source-root` 与 `--module`；`src/` 布局的 source-root 应指向 `src/`。
- 纳入顶层公开类、公开函数、直接定义的公开类方法及 `__init__`；沿用当前口径计入 property。不重复展开导入别名、继承接口、嵌套定义，不纳入包目录外的测试与示例。
- 函数 description 取参数段之前的说明；input 取签名、注解和参数说明；output 取返回说明。支持 Google、NumPy、reStructuredText 格式，保留 `ori_input`、`ori_description`。
- 无说明或类型填空字符串，无返回段填 null，任务列表为空数组。不要根据函数名编造参数或返回含义。
- C/C++ 等扩展的 Python API 需检查导出表和官方内嵌文档，按真实 Python 模块名补充；不把底层实现类计入 Python 类。
- 在 `others` 保存源码路径、文件哈希、版本/提交、解析规则和未覆盖范围。

自动计算 `environment.nums`：`class` 为顶层类数，`function` 为顶层函数数，
`class_func` 为保留的类方法数，`all_func = function + class_func`，不另加类数。
使用示例作模板时，必须核对 index、global_id 和元数据，不能带入示例包的身份。

现有 [extract_release_python_seeds.py](scripts/extract_release_python_seeds.py) 可按来源清单生成批量全量索引；
目前假定 Python 包位于仓库根目录，并含 DEVSIM 专用适配。新增包先检查布局和语言绑定，必要时扩展适配再使用。

## 4. 按能力筛选

先确定包的核心能力及一条“构造输入 → 配置/变换 → 计算 → 查询/保存结果”的完整调用链，
再为各能力选择代表接口，最终使每包 `all_func` 达到约 50–100。

- 优先选择常用公开入口、高层类、必要构造方法及可组合的查询/计算方法。
- 同时裁剪类的方法，避免保留一个大类就带入全部方法；排除重复别名、内部辅助、调试和长尾接口。
- 优先支持本地固定样例验证。外部程序、许可、原生库和数值后端依赖必须注明，不能把“计划验证”写成“验证通过”。
- 数量不能替代完整性；不足时先检查遗漏的必要能力，仍不足则说明原因，不用无关函数凑数。
- 必要接口没有说明时，在该符号的 profile 中显式填写 `missing_description_reason`，保留原空字段。

创建 `seed_gen/pypi_selection_profiles/{package}_{tag}.json`，参考已有清单，填写：
包名/版本、用 `_canonical_sha256(raw_payload)` 得到的规范化来源哈希、能力范围、
`target_all_func: {"min": 50, "max": 100}`、各能力的证据/保留原因/验证方式，以及明确的类、函数和方法名单。
这是显式选择清单，不按全局分数直接截取前 N 项。源码变更后应复核清单，不能只更新哈希绕过检查。

运行 [select_python_ref_tools.py](scripts/select_python_ref_tools.py)：

```powershell
python -m seed_gen.scripts.select_python_ref_tools --input seed_gen/pypi_outputs/ori_all/<package>_<tag>.json --profile seed_gen/pypi_selection_profiles/<package>_<tag>.json --output seed_gen/pypi_outputs/<package>_<tag>.json --report seed_gen/pypi_outputs/selection_reports/<package>_<tag>.json
```

精选只裁剪原始类/函数/方法，保留来源字段；重新计算 nums，报告中记录选择、排除及缺失说明。
若全量旧文件位于输出根目录，先移动至 `ori_all/` 并核对移动前后哈希，再生成精选文件。

## 5. 验证与交付

完成以下检查并记录实际结果：源码标签/提交一致；JSON 可解析；身份正确；精选为全量的原样子集；
nums 与目标范围正确；重复项及选择/排除数量核对；按清单重算结果一致。
使用 [验证 Schema](../schemas/validation/env_seeds.schema.json) 检查结构；空说明若违反 `minLength: 1`，
单独报告，不填造内容或擅自放宽 Schema。解析器/筛选器有修改时运行相关测试。

当前来源清单内包的批量命令（不是任意包名发现命令）：

```powershell
python -W ignore::SyntaxWarning -m seed_gen.scripts.extract_release_python_seeds
python -W ignore::SyntaxWarning -m seed_gen.scripts.extract_release_python_seeds --check
python -m seed_gen.scripts.select_release_python_seeds
python -m seed_gen.scripts.select_release_python_seeds --check
python -m unittest tests.test_extract_python_ref_tools tests.test_select_python_ref_tools
```

最终交付源码目录、全量 JSON、精选 JSON、来源清单、筛选 profile 和报告。
向用户简要汇报官方链接、发布标签、精选四项计数、验证结果及未验证范围；
记录必要的路径、命令和错误。调用链设想标明“设计示例”，只有真实执行并断言通过才称为可验证任务。
