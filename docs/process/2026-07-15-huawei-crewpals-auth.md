# Huawei CrewPals 本地认证

- **日期**: 2026-07-15
- **类型**: feature

## 背景与动机

每个用户已有独立的 CrewPals token，可由服务端代理获取 Huawei access token。Rundown 因此不需要保存 Huawei 开发者凭证，也不需要浏览器回调。

## 方案选择

Huawei Provider 只读取 `GROUP_PALS_TOKEN`，按 CrewPals 预发布 HTTPS 契约将原始 JWT 放入 `Authorization` header。返回的 Huawei AT 缓存在每用户独立目录；未过期时本地复用，过期后重新获取。

## 实现步骤

1. 将 Huawei 配置收敛为 `GROUP_PALS_TOKEN` 和 `HUAWEI_TOKEN_DIR`。
2. 移除 client credentials、loopback callback、授权码交换和浏览器流程。
3. 校验 CrewPals 返回的 `access_token` 与绝对过期时间后，以 `0600` 缓存。
4. 初始化和认证阶段准备 `0700` token 目录，并兼容外部 token 字段结构。
5. 更新 CLI、MCP、测试和用户/设计文档。
6. 默认以 `GROUP_PALS_TOKEN` 的 SHA-256 摘要派生本地用户目录，避免 token 缓存互相覆盖。
7. 接入 Huawei `/activityRecords` 列表和 `activityRecordId` 详情查询，并复用非 Garmin 标准入库流程。
8. 兼容预发布接口的 `data` wrapper 和 camelCase token 字段，写盘前统一为 snake_case。

## 遇到的问题与解决

最初的生产路径尚未发布；联调使用 `https://api.crewpals.com/api/v1/huawei/access_token`，且服务要求 Authorization header 不带 Bearer scheme。

## 关联文档

- CHANGELOG: [docs/CHANGELOG.md](../CHANGELOG.md)
- Design: [docs/design/12-multi-platform.md](../design/12-multi-platform.md)
