
### pymatgen-core

[pypi_outputs/pymatgen-core_v2026.8.30.json](D:/Desktop/agent-world-mini/seed_gen/pypi_outputs/pymatgen-core_v2026.8.30.json)

整理范围：

- 源码目录：`seed_pypi_raw/pymatgen-core/src/pymatgen`
- 版本：`pymatgen-core v2026.8.30`
- Python 源文件：204 个
- 包含定义的模块：175 个
- 顶层类：652 个
- 顶层函数：309 个
- 顶层 `init_ref_tools`：961 条
- 类方法：3,458 条
- 模块级 `@overload` 声明已与实现去重

提取规则与结果元数据已写入 JSON 的：

```json
others.python_source_extraction
```

当前规则是：

- 纳入公开的模块级类和函数
- 纳入 `__init__` 和公开类方法
- 排除私有定义、私有方法、非构造魔术方法
- 排除导入函数和继承而未在当前源码定义的函数
- 函数签名来自 AST
- 函数说明来自源码 docstring
- 不执行 `pymatgen` 代码

验证已通过：

```text
JSON 解析：通过
源码重新提取并逐项比对：通过
重复模块/名称检查：通过
提取脚本 compileall：通过
git diff --check：通过
```

可重复执行的脚本为：

[extract_python_ref_tools.py](D:/Desktop/agent-world-mini/seed_gen/scripts/extract_python_ref_tools.py)


### atomate2

已完成。

仓库：

[seed_pypi_raw/atomate2](D:/Desktop/agent-world-mini/seed_pypi_raw/atomate2)

- 标签：`v0.1.5`
- 提交：`0b61cf6365c8cfb5e74792f48d20b37dbb2916a6`
- 克隆方式：浅克隆
- 当前 Git 工作区干净

整理结果：

[seed_gen/pypi_outputs/atomate2_v0.1.5.json](D:/Desktop/agent-world-mini/seed_gen/pypi_outputs/atomate2_v0.1.5.json)

统计：

- Python 源文件：272 个
- 包含定义的模块：214 个
- 顶层类：474 个
- 顶层函数：260 个
- `init_ref_tools`：734 条
- 类方法：452 条

输出已使用：

```json
"global_id": "pypi_atomate2_1",
"environment": {
  "basic_info": {
    "source": "pypi",
    "name": "atomate2",
    "version": "v0.1.5"
  }
}
```

提取规则与源码文件清单写在：

```text
others.python_source_extraction
```

验证已通过：

```text
源码 AST 重新提取比对：通过
重复模块/函数名检查：通过
JSON 解析：通过
提取脚本编译：通过
git diff --check：通过
```
