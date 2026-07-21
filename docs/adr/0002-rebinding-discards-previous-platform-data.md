---
status: superseded by ADR-0010
---

# Rebinding discards data imported from the previous platform

第一版将 Platform Rebinding 定义为替换唯一活跃连接并清除旧 Platform Connection 的凭证及 Platform-Derived Data，而不是保留并合并两个平台的数据；neurun Account、昵称、运动者主动维护的档案、目标和偏好必须保留。换绑请求必须重新验证当前 neurun 密码并显式确认删除；系统随后完成新 Platform Account 认证，只有认证成功才清除旧数据并提交连接替换，认证、MFA 或平台接口失败不得改变旧连接和旧数据。这个选择牺牲换绑后的历史连续性，换取无需立即引入来源维度、跨平台活动唯一键和重复活动合并规则的较低实现成本；未来支持多连接时必须以新的数据模型显式取代本决策。
