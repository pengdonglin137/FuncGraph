# Pinned native dependencies

`versions.json` records the exact commits already selected by the Git submodule
gitlinks. Normal builds never follow branches or use `git submodule update --remote`.

| Repository | Locked commit |
| --- | --- |
| libtraceevent | `13701b5532e0c3295bf5670361692b0d0044228d` |
| libtracefs | `6fad6a14ba0d4c4b437d9e4eed7098d4bb07b4fc` |
| trace-cmd | `3ce20923b3efa60d417da7acc8a327615fbd1419` |
| uftrace | `842ed731248a9daded38774d8fb26c7b09474461` (upstream v0.20) |

```bash
git submodule update --init --recursive
python3 third_party/check_pins.py
```

The checker compares **all gitlinks against the lock**, then checks selected
initialized repositories' HEAD and tracked-source cleanliness. CI and both native
build scripts run it. `--allow-uninitialized` checks gitlinks in Python-only jobs;
it never permits a wrong initialized checkout. `--only uftrace` and
`--only libtraceevent libtracefs trace-cmd` select native build prerequisites.

To upgrade a dependency intentionally, choose an explicit reviewed commit,
check it out in the submodule, stage its gitlink, and update `versions.json` in
the **same commit**. Rebase/regenerate the uftrace patch series when changing
its base, run `scripts/uftrace.sh check`, rebuild clean native prefixes, run the
native and installed-package tests, and document the upgrade. Do not move the
lock to an upstream branch tip automatically.

These pins freeze the current dependency versions; no dependency is upgraded.
The lock does not select the system libraries installed by the operating system.
The uftrace integration applies the maintained patch series on top of its locked
base without modifying the original submodule checkout. See
[maintained patches](../patches/uftrace/README.md).

## 中文说明

上表的完整 commit SHA 与 Git 子模块的 gitlink 一一对应，保持当前正在使用的
版本不变，不升级任何依赖。正常初始化使用 `git submodule update --init --recursive`，
不要使用 `--remote`，也不要改为跟踪分支或可移动的标签。

执行 `python3 third_party/check_pins.py` 可检查全部依赖；构建脚本与 CI 也会执行
校验。锁文件与任意 gitlink 不一致、所需子模块 HEAD 不符或已跟踪源码被修改，
都会立即失败。允许未跟踪的编译产物；Python-only CI 可使用
`--allow-uninitialized` 跳过未初始化的子模块，但不会放过已初始化仓库的版本漂移。

有意升级时，选择明确的完整 SHA，同时修改子模块 gitlink 和 `versions.json`，
在同一个提交中记录升级原因并完成对应构建和测试。升级 uftrace 时还需重新核对、
生成并验证补丁序列。锁定的是仓库依赖版本，不是系统包管理器安装的库版本。
