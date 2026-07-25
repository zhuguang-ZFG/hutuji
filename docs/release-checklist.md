# 发布检查清单

> 发布负责人使用。全部勾选且证据可复核后，才可把该版本写入出货 manifest。  
> 当前总状态见 `release-readiness.md`——本清单不能单独把项目标成“可出货”。

## A. 冻结

- [ ] hutuji commit：`________`（clean）
- [ ] Grbl_Esp32 分支/commit：`________`（含 §9 则附 `agent_gate.py` overall=pass 日志路径）
- [ ] xiaozhi-esp32 分支/commit：`________`
- [ ] 依赖锁：`mcp-server/requirements.txt`（及生产 pip freeze 归档路径）
- [ ] 固件二进制 SHA256（S3 / Grbl 若本批烧录）：`________`

## B. 自动化

- [ ] `mcp-server`：`pytest -q` 全绿（输出归档）
- [ ] `e2e_local.py` 输出 `PASS`
- [ ] （若改桥接）`cloud_bridge_smoke.py` PASS（接入点仅环境注入）

## C. 京东云

- [ ] 目标机 `117.72.118.95`；unit 与 nginx 与 `deploy/` 一致（或已记录 diff）
- [ ] `HUTUJI_HOST=127.0.0.1`；安全组未放行 8300
- [ ] TLS 有效；公网仅 `/files`；`/mcp` 404
- [ ] `HUTUJI_PUBLIC_BASE_URL` 与本批设备配置一致
- [ ] 回滚包/上一 commit 可恢复（演练记录路径）

## D. 硬件联调证据

- [ ] `bringup-sop` 阶段 0–8 记录齐全（或本批明确不适用项 + 批准人）
- [ ] 阶段 8 🔬 数字已回填 `protocol.md` 或登记“本批仍为推导、不升 v0.6”

## E. 生产与合规

- [ ] `production-sop` 首件通过
- [ ] 逐台 `device-test-record` 齐全
- [ ] `compliance.md` 签核项完成（GPL 源码义务、标签、隐私）
- [ ] 客户资料联系方式已非占位（或书面接受占位风险）

## F. 审批

| 角色 | 姓名 | 日期 | 签字 |
|------|------|------|------|
| 研发 | | | |
| QA | | | |
| 运维/发布 | | | |
| 生产（出货批） | | | |

回滚负责人：________  回滚演练日期：________
